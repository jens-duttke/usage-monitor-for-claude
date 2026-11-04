"""
Usage Cache
============

Thread-safe cache for API data - single source of truth for all usage
state.  All API refresh requests go through ``UsageCache.update()``,
which uses a lock to prevent concurrent calls and a cooldown to prevent
calls that are too close together (HTTP 429).
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from .api import fetch_prepaid_credits, fetch_profile, fetch_usage, read_access_token
from .claude_cli import RefreshResult, refresh_token
from .forecast import record_samples
from .settings import FORECAST_CHART, MAX_BACKOFF, POLL_FAST, POLL_INTERVAL

__all__ = ['CacheSnapshot', 'MANUAL_REFRESH_INTERVAL', 'RESET_BUFFER', 'UpdateResult', 'UsageCache', 'reset_offsets']

log = logging.getLogger(__name__)

# Seconds after a reset at which to place the confirming poll.  A small buffer
# absorbs minor timing differences (clocks, caches, server-side propagation).
RESET_BUFFER = 5

# Seconds a manual refresh waits after the latest usage request finished, so a
# double click does not send a second request.
MANUAL_REFRESH_INTERVAL = 15

_PLAN_LOG_KEYS = ('organization_type', 'rate_limit_tier', 'plan_display_name')


@dataclass(frozen=True)
class CacheSnapshot:
    """Immutable, consistent snapshot of cache state for the popup."""

    usage: dict[str, Any]
    profile: dict[str, Any] | None
    prepaid: dict[str, Any] | None
    last_success_time: float | None
    refreshing: bool
    last_error: str | None
    version: int
    history: dict[str, dict[str, Any]] = field(default_factory=dict)
    manual_refresh_reason: str | None = None
    manual_refresh_retry_at: float | None = None


@dataclass(frozen=True)
class UpdateResult:
    """Result of a ``UsageCache.update()`` call.

    Attributes
    ----------
    data : dict or None
        Raw API response dict, or ``None`` when the call was skipped
        (lock held or cooldown active).
    token_refresh : RefreshResult or None
        Set when a token refresh was attempted after a 401 auth error.
    token : str or None
        Access token that was in effect when the successful request was
        sent.  Lets the caller detect credentials that changed while the
        request was in flight, meaning ``data`` still belongs to the
        account that was active before an account switch.
    """

    data: dict[str, Any] | None
    token_refresh: RefreshResult | None = None
    token: str | None = None


class UsageCache:
    """Thread-safe cache managing API data, cooldown, and error state.

    All callers (poll loop, popup) go through ``update()`` instead
    of calling ``fetch_usage()`` directly.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._profile_lock = threading.Lock()
        self._usage: dict[str, Any] = {}
        self._usage_token: str | None = None
        self._profile: dict[str, Any] | None = None
        self._profile_token: str | None = None
        self._prepaid: dict[str, Any] | None = None
        self._prepaid_token: str | None = None
        self._prepaid_read_at: float | None = None
        # Readings of the current quota windows, kept in memory only and
        # recorded only while the forecast chart is enabled.
        self._history: dict[str, dict[str, Any]] = {}
        self._last_success_time: float | None = None
        self._refreshing = False
        self._last_error: str | None = None
        self._version = 0
        self._consecutive_errors = 0
        self._last_failed_token: str | None = None
        self._rate_limit_until: float = 0
        self._last_attempt_monotonic: float | None = None
        self._last_manual_monotonic: float | None = None

    # Public properties

    @property
    def usage(self) -> dict[str, Any]:
        """Last successful usage data (empty dict before first success)."""
        return self._usage

    @property
    def profile(self) -> dict[str, Any] | None:
        return self._profile

    @property
    def prepaid(self) -> dict[str, Any] | None:
        """Prepaid credit balance from the last read for the current token, or None."""
        return self._prepaid

    @property
    def last_success_time(self) -> float | None:
        return self._last_success_time

    @property
    def refreshing(self) -> bool:
        return self._refreshing

    @property
    def last_error(self) -> str | None:
        return self._last_error

    @property
    def version(self) -> int:
        """Change counter - incremented on every state change."""
        return self._version

    @property
    def consecutive_errors(self) -> int:
        return self._consecutive_errors

    @property
    def rate_limit_remaining(self) -> float:
        """Seconds remaining in the rate-limit backoff window, or 0."""
        return max(self._rate_limit_until - time.time(), 0)

    @property
    def snapshot(self) -> CacheSnapshot:
        """Return a consistent snapshot for the popup to display."""
        with self._state_lock:
            reason, retry_at = self._manual_refresh_state(time.time())
            return CacheSnapshot(
                usage=self._usage,
                profile=self._profile,
                prepaid=self._prepaid,
                last_success_time=self._last_success_time,
                refreshing=self._refreshing,
                last_error=self._last_error,
                version=self._version,
                history=self._history,
                manual_refresh_reason=reason,
                manual_refresh_retry_at=retry_at,
            )

    # Public methods

    def ensure_profile(self, *, bypass_rate_limit: bool = False) -> None:
        """Fetch the account profile if not yet loaded, or re-fetch if the access token changed (thread-safe).

        Acquires ``_lock`` around the HTTP call to prevent concurrent
        API requests with ``update()``.  Skips the fetch while a
        rate-limit backoff is active so a failed profile request does not
        keep hammering an already 429-limited endpoint.

        Parameters
        ----------
        bypass_rate_limit : bool
            When True, probe the profile even during a 429 backoff.  Used
            for the immediate account-switch check: the newly selected
            account has its own quota and cannot be the source of the
            backoff, so its identity must be readable right away.
        """
        current_token = read_access_token()
        if self._profile is not None and self._profile_token == current_token:
            return

        with self._profile_lock:
            current_token = read_access_token()
            if self._profile is not None and self._profile_token == current_token:
                return

            # Respect the rate-limit backoff window. A failed profile fetch
            # leaves _profile as None, so without this guard every popup open
            # would re-fire a request against an already 429-limited endpoint
            # and could prolong the backoff.
            if not bypass_rate_limit and time.time() < self._rate_limit_until:
                log.debug('ensure_profile skipped (rate-limit backoff, %.0fs remaining)', self._rate_limit_until - time.time())
                return

            log.info('fetch_profile started')
            with self._lock:
                profile = fetch_profile()
            with self._state_lock:
                self._profile = profile
                self._profile_token = current_token
                self._version += 1
            log.info('fetch_profile -> %s', _profile_log_summary(profile))

    def ensure_prepaid(self) -> None:
        """Read the prepaid credit balance unless it was read within ``POLL_FAST`` (thread-safe).

        Called when the popup opens.  The balance is shown nowhere else, so
        the background polling does not keep it current, and a popup whose
        usage data is still within the cooldown would otherwise show a
        balance from its previous opening.  Waits for an update in flight,
        which may read the balance itself, and skips the request during a
        rate-limit backoff.
        """
        if not self._prepaid_due():
            return

        with self._lock:
            if not self._prepaid_due():
                return

            if time.time() < self._rate_limit_until:
                log.debug('ensure_prepaid skipped (rate-limit backoff, %.0fs remaining)', self._rate_limit_until - time.time())
                return

            self._refresh_prepaid(self._usage, self._usage_token)

    def update(self, *, force: bool = False, manual: bool = False, with_prepaid: bool = False) -> UpdateResult:
        """Fetch usage data with lock and cooldown protection.

        Parameters
        ----------
        force : bool
            When True, bypass the ``POLL_FAST`` cooldown and the 429
            rate-limit backoff for this single fetch.  Used only for an
            immediate refresh after a confirmed account switch: the newly
            selected account has no polling history, so it cannot be the
            source of a rate limit those throttles guard against.  The
            update lock is still honored, so concurrent fetches never run.
        manual : bool
            Wait for any in-progress request, then refresh only outside the
            reset window and both manual cooldowns. Retain rate-limit backoff.
        with_prepaid : bool
            When True, a successful fetch also reads the prepaid credit
            balance, unless it was read within the last ``POLL_FAST``
            seconds.  Set only while the popup is open, the one place the
            balance is shown, so polling in the background costs a single
            request per cycle.

        Returns
        -------
        UpdateResult
            Contains the API response dict (``data``), or ``None``
            when the call was skipped.  If a token refresh was
            attempted, ``token_refresh`` carries the outcome.
        """
        if not self._lock.acquire(blocking=manual):
            log.debug('update skipped (another update in progress)')
            return UpdateResult(data=None)

        try:
            return self._update_locked(manual=manual, force=force, with_prepaid=with_prepaid)
        finally:
            self._lock.release()

    # Private helpers

    def _manual_refresh_state(self, now: float) -> tuple[str | None, float | None]:
        """Return the reason and retry time for a withheld manual request.

        The caller holds ``_state_lock``.  Both cooldowns use monotonic
        time, so a clock correction cannot shorten them.

        Returns
        -------
        tuple[str | None, float | None]
            ``'backoff'``, ``'reset'`` or ``'cooldown'`` with the wall-clock
            time at which a manual request is allowed again, or
            ``(None, None)`` when it is allowed now.
        """
        if now < self._rate_limit_until:
            return 'backoff', self._rate_limit_until

        if self._last_success_time is not None:
            offsets = reset_offsets(self._usage, datetime.fromtimestamp(now, timezone.utc))
            protected = [offset for offset in offsets if -RESET_BUFFER < offset < POLL_FAST]
            if protected:
                return 'reset', now + max(protected) + RESET_BUFFER

        monotonic_now = time.monotonic()
        remaining = 0.0
        if self._last_attempt_monotonic is not None:
            remaining = MANUAL_REFRESH_INTERVAL - (monotonic_now - self._last_attempt_monotonic)
        if self._last_manual_monotonic is not None:
            remaining = max(remaining, POLL_FAST - (monotonic_now - self._last_manual_monotonic))
        if remaining > 0:
            return 'cooldown', now + remaining
        return None, None

    def _update_locked(self, *, manual: bool = False, force: bool = False, with_prepaid: bool = False) -> UpdateResult:
        """Execute the actual update while holding ``_lock``."""
        # Clamp epoch-based throttle state after a backward clock jump
        # (manual correction, NTP step, VM restore) - otherwise cooldown and
        # backoff would block fetches until the wall clock catches up with
        # the pre-jump timestamps, potentially for hours.
        now = time.time()
        if self._last_success_time is not None and now < self._last_success_time:
            self._last_success_time = now - POLL_FAST
        if self._rate_limit_until - now > MAX_BACKOFF:
            self._rate_limit_until = now + MAX_BACKOFF

        if not (force or manual) and self._last_success_time is not None and time.time() - self._last_success_time < POLL_FAST:
            log.debug('update skipped (cooldown, %.0fs remaining)', POLL_FAST - (time.time() - self._last_success_time))
            return UpdateResult(data=None)

        if not force and time.time() < self._rate_limit_until:
            log.debug('update skipped (rate-limit backoff, %.0fs remaining)', self._rate_limit_until - time.time())
            return UpdateResult(data=None)

        if manual and not force:
            with self._state_lock:
                reason, _retry_at = self._manual_refresh_state(now)
            if reason is not None:
                log.debug('manual update deferred (%s)', reason)
                return UpdateResult(data=None)

        if self._last_failed_token is not None:
            if read_access_token() == self._last_failed_token:
                log.debug('update skipped (token unchanged after auth failure)')
                return UpdateResult(data=None)
            self._last_failed_token = None

        with self._state_lock:
            self._refreshing = True
            self._version += 1

        try:
            return self._fetch_and_process(manual=manual and not force, with_prepaid=with_prepaid)
        except Exception:
            with self._state_lock:
                self._refreshing = False
                self._last_attempt_monotonic = time.monotonic()
                self._version += 1
            raise

    def _fetch_and_process(self, *, manual: bool = False, with_prepaid: bool = False) -> UpdateResult:
        """Fetch usage data and process the response."""
        token_before = read_access_token()
        if manual and token_before:
            # Only a request that is actually sent counts against the manual
            # allowance - without a token fetch_usage() sends nothing.
            with self._state_lock:
                self._last_manual_monotonic = time.monotonic()
        log.info('fetch_usage started')
        data = fetch_usage()

        if 'error' in data:
            self._record_error(data)

            if data.get('rate_limited'):
                self._apply_rate_limit_backoff(data)

            token_refresh = None
            if data.get('auth_error'):
                log.warning('fetch_usage -> auth error, attempting token refresh')
                token_refresh, retry_data = self._try_token_refresh(token_before, with_prepaid=with_prepaid)
                if token_refresh is not None and self._last_error is None:
                    # Token refresh succeeded and retry was successful
                    return UpdateResult(data=self._usage, token_refresh=token_refresh, token=self._usage_token)
                if token_refresh is None:
                    # Refresh failed or token unchanged - block this token
                    self._last_failed_token = token_before
                if retry_data is not None:
                    # Report the retry's failure, not the repaired 401, so the
                    # caller reacts to the current state (e.g. a 429 backoff
                    # instead of a stale credentials error).
                    data = retry_data
            elif not data.get('rate_limited'):
                log.warning('fetch_usage -> error: %s', data['error'])

            with self._state_lock:
                self._refreshing = False
                self._last_attempt_monotonic = time.monotonic()
                self._version += 1
            return UpdateResult(data=data, token_refresh=token_refresh)

        pct_5h = (data.get('five_hour') or {}).get('utilization')
        pct_7d = (data.get('seven_day') or {}).get('utilization')
        log.info('fetch_usage -> OK (5h: %s%%, 7d: %s%%)', pct_5h if pct_5h is not None else '?', pct_7d if pct_7d is not None else '?')
        self._record_success(data, token_before)
        if with_prepaid and self._prepaid_due():
            self._refresh_prepaid(data, token_before)

        return UpdateResult(data=data, token=token_before)

    def _prepaid_due(self) -> bool:
        """Whether the last balance read is ``POLL_FAST`` or more seconds old.

        A read timestamp in the future (backward clock jump) counts as due,
        so the cooldown cannot block reads until the clock catches up.
        """
        if self._prepaid_read_at is None:
            return True

        elapsed = time.time() - self._prepaid_read_at

        return not 0 <= elapsed < POLL_FAST

    def _refresh_prepaid(self, usage: dict[str, Any], token: str | None) -> None:
        """Read and store the prepaid credit balance for the current organization.

        The caller holds ``_lock``.  Read only while extra usage is enabled,
        which the popup requires to render the balance at all, so an account
        that can never show it does not pay for reading it.  The balance is
        supplementary: without a known organization uuid, or on any failure,
        it is None and the usage result is unaffected.

        Parameters
        ----------
        usage : dict
            Latest successful usage response.
        token : str or None
            Access token *usage* was fetched with, which the balance is
            stored against.
        """
        prepaid = None
        org_uuid = ((self._profile or {}).get('organization') or {}).get('uuid')
        if (usage.get('extra_usage') or {}).get('is_enabled') and org_uuid:
            prepaid = fetch_prepaid_credits(org_uuid)
            log.debug('fetch_prepaid_credits -> %s', 'OK' if prepaid else 'unavailable')
            self._prepaid_read_at = time.time()

        with self._state_lock:
            self._prepaid = prepaid
            self._prepaid_token = token
            self._version += 1

    def _apply_rate_limit_backoff(self, data: dict[str, Any]) -> None:
        """Arm the 429 backoff window from a rate-limited error response.

        Uses the server's ``Retry-After`` when present (clamped between
        ``POLL_INTERVAL`` and ``MAX_BACKOFF``), otherwise an exponential
        backoff based on the consecutive error count.
        """
        retry_after = data.get('retry_after')
        if retry_after is not None and retry_after > 0:
            delay = min(max(retry_after, POLL_INTERVAL), MAX_BACKOFF)
        else:
            delay = min(POLL_INTERVAL * (2 ** max(self._consecutive_errors - 1, 0)), MAX_BACKOFF)
        self._rate_limit_until = time.time() + delay
        log.warning('fetch_usage -> rate limited, backoff %.0fs', delay)

    def _record_error(self, data: dict[str, Any], *, count: bool = True) -> None:
        """Apply common state updates after a failed API response.

        Parameters
        ----------
        data : dict
            API response containing ``'error'`` and optional ``'server_message'``.
        count : bool
            If True (default), increment ``_consecutive_errors``.
        """
        with self._state_lock:
            if count:
                self._consecutive_errors += 1
            error = data['error']
            server_msg = data.get('server_message')
            if server_msg:
                error += f'\n{server_msg}'
            self._last_error = error

    def restart_history(self) -> None:
        """Start the reading log over from the latest reading, after an account switch.

        Readings of the previous account must not continue into the new
        account's curve.  A similar reset time and an equal or higher
        utilization look like the same window to ``record_samples()``.
        """
        if not FORECAST_CHART:
            return

        with self._state_lock:
            self._history = record_samples({}, self._usage, self._last_success_time or time.time())
            self._version += 1

    def _record_success(self, data: dict[str, Any], token: str | None) -> None:
        """Apply common state updates after a successful API response.

        A balance read with a different token may belong to another account,
        so it is dropped rather than shown next to this account's usage, and
        the next read is due at once.

        Parameters
        ----------
        data : dict
            Successful API response.
        token : str or None
            Access token that was in effect when the request was sent.
        """
        # _usage is always reassigned (never mutated in place), so existing
        # CacheSnapshot references remain valid after this update.
        with self._state_lock:
            self._consecutive_errors = 0
            self._last_error = None
            self._last_success_time = time.time()
            # Reassigned, never mutated in place, like _usage below.  Only the
            # forecast chart reads the log, so without it nothing is recorded.
            if FORECAST_CHART:
                self._history = record_samples(self._history, data, self._last_success_time)
            self._rate_limit_until = 0
            self._last_failed_token = None
            self._usage = data
            self._usage_token = token
            if token != self._prepaid_token:
                self._prepaid = None
                self._prepaid_read_at = None
            self._refreshing = False
            self._last_attempt_monotonic = time.monotonic()
            self._version += 1

    def _try_token_refresh(self, token_before: str | None, *, with_prepaid: bool = False) -> tuple[RefreshResult | None, dict[str, Any] | None]:
        """Obtain a working access token after a 401 and retry the usage fetch.

        If the credentials already hold a different token - the user
        switched accounts, or the token was refreshed out of band since
        this request began - it is retried directly, skipping the slow
        ``claude update``.  Only an unchanged token is refreshed via the
        CLI.

        Parameters
        ----------
        token_before : str or None
            The token that was used for the failed request.
        with_prepaid : bool
            Read the prepaid credit balance after a successful retry, as
            ``update()`` does after a successful fetch.

        Returns
        -------
        tuple[RefreshResult | None, dict | None]
            The refresh outcome to report to the caller (``None`` when no
            usable token could be obtained: CLI unavailable, or token
            unchanged after the refresh), and the retry's API response
            (``None`` when no retry was made).
        """
        result = RefreshResult(success=True, updated=False, old_version='', new_version='', error='')

        current_token = read_access_token()
        if current_token in (token_before, None):
            # Token unchanged - refresh it via the CLI (claude update).
            result = refresh_token()
            if not result.success:
                log.info('token refresh failed: %s', result.error)
                return None, None

            current_token = read_access_token()
            if current_token == token_before:
                log.info('token refresh succeeded but token unchanged')
                return None, None

            log.info('token changed via refresh, retrying fetch_usage')
        else:
            log.info('token already changed, retrying fetch_usage without CLI refresh')

        data = fetch_usage()
        if 'error' not in data:
            log.info('retry -> OK')
            self._record_success(data, current_token)
            if with_prepaid and self._prepaid_due():
                self._refresh_prepaid(data, current_token)

            return result, data

        log.warning('retry -> error: %s', data['error'])
        # Update error message but do not increment _consecutive_errors
        # again (the caller already counted this update cycle as one error).
        self._record_error(data, count=False)
        if data.get('rate_limited'):
            self._apply_rate_limit_backoff(data)

        return result, data


def reset_offsets(usage: dict[str, Any], now: datetime) -> list[float]:
    """Return the seconds until each quota reset in a usage response.

    Parameters
    ----------
    usage : dict
        Usage API response.  Entries without a parsable ``resets_at`` are
        skipped, so a field of any other shape never raises.
    now : datetime
        Timezone-aware current time.

    Returns
    -------
    list[float]
        One offset per quota with a reset time, negative once it has passed.
    """
    offsets = []
    for entry in usage.values():
        if not isinstance(entry, dict) or not entry.get('resets_at'):
            continue
        try:
            # The subtraction stays inside the guard: a timestamp without a
            # UTC offset parses fine and only fails when subtracted.
            reset_time = datetime.fromisoformat(entry['resets_at'])
            offsets.append((reset_time - now).total_seconds())
        except (TypeError, ValueError, OverflowError):
            continue

    return offsets


def _profile_log_summary(profile: dict[str, Any] | None) -> str:
    """Return the outcome of a profile fetch for the log, with the plan identifiers on success.

    The identifiers say which plan the API reports, so a wrong plan name in
    the popup can be traced from a ``--verbose`` log.  None of them is
    personal data; ``repr`` keeps a ``null`` apart from an empty string.
    """
    if not profile:
        return 'failed'

    organization = profile.get('organization') if isinstance(profile, dict) else None
    if not isinstance(organization, dict):
        return 'OK'

    identifiers = ', '.join(f'{key}={organization.get(key)!r}' for key in _PLAN_LOG_KEYS)

    return f'OK ({identifiers})'

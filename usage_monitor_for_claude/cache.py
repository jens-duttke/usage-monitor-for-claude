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
from dataclasses import dataclass
from typing import Any

from .api import fetch_prepaid_credits, fetch_profile, fetch_usage, read_access_token
from .claude_cli import RefreshResult, refresh_token
from .codex import fetch_codex_usage
from .settings import MAX_BACKOFF, POLL_FAST, POLL_INTERVAL

CODEX_TTL = 5 * 60

__all__ = ['CacheSnapshot', 'UpdateResult', 'UsageCache']

log = logging.getLogger(__name__)


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
    codex_usage: dict[str, Any] | None = None
    codex_last_success_time: float | None = None
    codex_last_attempt_time: float | None = None
    codex_refresh_failed: bool = False


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
        self._codex_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._profile_lock = threading.Lock()
        self._usage: dict[str, Any] = {}
        self._usage_token: str | None = None
        self._profile: dict[str, Any] | None = None
        self._profile_token: str | None = None
        self._prepaid: dict[str, Any] | None = None
        self._codex_usage: dict[str, Any] | None = None
        self._codex_last_success: float | None = None
        self._codex_last_attempt: float | None = None
        self._codex_refresh_failed = False
        self._last_success_time: float | None = None
        self._refreshing = False
        self._last_error: str | None = None
        self._version = 0
        self._consecutive_errors = 0
        self._last_failed_token: str | None = None
        self._rate_limit_until: float = 0

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
        """Prepaid credit balance from the last successful fetch, or None."""
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
            return CacheSnapshot(
                usage=self._usage,
                profile=self._profile,
                prepaid=self._prepaid,
                codex_usage=self._codex_usage,
                codex_last_success_time=self._codex_last_success,
                codex_last_attempt_time=self._codex_last_attempt,
                codex_refresh_failed=self._codex_refresh_failed,
                last_success_time=self._last_success_time,
                refreshing=self._refreshing,
                last_error=self._last_error,
                version=self._version,
            )

    def codex_expired(self) -> bool:
        """Return whether the last valid Codex snapshot is older than its TTL.

        A failed refresh never changes the last-success timestamp, so a
        transient app-server failure leaves the previous valid snapshot
        available while the next cadence can retry it.
        """
        with self._state_lock:
            last_success = self._codex_last_success

        return last_success is None or time.monotonic() - last_success >= CODEX_TTL

    def refresh_codex_if_stale(self, *, force: bool = False) -> bool:
        """Refresh the Codex snapshot when its independent cadence is due.

        Parameters
        ----------
        force : bool
            Request an immediate refresh when the snapshot is expired.  A
            recent failed attempt still observes the TTL to avoid hammering
            an unavailable app-server when the popup is opened repeatedly.

        Returns
        -------
        bool
            True when a new valid snapshot was stored, otherwise False.
        """
        if not self._codex_lock.acquire(blocking=False):
            return False

        try:
            now = time.monotonic()
            with self._state_lock:
                last_attempt = self._codex_last_attempt
                last_success = self._codex_last_success
                expired = last_success is None or now - last_success >= CODEX_TTL
                if not expired:
                    return False
                if last_attempt is not None and now - last_attempt < CODEX_TTL:
                    return False
                self._codex_last_attempt = now

            try:
                codex_usage = self._fetch_codex_usage()
            except Exception:
                log.debug('fetch_codex_usage failed', exc_info=True)
                with self._state_lock:
                    self._codex_refresh_failed = True
                    self._version += 1
                return False

            if codex_usage is None:
                with self._state_lock:
                    self._codex_refresh_failed = True
                    self._version += 1
                return False

            with self._state_lock:
                self._codex_usage = codex_usage
                self._codex_last_success = time.monotonic()
                self._codex_refresh_failed = False
                self._version += 1
            return True
        finally:
            self._codex_lock.release()

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
            log.info('fetch_profile -> %s', 'OK' if profile else 'failed')

    def update(self, *, force: bool = False) -> UpdateResult:
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

        Returns
        -------
        UpdateResult
            Contains the API response dict (``data``), or ``None``
            when the call was skipped.  If a token refresh was
            attempted, ``token_refresh`` carries the outcome.
        """
        if not self._lock.acquire(blocking=False):
            log.debug('update skipped (another update in progress)')
            return UpdateResult(data=None)

        try:
            return self._update_locked(force=force)
        finally:
            self._lock.release()

    # Private helpers

    def _update_locked(self, *, force: bool = False) -> UpdateResult:
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

        if not force and self._last_success_time is not None and time.time() - self._last_success_time < POLL_FAST:
            log.debug('update skipped (cooldown, %.0fs remaining)', POLL_FAST - (time.time() - self._last_success_time))
            return UpdateResult(data=None)

        if not force and time.time() < self._rate_limit_until:
            log.debug('update skipped (rate-limit backoff, %.0fs remaining)', self._rate_limit_until - time.time())
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
            return self._fetch_and_process()
        except Exception:
            with self._state_lock:
                self._refreshing = False
                self._version += 1
            raise

    def _fetch_and_process(self) -> UpdateResult:
        """Fetch usage data and process the response."""
        token_before = read_access_token()
        log.info('fetch_usage started')
        data = fetch_usage()

        if 'error' in data:
            self._record_error(data)

            if data.get('rate_limited'):
                self._apply_rate_limit_backoff(data)

            token_refresh = None
            if data.get('auth_error'):
                log.warning('fetch_usage -> auth error, attempting token refresh')
                token_refresh, retry_data = self._try_token_refresh(token_before)
                if token_refresh is not None and self._last_error is None:
                    return UpdateResult(data=self._usage, token_refresh=token_refresh, token=self._usage_token)
                if token_refresh is None:
                    self._last_failed_token = token_before
                if retry_data is not None:
                    data = retry_data
            elif not data.get('rate_limited'):
                log.warning('fetch_usage -> error: %s', data['error'])

            with self._state_lock:
                self._refreshing = False
                self._version += 1
            return UpdateResult(data=data, token_refresh=token_refresh)

        pct_5h = (data.get('five_hour') or {}).get('utilization')
        pct_7d = (data.get('seven_day') or {}).get('utilization')
        log.info('fetch_usage -> OK (5h: %s%%, 7d: %s%%)', pct_5h if pct_5h is not None else '?', pct_7d if pct_7d is not None else '?')
        prepaid = self._fetch_prepaid_balance(data)
        self._record_success(data, token_before, prepaid)
        return UpdateResult(data=data, token=token_before)

    def _fetch_codex_usage(self) -> dict[str, Any] | None:
        """Return the optional Codex snapshot without affecting Claude updates."""
        try:
            return fetch_codex_usage()
        except Exception:
            log.debug('fetch_codex_usage failed', exc_info=True)
            return None

    def _fetch_prepaid_balance(self, usage: dict[str, Any]) -> dict[str, Any] | None:
        """Return the prepaid credit balance for the current organization.

        Read only while extra usage is enabled, which the popup requires to
        render the balance at all, so an account that can never show it does
        not pay for reading it.  The request runs in the same cycle as the
        usage fetch and therefore inherits the cooldown, the 429 backoff and
        the adaptive cadence of ``_update_locked``.  The balance is
        supplementary: without a known organization uuid, or on any failure,
        it stays None and the usage result is unaffected.

        Parameters
        ----------
        usage : dict
            Successful usage response of the current cycle.
        """
        if not (usage.get('extra_usage') or {}).get('is_enabled'):
            return None

        org_uuid = ((self._profile or {}).get('organization') or {}).get('uuid')
        if not org_uuid:
            return None

        prepaid = fetch_prepaid_credits(org_uuid)
        log.debug('fetch_prepaid_credits -> %s', 'OK' if prepaid else 'unavailable')

        return prepaid

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

    def _record_success(self, data: dict[str, Any], token: str | None, prepaid: dict[str, Any] | None) -> None:
        """Apply common state updates after a successful API response.

        Parameters
        ----------
        data : dict
            Successful API response.
        token : str or None
            Access token that was in effect when the request was sent.
        prepaid : dict or None
            Prepaid credit balance fetched in the same cycle, or None when
            unavailable.
        """
        # _usage is always reassigned (never mutated in place), so existing
        # CacheSnapshot references remain valid after this update.
        with self._state_lock:
            self._consecutive_errors = 0
            self._last_error = None
            self._last_success_time = time.time()
            self._rate_limit_until = 0
            self._last_failed_token = None
            self._usage = data
            self._usage_token = token
            self._prepaid = prepaid
            self._refreshing = False
            self._version += 1

    def _try_token_refresh(self, token_before: str | None) -> tuple[RefreshResult | None, dict[str, Any] | None]:
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
            prepaid = self._fetch_prepaid_balance(data)
            self._record_success(data, current_token, prepaid)
            return result, data

        log.warning('retry -> error: %s', data['error'])
        # Update error message but do not increment _consecutive_errors
        # again (the caller already counted this update cycle as one error).
        self._record_error(data, count=False)
        if data.get('rate_limited'):
            self._apply_rate_limit_backoff(data)

        return result, data

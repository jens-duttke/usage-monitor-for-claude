"""
Application
=============

System tray application class with adaptive polling and event handling.
"""
from __future__ import annotations

import math
import threading
import time
import traceback
import webbrowser
from datetime import datetime, timedelta, timezone
from typing import Any

import pystray  # type: ignore[import-untyped]  # no type stubs available

from .api import api_headers, read_access_token
from .cache import UsageCache
from .claude_cli import PROJECT_URL
from .command import run_event_command
from .instance_id import effective_config_dir, is_default_config_dir
from .platforms import (
    autostart_supported, get_idle_seconds, install_tray_click_handler, is_autostart_enabled,
    is_screensaver_running, is_workstation_locked, set_autostart, show_error_box, sync_autostart_path,
    taskbar_uses_light_theme, watch_theme_change,
)
from .settings import (
    ALERT_EXTRA_USAGE_SPENT, ALERT_TIME_AWARE, ALERT_TIME_AWARE_BELOW, ICON_FIELDS, IDLE_INTERVAL, IDLE_PAUSE,
    NOTIFY_CLAUDE_UPDATE, ON_RESET_COMMAND, ON_STARTUP_COMMAND, ON_THRESHOLD_COMMAND, POLL_ERROR, POLL_FAST,
    POLL_FAST_EXTRA, POLL_INTERVAL, QUICK_ACTION_COMMAND, get_alert_thresholds,
)
from .formatting import elapsed_pct, field_period, format_credits, format_tooltip, parse_field_name, popup_label
from .i18n import T
from .popup import UsagePopup
from .tray_icon import create_icon_image, create_status_image

__all__ = ['UsageMonitorForClaude', 'crash_log']

# Seconds after a reset at which to place the confirming poll.  A small buffer
# absorbs minor timing differences (clocks, caches, server-side propagation).
RESET_BUFFER = 5


def _future_iso(**kwargs: float) -> str:
    """Return an ISO 8601 timestamp offset from now by the given timedelta kwargs."""
    return (datetime.now(timezone.utc) + timedelta(**kwargs)).isoformat()


def _align_to_reset(interval: int, next_reset: float | None) -> tuple[int, bool]:
    """Shift the next poll so the confirming poll lands just after the reset.

    Every returned interval stays at or above ``POLL_FAST`` (the cache
    cooldown), so the reset is caught without polling faster.  The poll before
    the reset is pulled forward to ``POLL_FAST - RESET_BUFFER`` seconds before
    it (the danger-window start); from there the confirming poll lands
    ``RESET_BUFFER`` seconds after the reset.  When the current poll is
    already too close to pull the previous one forward without breaking the
    cooldown, the confirming poll is committed directly.

    Parameters
    ----------
    interval : int
        The normal cadence interval before reset alignment.
    next_reset : float or None
        Seconds until the nearest upcoming reset, or None.

    Returns
    -------
    tuple[int, bool]
        The (possibly adjusted) interval and whether alignment engaged.
    """
    if next_reset is None or next_reset <= 0:
        return interval, False

    danger = POLL_FAST - RESET_BUFFER          # last window where a poll can no longer be exact
    post = int(next_reset) + RESET_BUFFER      # offset that lands the poll just after the reset

    if next_reset <= danger:
        # Already inside that last window: the confirming poll can only land
        # POLL_FAST after this one (small, unavoidable overshoot).
        return POLL_FAST, True

    if post <= interval * 1.5:
        # Reset near enough: commit the confirming poll to just after it.
        return post, True

    if next_reset < interval + danger:
        # A normal interval would drop the next poll into that last window,
        # from where the confirming poll would overshoot.  Pull it forward to
        # the window start (POLL_FAST - RESET_BUFFER before the reset); if
        # that is too close to keep POLL_FAST spacing, commit to the
        # confirming poll directly.
        pre = int(next_reset) - danger
        return (pre if pre >= POLL_FAST else post), True

    return interval, False                     # reset still far - keep the normal cadence


class UsageMonitorForClaude:
    """System tray application displaying Claude usage."""

    def __init__(self) -> None:
        """Set up the tray icon with context menu and polling state."""
        self.running = True
        self.cache = UsageCache()

        # Last raw API response (may contain 'error') - for icon and polling decisions
        self._last_response: dict[str, Any] = {}

        # Notification state
        self._prev_utilization: dict[str, float] = {}
        self._prev_account_uuid: str | None = None
        self._first_update_done = False
        self._notified_thresholds: dict[str, float] = {}

        # Adaptive polling state
        self._fast_polls_remaining = 0
        # Guarded by _notify_lock: deferrals arrive from the popup and poll
        # threads while the poll loop flushes.
        self._notify_lock = threading.Lock()
        self._deferred_notifications: dict[str, tuple[str, str]] = {}

        # Popup state
        self._popup_lock = threading.Lock()
        self._popup_open = False
        self._popup_closed_at = 0.0
        self._next_poll_time: float | None = None

        # Theme state
        self._light_taskbar = taskbar_uses_light_theme()

        self.restart_requested = False

        # Non-default config dirs get a tooltip prefix so multiple
        # instances (one per Claude account) can be told apart.
        self._tooltip_prefix = '' if is_default_config_dir() else f'[{effective_config_dir().name}] '

        self.icon = pystray.Icon(
            'usage_monitor',
            icon=create_icon_image(0, 0, self._light_taskbar),
            title=self._tooltip_prefix + T['loading'],
            menu=pystray.Menu(
                pystray.MenuItem(T['menu_show'], self.on_show_popup, default=True),
                pystray.MenuItem(
                    T['menu_quick_action'], self.on_run_quick_action,
                    visible=self._quick_action_menu_visible,
                ),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem(
                    T['autostart'], self.on_toggle_autostart,
                    checked=lambda item: is_autostart_enabled(),
                    visible=autostart_supported(),
                ),
                pystray.MenuItem(T['test_commands'], pystray.Menu(
                    pystray.MenuItem(T['test_reset_5h'], self.on_test_reset_5h, enabled=bool(ON_RESET_COMMAND)),
                    pystray.MenuItem(T['test_reset_7d'], self.on_test_reset_7d, enabled=bool(ON_RESET_COMMAND)),
                    pystray.MenuItem(T['test_threshold_5h'], self.on_test_threshold_5h, enabled=bool(ON_THRESHOLD_COMMAND)),
                    pystray.MenuItem(T['test_threshold_7d'], self.on_test_threshold_7d, enabled=bool(ON_THRESHOLD_COMMAND)),
                    pystray.MenuItem(T['test_startup'], self.on_test_startup, enabled=bool(ON_STARTUP_COMMAND)),
                    pystray.MenuItem(T['test_quick_action'], self.on_test_quick_action, enabled=bool(QUICK_ACTION_COMMAND)),
                ), enabled=bool(ON_RESET_COMMAND or ON_STARTUP_COMMAND or ON_THRESHOLD_COMMAND or QUICK_ACTION_COMMAND)),
                pystray.MenuItem(T['restart'], self.on_restart),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem(T['menu_project'], self.on_open_project),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem(T['quit'], self.on_quit),
            ),
        )

        # Only wired up when a command is configured, so the platform's own
        # single-click behavior is otherwise untouched.  Not every platform
        # can offer this - a StatusNotifierItem is driven by the panel, not by
        # this process - so the result is reported rather than assumed.
        self.double_click_installed = False
        if QUICK_ACTION_COMMAND:
            self.double_click_installed = install_tray_click_handler(
                self.icon, self.on_show_popup, self._run_quick_action,
            )
            if not self.double_click_installed:
                # Printed rather than shown: not worth a dialog on every start,
                # and the command is still reachable from the menu entry that
                # appears in exactly this case.  Visible with --verbose, which
                # is where someone looks when the double-click stopped working.
                print('A quick action is configured, but this system does not report tray double-clicks. '
                      'Use the tray menu entry instead.')

    # Menu actions

    def on_show_popup(self, icon: Any = None, item: Any = None) -> None:
        with self._popup_lock:
            if self._popup_open:
                return
            if time.time() - self._popup_closed_at < 0.15:
                return
            self._popup_open = True
        threading.Thread(target=self._open_popup, daemon=True).start()

    def on_toggle_autostart(self, icon: Any = None, item: Any = None) -> None:
        set_autostart(not is_autostart_enabled())

    def on_restart(self, icon: Any = None, item: Any = None) -> None:
        self.restart_requested = True
        self.on_quit(icon, item)

    def on_open_project(self, icon: Any = None, item: Any = None) -> None:
        webbrowser.open(PROJECT_URL)

    def on_test_reset_5h(self, icon: Any = None, item: Any = None) -> None:
        run_event_command(ON_RESET_COMMAND, {
            'USAGE_MONITOR_EVENT': 'reset',
            'USAGE_MONITOR_VARIANT': 'five_hour',
            'USAGE_MONITOR_UTILIZATION': '0',
            'USAGE_MONITOR_PREV_UTILIZATION': '95',
            'USAGE_MONITOR_UTILIZATION_FIVE_HOUR': '0',
            'USAGE_MONITOR_UTILIZATION_SEVEN_DAY': '45',
            'USAGE_MONITOR_RESETS_AT': _future_iso(hours=5),
            'USAGE_MONITOR_TITLE': T['notify_reset_title'],
            'USAGE_MONITOR_MESSAGE': T['notify_reset'],
        }, capture_output=True)

    def on_test_reset_7d(self, icon: Any = None, item: Any = None) -> None:
        run_event_command(ON_RESET_COMMAND, {
            'USAGE_MONITOR_EVENT': 'reset',
            'USAGE_MONITOR_VARIANT': 'seven_day',
            'USAGE_MONITOR_UTILIZATION': '0',
            'USAGE_MONITOR_PREV_UTILIZATION': '99',
            'USAGE_MONITOR_UTILIZATION_FIVE_HOUR': '12',
            'USAGE_MONITOR_UTILIZATION_SEVEN_DAY': '0',
            'USAGE_MONITOR_RESETS_AT': _future_iso(days=7),
            'USAGE_MONITOR_TITLE': T['notify_reset_title'],
            'USAGE_MONITOR_MESSAGE': T['notify_reset'],
        }, capture_output=True)

    def on_test_threshold_5h(self, icon: Any = None, item: Any = None) -> None:
        run_event_command(ON_THRESHOLD_COMMAND, {
            'USAGE_MONITOR_EVENT': 'threshold',
            'USAGE_MONITOR_VARIANT': 'five_hour',
            'USAGE_MONITOR_UTILIZATION': '82',
            'USAGE_MONITOR_THRESHOLD': '80',
            'USAGE_MONITOR_RESETS_AT': _future_iso(hours=3),
            'USAGE_MONITOR_TITLE': T['notify_threshold_title'],
            'USAGE_MONITOR_MESSAGE': T['notify_threshold_generic'].format(label=popup_label('five_hour'), pct='82'),
        }, capture_output=True)

    def on_test_threshold_7d(self, icon: Any = None, item: Any = None) -> None:
        run_event_command(ON_THRESHOLD_COMMAND, {
            'USAGE_MONITOR_EVENT': 'threshold',
            'USAGE_MONITOR_VARIANT': 'seven_day',
            'USAGE_MONITOR_UTILIZATION': '81',
            'USAGE_MONITOR_THRESHOLD': '80',
            'USAGE_MONITOR_RESETS_AT': _future_iso(days=4),
            'USAGE_MONITOR_TITLE': T['notify_threshold_title'],
            'USAGE_MONITOR_MESSAGE': T['notify_threshold_generic'].format(label=popup_label('seven_day'), pct='81'),
        }, capture_output=True)

    def on_test_startup(self, icon: Any = None, item: Any = None) -> None:
        run_event_command(ON_STARTUP_COMMAND, {
            'USAGE_MONITOR_EVENT': 'startup',
            'USAGE_MONITOR_UTILIZATION_FIVE_HOUR': '0',
            'USAGE_MONITOR_RESETS_AT_FIVE_HOUR': '',
            'USAGE_MONITOR_UTILIZATION_SEVEN_DAY': '45',
            'USAGE_MONITOR_RESETS_AT_SEVEN_DAY': _future_iso(days=3),
        }, capture_output=True)

    def _quick_action_menu_visible(self, item: Any = None) -> bool:
        """Whether the menu needs to offer the quick action.

        Only where the tray cannot report a double-click itself, so a
        configured quick action stays reachable instead of being dead.
        pystray resolves this when the menu opens, which is after the click
        handler had its chance to install.
        """
        return bool(QUICK_ACTION_COMMAND) and not self.double_click_installed

    def on_run_quick_action(self, icon: Any = None, item: Any = None) -> None:
        """Run the configured quick action from the menu.

        The menu is the only route to it on a desktop whose panel handles the
        tray click itself and never passes it to the application.
        """
        self._run_quick_action()

    def on_test_quick_action(self, icon: Any = None, item: Any = None) -> None:
        run_event_command(QUICK_ACTION_COMMAND, {
            'USAGE_MONITOR_EVENT': 'quick_action',
            'USAGE_MONITOR_UTILIZATION_FIVE_HOUR': '30',
            'USAGE_MONITOR_RESETS_AT_FIVE_HOUR': _future_iso(hours=3),
            'USAGE_MONITOR_UTILIZATION_SEVEN_DAY': '55',
            'USAGE_MONITOR_RESETS_AT_SEVEN_DAY': _future_iso(days=4),
        }, capture_output=True)

    def on_quit(self, icon: Any = None, item: Any = None) -> None:
        self.running = False
        self.icon.stop()

    # Popup

    def _should_refresh_usage(self) -> bool:
        """Return whether opening the popup should trigger a background usage fetch.

        Refreshes stale data, with one exception: when a quota reset is closer
        than the cache cooldown, a fetch now would advance
        ``last_success_time`` into the last ``POLL_FAST`` window before the
        reset and force the reset-aligned poll to overshoot.  Such a fetch is
        deferred to the scheduled reset poll, whose fresh data the open popup
        picks up live.  The very first fetch (no data yet) always refreshes.
        """
        last = self.cache.last_success_time
        if last is None:
            return True
        if time.time() - last < POLL_FAST:
            return False

        next_reset = self._seconds_until_next_reset()
        return not (next_reset is not None and next_reset < POLL_FAST)

    def _open_popup(self) -> None:
        # _popup_open is set True under _popup_lock (in on_show_popup) and
        # reset here without the lock.  This is safe because False is the
        # permissive default - a momentary stale True only delays the next open.
        try:
            needs_profile = not self.cache.profile
            needs_refresh = self._should_refresh_usage()
            if needs_profile or needs_refresh:
                # Single thread: ensure_profile() and update() both acquire
                # cache._lock, so they must run sequentially.  Two threads
                # would cause update()'s non-blocking acquire to fail while
                # ensure_profile() holds the lock.
                def _bg_refresh() -> None:
                    if needs_profile:
                        self.cache.ensure_profile()
                    if needs_refresh:
                        self.update()
                threading.Thread(target=_bg_refresh, daemon=True).start()
            UsagePopup(self)
        finally:
            self._popup_closed_at = time.time()
            self._popup_open = False

    # Double-click handling

    # Tray rendering

    def _render_tray(self) -> None:
        """Re-render tray icon and tooltip from current state."""
        data = self._last_response
        if 'error' in data:
            self.icon.icon = create_status_image('C!' if data.get('auth_error') else '!', self._light_taskbar)
        else:
            top_field, top_mode = ICON_FIELDS[0].split(':', 1) if ':' in ICON_FIELDS[0] else (ICON_FIELDS[0], 'utilization')
            bottom_field, bottom_mode = ICON_FIELDS[1].split(':', 1) if ':' in ICON_FIELDS[1] else (ICON_FIELDS[1], 'utilization')
            # isinstance instead of truthiness: a configured field may point at
            # a non-dict response value (e.g. the raw limits array).
            top_entry = data.get(top_field)
            bottom_entry = data.get(bottom_field)
            if not isinstance(top_entry, dict):
                top_entry = {}
            if not isinstance(bottom_entry, dict):
                bottom_entry = {}
            pct_top = top_entry.get('utilization', 0) or 0
            pct_bottom = bottom_entry.get('utilization', 0) or 0
            top_period = field_period(top_field)
            bottom_period = field_period(bottom_field)
            time_pct_top = elapsed_pct(top_entry.get('resets_at', ''), top_period) if top_period else None
            time_pct_bottom = elapsed_pct(bottom_entry.get('resets_at', ''), bottom_period) if bottom_period else None
            extra = data.get('extra_usage') or {}
            extra_limit = extra.get('monthly_limit') or 0
            extra_used = extra.get('used_credits') or 0
            # A missing/null monthly_limit means uncapped pay-as-you-go extra
            # usage, which cannot be exhausted.
            extra_usage_available = bool(extra.get('is_enabled')) and (extra_limit <= 0 or extra_used < extra_limit)
            self.icon.icon = create_icon_image(
                pct_top, pct_bottom, self._light_taskbar,
                mode_top=top_mode, mode_bottom=bottom_mode,
                time_pct_top=time_pct_top, time_pct_bottom=time_pct_bottom,
                extra_usage_available=extra_usage_available,
            )
        self.icon.title = self._tooltip_prefix + format_tooltip(data)

    def _on_theme_changed(self) -> None:
        """Re-render the tray icon when the Windows theme changes."""
        light = taskbar_uses_light_theme()
        if light == self._light_taskbar:
            return

        self._light_taskbar = light
        if self._last_response:
            self._render_tray()

    # Update orchestration

    def update(self, force: bool = False) -> None:
        """Request a data refresh from the cache and process the result.

        Parameters
        ----------
        force : bool
            When True, bypass the cache cooldown and the 429 rate-limit
            backoff so the refresh happens immediately.  Used after a
            confirmed account switch, where the freshly selected account
            has no polling history that those throttles need to protect.
        """
        result = self.cache.update(force=force)
        if result.data is None:
            return

        self._last_response = result.data
        self._render_tray()

        # Handle CLI update notification from token refresh
        if NOTIFY_CLAUDE_UPDATE and result.token_refresh and result.token_refresh.updated:
            self.icon.notify(
                T['notify_update'].format(old=result.token_refresh.old_version, new=result.token_refresh.new_version),
                T['notify_update_title'],
            )

        if 'error' in result.data:
            return

        # The credentials token changed after this usage data was fetched: the user switched
        # accounts while the request was in flight, so the data still belongs to the previous
        # account.  Comparing it against the new account's profile would announce the switch on
        # top of the old account's numbers and consume the UUID baseline, leaving the stale data
        # in place until the next regular poll.  Keep every baseline untouched instead - for a
        # real switch the poll loop's token watcher forces an immediate refetch that reports it
        # with the new data; a same-account token rotation just resumes on the next poll.
        if result.token is not None and result.token != read_access_token():
            return

        # Detect account switch: re-fetch profile if the access token changed, then compare UUIDs.
        # When the user runs 'claude auth login', the token changes and the next profile fetch
        # returns a different account UUID, preventing a false quota-reset notification.
        self.cache.ensure_profile()
        current_profile = self.cache.profile
        current_account_uuid = (current_profile.get('account') or {}).get('uuid') if isinstance(current_profile, dict) else None

        # Unknown identity with a known baseline: the profile fetch failed
        # after a token change, so this usage data may already belong to a
        # different account.  Skip all cross-poll comparisons and keep the
        # baselines untouched; the poll where the profile is readable again
        # detects the switch (or resumes normally for the same account).
        if self._prev_account_uuid is not None and current_account_uuid is None:
            return

        if self._prev_account_uuid is not None and current_account_uuid is not None and current_account_uuid != self._prev_account_uuid:
            email = (current_profile.get('account') or {}).get('email', '')
            message = T['notify_account_switched'].format(email=email) if email else T['notify_account_switched_title']
            self._notify_or_defer('account_switched', message, T['notify_account_switched_title'])
            self._prev_utilization = {}
            self._notified_thresholds = {}
            self._prev_account_uuid = current_account_uuid
            return
        self._prev_account_uuid = current_account_uuid

        # Collect all quota fields with utilization (extra_usage has a different structure)
        quota_fields: dict[str, float] = {}
        for key, value in result.data.items():
            if key == 'extra_usage':
                continue
            if isinstance(value, dict) and 'utilization' in value:
                quota_fields[key] = value.get('utilization', 0) or 0

        # Notify when quota resets after being nearly exhausted, but only if no other quota is blocking usage.
        # While idle/locked, defer notifications until the user returns (avoids lock screen privacy concerns).
        # The message carries no field information, so several quotas resetting
        # within one polling gap still produce a single notification.
        reset_detected = False
        for key, pct in quota_fields.items():
            prev = self._prev_utilization.get(key)
            if prev is None:
                continue

            parsed = parse_field_name(key)
            if parsed is None:
                continue

            _, unit, _ = parsed
            reset_threshold = 95 if unit == 'hour' else 98
            any_blocking = any(other_pct >= 99 for other_key, other_pct in quota_fields.items() if other_key != key)

            if prev > reset_threshold and pct < prev and not any_blocking:
                reset_detected = True

        if reset_detected:
            self._notify_or_defer('reset', T['notify_reset'], T['notify_reset_title'])

        # Run reset command on any detected usage drop (independent of notification threshold)
        for key, pct in quota_fields.items():
            prev = self._prev_utilization.get(key)
            if prev is not None and pct < prev:
                self._run_reset_command(key, pct, prev, data=result.data, entry=result.data.get(key, {}))

        self._check_threshold_alerts(result.data)

        # Adaptive polling: speed up when icon top field usage is increasing
        icon_top_key = ICON_FIELDS[0].split(':', 1)[0]
        icon_top_pct = quota_fields.get(icon_top_key, 0)
        icon_top_prev = self._prev_utilization.get(icon_top_key)
        if icon_top_prev is not None and icon_top_pct > icon_top_prev:
            self._fast_polls_remaining = POLL_FAST_EXTRA + 1
        elif self._fast_polls_remaining > 0:
            self._fast_polls_remaining -= 1

        self._prev_utilization = quota_fields

        if not self._first_update_done:
            self._run_startup_command(result.data)

        self._first_update_done = True

    # Notifications

    def _notify_or_defer(self, category: str, message: str, title: str) -> None:
        """Show a notification immediately, or defer it if the user is away.

        Parameters
        ----------
        category : str
            Deduplication key (e.g. ``'reset'``, ``'threshold_five_hour'``).
            While deferred, only the latest notification per category is
            kept so the user does not get a flood on return.
        message : str
            Notification body text.
        title : str
            Notification title.
        """
        if self._is_user_away():
            with self._notify_lock:
                self._deferred_notifications[category] = (message, title)
        else:
            self.icon.notify(message, title)

    def _flush_deferred_notifications(self) -> None:
        """Show all deferred notifications and clear the queue.

        The queue is swapped out under the lock so a deferral landing
        mid-flush (from the popup thread) is kept for the next flush
        instead of mutating the dict being iterated.
        """
        with self._notify_lock:
            pending, self._deferred_notifications = self._deferred_notifications, {}
        for message, title in pending.values():
            self.icon.notify(message, title)

    def _check_threshold_alerts(self, data: dict[str, Any]) -> None:
        """Show a notification when usage crosses a configured threshold.

        Dynamically detects all quota fields in the API response.  For
        each field, finds the highest threshold exceeded by current
        utilization.  If it exceeds a threshold not yet notified, shows a
        single notification with the current usage percentage.  When usage
        drops (e.g. after reset), tracking resets so thresholds can
        re-trigger in the next cycle.
        """
        for variant_key, entry in data.items():
            if variant_key == 'extra_usage':
                continue
            if not isinstance(entry, dict) or entry.get('utilization') is None:
                continue

            pct = entry['utilization']
            thresholds = get_alert_thresholds(variant_key)
            if not thresholds:
                continue

            exceeded = [t for t in thresholds if pct >= t]
            highest_exceeded = max(exceeded) if exceeded else 0
            last_notified = self._notified_thresholds.get(variant_key, 0)

            if ALERT_TIME_AWARE and highest_exceeded > last_notified and highest_exceeded < ALERT_TIME_AWARE_BELOW:
                period = field_period(variant_key)
                if period:
                    time_pct = elapsed_pct(entry.get('resets_at'), period)
                    if time_pct is not None and pct <= time_pct:
                        self._notified_thresholds[variant_key] = highest_exceeded
                        continue

            if highest_exceeded > last_notified:
                title = T['notify_threshold_title']
                label = popup_label(variant_key)
                message = T['notify_threshold_generic'].format(label=label, pct=f'{pct:.0f}')
                self._notify_or_defer(f'threshold_{variant_key}', message, title)
                self._run_threshold_command(variant_key, pct, highest_exceeded, entry, title, message)
                self._notified_thresholds[variant_key] = highest_exceeded
            elif highest_exceeded < last_notified:
                self._notified_thresholds[variant_key] = highest_exceeded

        self._check_extra_usage_alerts(data)

    def _check_extra_usage_alerts(self, data: dict[str, Any]) -> None:
        """Show a notification when extra usage crosses a configured threshold.

        Extra usage has a different data format (``used_credits`` /
        ``monthly_limit``) and no time-based reset, so it is handled
        separately from the sliding-window quotas.
        """
        extra = data.get('extra_usage')
        if not extra or not extra.get('is_enabled'):
            return

        used = extra.get('used_credits', 0) or 0
        currency = extra.get('currency')
        decimal_places = extra.get('decimal_places')
        used_text = format_credits(used, currency, decimal_places)

        limit = extra.get('monthly_limit', 0) or 0
        if limit > 0:
            pct = used / limit * 100
            thresholds = get_alert_thresholds('extra_usage')
            exceeded = [t for t in thresholds if pct >= t]
            highest_exceeded = max(exceeded) if exceeded else 0
            last_notified = self._notified_thresholds.get('extra_usage', 0)

            if highest_exceeded > last_notified:
                title = T['notify_threshold_title']
                limit_text = format_credits(limit, currency, decimal_places)
                message = T['notify_threshold_extra_usage'].format(
                    pct=f'{pct:.0f}', used=used_text, limit=limit_text,
                )
                self._notify_or_defer('threshold_extra_usage', message, title)
                self._run_threshold_command(
                    'extra_usage', pct, highest_exceeded, extra, title, message,
                    extra_used=used_text, extra_limit=limit_text,
                )
                self._notified_thresholds['extra_usage'] = highest_exceeded
            elif highest_exceeded < last_notified:
                self._notified_thresholds['extra_usage'] = highest_exceeded

        self._check_extra_usage_spent_alerts(extra, used, used_text)

    def _check_extra_usage_spent_alerts(self, extra: dict[str, Any], used: float, used_text: str) -> None:
        """Show a notification when extra-usage spending crosses a configured amount.

        Amounts in ``ALERT_EXTRA_USAGE_SPENT`` are absolute major-unit values
        (e.g. dollars), so they also work for accounts whose extra usage has
        no monthly limit and can never produce a percentage.
        """
        if not ALERT_EXTRA_USAGE_SPENT:
            return

        decimal_places = extra.get('decimal_places')
        places = decimal_places if decimal_places is not None else 2
        spent = used / (10 ** places)

        exceeded = [amount for amount in ALERT_EXTRA_USAGE_SPENT if spent >= amount]
        highest_exceeded = max(exceeded) if exceeded else 0
        last_notified = self._notified_thresholds.get('extra_usage_spent', 0)

        if highest_exceeded > last_notified:
            title = T['notify_threshold_title']
            message = T['notify_threshold_extra_usage_spent'].format(used=used_text)
            self._notify_or_defer('threshold_extra_usage_spent', message, title)
            self._run_threshold_command(
                'extra_usage_spent', None, highest_exceeded, extra, title, message,
                extra_used=used_text,
            )
            self._notified_thresholds['extra_usage_spent'] = highest_exceeded
        elif highest_exceeded < last_notified:
            self._notified_thresholds['extra_usage_spent'] = highest_exceeded

    # Event commands

    def _quota_snapshot_env(self, data: dict[str, Any]) -> dict[str, str]:
        """Build environment variables describing the current quota state.

        Emits one ``USAGE_MONITOR_UTILIZATION_<FIELD>`` /
        ``USAGE_MONITOR_RESETS_AT_<FIELD>`` pair per detected quota field, plus
        ``USAGE_MONITOR_EXTRA_USED`` when paid extra usage is enabled and
        ``USAGE_MONITOR_EXTRA_LIMIT`` when it also has a monthly limit (an
        uncapped account has no limit to report).  Shared by the startup and
        double-click commands.
        """
        env_vars: dict[str, str] = {}
        for key, entry in data.items():
            if key == 'extra_usage' or not isinstance(entry, dict) or 'utilization' not in entry:
                continue
            env_vars[f'USAGE_MONITOR_UTILIZATION_{key.upper()}'] = str(round(entry.get('utilization', 0) or 0))
            env_vars[f'USAGE_MONITOR_RESETS_AT_{key.upper()}'] = entry.get('resets_at') or ''

        extra = data.get('extra_usage') or {}
        if extra.get('is_enabled'):
            limit = extra.get('monthly_limit', 0) or 0
            used = extra.get('used_credits', 0) or 0
            currency = extra.get('currency')
            decimal_places = extra.get('decimal_places')
            env_vars['USAGE_MONITOR_EXTRA_USED'] = format_credits(used, currency, decimal_places)
            if limit > 0:
                env_vars['USAGE_MONITOR_EXTRA_LIMIT'] = format_credits(limit, currency, decimal_places)

        return env_vars

    def _run_startup_command(self, data: dict[str, Any]) -> None:
        """Run the user-configured startup command if set.

        Fires once after the first successful API update.  Receives the
        full quota state so the command can decide what to do (e.g. only
        ping Claude when no five-hour session is active).
        """
        if not ON_STARTUP_COMMAND:
            return

        env_vars = {'USAGE_MONITOR_EVENT': 'startup', **self._quota_snapshot_env(data)}
        run_event_command(ON_STARTUP_COMMAND, env_vars)

    def _run_quick_action(self) -> None:
        """Run the user-configured quick action if set.

        Receives the latest quota state (from the most recent successful
        update) so the command can act on current usage, mirroring the
        startup command's environment.  The quick action is user-driven, so
        a command that exits with a non-zero code surfaces its
        stderr in an error dialog (``capture_output``) instead of failing
        silently - unlike the automatic reset/threshold/startup commands.
        The dialog is limited to failures right after the launch
        (``report_late_failures=False``): this command usually starts an app
        the user keeps open, and its exit code once that app closes says
        nothing about the command being configured correctly.
        """
        if not QUICK_ACTION_COMMAND:
            return

        env_vars = {'USAGE_MONITOR_EVENT': 'quick_action', **self._quota_snapshot_env(self._last_response)}
        run_event_command(QUICK_ACTION_COMMAND, env_vars, capture_output=True, report_late_failures=False)

    def _run_reset_command(
        self, variant: str, pct: float, prev_pct: float, *, data: dict[str, Any], entry: dict[str, Any],
    ) -> None:
        """Run the user-configured reset command if set."""
        if not ON_RESET_COMMAND:
            return

        pct_5h = (data.get('five_hour') or {}).get('utilization', 0) or 0
        pct_7d = (data.get('seven_day') or {}).get('utilization', 0) or 0
        run_event_command(ON_RESET_COMMAND, {
            'USAGE_MONITOR_EVENT': 'reset',
            'USAGE_MONITOR_VARIANT': variant,
            'USAGE_MONITOR_UTILIZATION': str(round(pct)),
            'USAGE_MONITOR_PREV_UTILIZATION': str(round(prev_pct)),
            'USAGE_MONITOR_UTILIZATION_FIVE_HOUR': str(round(pct_5h)),
            'USAGE_MONITOR_UTILIZATION_SEVEN_DAY': str(round(pct_7d)),
            'USAGE_MONITOR_RESETS_AT': entry.get('resets_at') or '',
            'USAGE_MONITOR_TITLE': T['notify_reset_title'],
            'USAGE_MONITOR_MESSAGE': T['notify_reset'],
        })

    def _run_threshold_command(
        self, variant: str, pct: float | None, threshold: float,
        entry: dict[str, Any], title: str, message: str,
        *, extra_used: str = '', extra_limit: str = '',
    ) -> None:
        """Run the user-configured threshold command if set.

        Skipped on the first update (before ``_first_update_done`` is set)
        so that already-exceeded thresholds at app startup do not trigger
        commands.  Notifications still fire - commands react to *events*,
        not *state*.

        ``pct`` is None for spend-amount alerts, which have no utilization
        percentage; ``USAGE_MONITOR_UTILIZATION`` is omitted from the
        environment in that case.
        """
        if not ON_THRESHOLD_COMMAND or not self._first_update_done:
            return

        env_vars = {
            'USAGE_MONITOR_EVENT': 'threshold',
            'USAGE_MONITOR_VARIANT': variant,
        }
        if pct is not None:
            env_vars['USAGE_MONITOR_UTILIZATION'] = str(round(pct))
        env_vars.update({
            'USAGE_MONITOR_THRESHOLD': str(round(threshold)),
            'USAGE_MONITOR_RESETS_AT': entry.get('resets_at') or '',
            'USAGE_MONITOR_TITLE': title,
            'USAGE_MONITOR_MESSAGE': message,
        })
        if extra_used:
            env_vars['USAGE_MONITOR_EXTRA_USED'] = extra_used
        if extra_limit:
            env_vars['USAGE_MONITOR_EXTRA_LIMIT'] = extra_limit

        run_event_command(ON_THRESHOLD_COMMAND, env_vars)

    # Polling

    def _reset_offsets(self) -> list[float]:
        """Return seconds until each known quota reset, negative once it has passed."""
        now = datetime.now(timezone.utc)
        offsets = []
        for entry in self._last_response.values():
            if not isinstance(entry, dict) or not entry.get('resets_at'):
                continue
            try:
                # The subtraction stays inside the guard: a timestamp without a
                # UTC offset parses fine and only fails when subtracted.
                reset_time = datetime.fromisoformat(entry['resets_at'])
                offsets.append((reset_time - now).total_seconds())
            except Exception:
                continue

        return offsets

    def _seconds_until_next_reset(self) -> float | None:
        """Return seconds until the earliest upcoming quota reset, or None."""
        upcoming = [seconds for seconds in self._reset_offsets() if seconds > 0]

        return min(upcoming) if upcoming else None

    def _reset_overdue(self) -> bool:
        """Return whether a quota reset has passed without the API confirming it.

        A confirmed reset carries a new reset timestamp, or none at all while
        no window is active.  A timestamp still in the past means the
        confirming poll has not seen the reset yet - server-side propagation,
        or a fetch that failed - and that retry must not be delayed by the
        reduced away cadence.
        """
        return any(seconds <= 0 for seconds in self._reset_offsets())

    def _account_switched(self) -> bool:
        """Return whether the current credentials belong to a different account.

        Probes the account profile with the token now in the credentials
        file (bypassing the 429 backoff, since a freshly selected account
        cannot be the source of that rate limit) and compares its UUID
        against the last seen one.  Returns False until a baseline UUID is
        known, so the first successful update is never taken for a switch.
        """
        if self._prev_account_uuid is None:
            return False

        self.cache.ensure_profile(bypass_rate_limit=True)
        profile = self.cache.profile
        current_uuid = (profile.get('account') or {}).get('uuid') if isinstance(profile, dict) else None

        return current_uuid is not None and current_uuid != self._prev_account_uuid

    def _reset_aligned_poll_target(self, next_reset: float) -> float:
        """Return the absolute time for a poll landing just after a reset.

        Clamped to the cache cooldown (``last_success_time + POLL_FAST``) so
        the confirming poll never fires before a fresh fetch is permitted.

        Parameters
        ----------
        next_reset : float
            Seconds until the upcoming reset.
        """
        target = time.time() + next_reset + RESET_BUFFER
        last = self.cache.last_success_time
        if last is not None:
            target = max(target, last + POLL_FAST)

        return target

    def _safe_poll_target(self, target: float) -> float:
        """Move a poll target off a slot that would delay the reset poll.

        A fetch in the danger window (the last ``POLL_FAST - RESET_BUFFER``
        seconds before a reset) consumes the cache cooldown, so the confirming
        poll would overshoot the reset; a target past the reset-aligned slot
        delays that poll directly.  Both fall back to the aligned slot.

        Parameters
        ----------
        target : float
            Candidate poll time (``time.time()`` epoch).
        """
        next_reset = self._seconds_until_next_reset()
        if next_reset is None:
            return target

        reset_epoch = time.time() + next_reset
        aligned = self._reset_aligned_poll_target(next_reset)
        if target > aligned or reset_epoch - (POLL_FAST - RESET_BUFFER) < target < reset_epoch:
            return aligned

        return target

    def _base_poll_interval(self) -> int:
        """Return the cadence interval implied by the current data state."""
        data = self._last_response

        if data.get('rate_limited'):
            remaining = self.cache.rate_limit_remaining
            return max(math.ceil(remaining), POLL_INTERVAL) if remaining > 0 else POLL_INTERVAL

        if 'error' in data:
            return POLL_ERROR

        if self._fast_polls_remaining > 0:
            return POLL_FAST

        return POLL_INTERVAL

    def _calculate_poll_interval(self) -> int:
        """Determine the next poll interval based on current state.

        While nobody is watching, the cadence drops to ``IDLE_INTERVAL``
        instead of stopping, and reset alignment is applied on top either way -
        so a quota reset is still picked up on time on a locked machine.  A
        reset the API has not confirmed yet keeps the normal cadence.

        Returns
        -------
        int
            Seconds to wait before the next poll.
        """
        interval = self._base_poll_interval()

        if self._polling_throttled() and not self._reset_overdue():
            interval = max(interval, IDLE_INTERVAL)

        # Align the next poll around an imminent reset for faster feedback.
        # The confirming poll is placed just after the reset; a follow-up uses
        # POLL_FAST regardless of user activity (quota was likely exhausted).
        next_reset = self._seconds_until_next_reset()
        interval, aligned = _align_to_reset(interval, next_reset)
        if aligned:
            self._fast_polls_remaining = max(self._fast_polls_remaining, 2)

        return interval

    def _is_user_away(self) -> bool:
        """Return True if the user is idle or the workstation is locked."""
        if is_workstation_locked():
            return True
        return IDLE_PAUSE > 0 and get_idle_seconds() >= IDLE_PAUSE

    def _screen_hidden(self) -> bool:
        """Return True if the lock screen or a screensaver covers the display."""
        return is_workstation_locked() or is_screensaver_running()

    def _polling_throttled(self) -> bool:
        """Return whether polling runs on the reduced away cadence.

        An open popup holds the normal cadence: its numbers are on screen and
        would go stale in front of the user, however long ago the last mouse
        move was.  A covered screen overrides that - nobody reads a popup
        behind the lock screen or a screensaver.
        """
        if self._popup_open and not self._screen_hidden():
            return False

        return self._is_user_away()

    def poll_loop(self) -> None:
        """Poll the API in a loop with adaptive intervals.

        While the user is away the cadence drops to ``IDLE_INTERVAL``, but
        polling never stops: quota resets stay aligned and the account-switch
        watcher keeps running on an unattended machine.  Coming back - or
        opening the popup - pulls the next poll back to the normal cadence.
        """
        self.cache.ensure_profile()
        force_next = False
        while self.running:
            # Read before the update, not after: an account switch that lands while the
            # request is in flight would otherwise already be part of the token read
            # afterwards and never register as a change - leaving the previous account's
            # usage on screen until the next regular poll.
            token_seen = read_access_token()
            self.update(force=force_next)
            force_next = False
            interval = self._calculate_poll_interval()

            target = time.time() + interval
            self._next_poll_time = target
            last_success_seen = self.cache.last_success_time
            throttled_seen = self._polling_throttled()
            while self.running and time.time() < target:
                time.sleep(1)

                # React to a credentials token change between polls. A switch to
                # a different account forces an immediate refresh (bypassing the
                # cooldown) so the new account's usage shows right away. A token
                # change while the last fetch failed auth is retried at once so a
                # freshly refreshed token recovers usage and profile without
                # waiting out the error cadence or needing a restart.
                current_token = read_access_token()
                if current_token and current_token != token_seen:
                    token_seen = current_token
                    if self._account_switched():
                        force_next = True
                        break
                    if self._last_response.get('auth_error'):
                        break

                # Re-anchor the wait target after a backward clock jump -
                # otherwise the poll would stall until the wall clock catches
                # up with the pre-jump target, potentially for hours.  The
                # bound leaves room for reset-aligned targets, which may lie
                # up to roughly POLL_FAST past a normal interval.
                if target - time.time() > interval + POLL_FAST:
                    target = time.time() + interval
                    self._next_poll_time = target

                # If another thread (popup) fetched successfully, push the next
                # poll a full interval past that fetch to avoid a redundant one.
                # Only react to an actual new fetch (last_success advanced), not
                # to a target the away-return path lowered on its own.
                lst = self.cache.last_success_time
                if lst is not None and (last_success_seen is None or lst > last_success_seen):
                    last_success_seen = lst
                    target = self._safe_poll_target(max(target, lst + interval))
                    self._next_poll_time = target

                # Show notifications deferred while the user was away as soon
                # as they are present.
                if self._deferred_notifications and not self._is_user_away():
                    self._flush_deferred_notifications()

                # The user came back, or opened the popup: the reduced away
                # cadence no longer applies, so pull the next poll back to what
                # the normal cadence would have scheduled - immediately when
                # that interval has already elapsed since the last fetch.  The
                # target only ever moves closer, and never onto a slot that
                # would delay the reset-confirming poll.
                throttled_now = self._polling_throttled()
                if throttled_seen and not throttled_now:
                    interval = self._calculate_poll_interval()
                    lst = self.cache.last_success_time
                    resumed = time.time() if lst is None else lst + interval
                    target = min(target, self._safe_poll_target(resumed))
                    self._next_poll_time = target
                throttled_seen = throttled_now

    # Lifecycle

    def _on_icon_ready(self, icon: Any) -> None:
        """Called by pystray in a separate thread once the tray icon is set up."""
        try:
            icon.visible = True
            if autostart_supported():
                sync_autostart_path()
            if not api_headers():
                icon.notify(f"{T['warn_no_token']}\n{T['warn_login']}", T['popup_title'])
            threading.Thread(target=watch_theme_change, args=(self._on_theme_changed,), daemon=True).start()
            self.poll_loop()
        except Exception:
            crash_log(traceback.format_exc())

    def run(self) -> None:
        self.icon.run(setup=self._on_icon_ready)


def crash_log(msg: str) -> None:
    """Show a crash message box (for windowless EXE builds)."""
    show_error_box(msg, 'Usage Monitor for Claude - Error')

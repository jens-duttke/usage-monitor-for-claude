"""
Application
=============

System tray application class with adaptive polling and event handling.
"""
from __future__ import annotations

import ctypes
import math
import sys
import threading
import time
import traceback
import webbrowser
from datetime import datetime, timedelta, timezone
from typing import Any

import pystray  # type: ignore[import-untyped]  # no type stubs available

from .api import api_headers, read_access_token
from .autostart import is_autostart_enabled, set_autostart, sync_autostart_path
from .cache import UsageCache
from .claude_cli import PROJECT_URL
from .command import run_event_command
from .idle import get_idle_seconds, is_workstation_locked
from .instance_id import effective_config_dir, is_default_config_dir
from .settings import (
    ALERT_TIME_AWARE, ALERT_TIME_AWARE_BELOW, ICON_FIELDS, IDLE_PAUSE, NOTIFY_CLAUDE_UPDATE,
    ON_DOUBLE_CLICK_COMMAND, ON_RESET_COMMAND, ON_STARTUP_COMMAND, ON_THRESHOLD_COMMAND,
    POLL_ERROR, POLL_FAST, POLL_FAST_EXTRA, POLL_INTERVAL, get_alert_thresholds,
)
from .formatting import elapsed_pct, field_period, format_credits, format_tooltip, parse_field_name, popup_label
from .i18n import T
from .popup import UsagePopup
from .tray_icon import create_icon_image, create_status_image, taskbar_uses_light_theme, watch_theme_change

__all__ = ['UsageMonitorForClaude', 'crash_log']

# Seconds after a reset at which to place the confirming poll.  A small buffer
# absorbs minor timing differences (clocks, caches, server-side propagation).
RESET_BUFFER = 5

# Win32 tray mouse messages, delivered by the shell as the WM_NOTIFY lParam.
# pystray natively acts only on WM_LBUTTONUP; WM_LBUTTONDBLCLK drives the
# optional double-click command.
WM_LBUTTONUP = 0x0202
WM_LBUTTONDBLCLK = 0x0203


def _future_iso(**kwargs: float) -> str:
    """Return an ISO 8601 timestamp offset from now by the given timedelta kwargs."""
    return (datetime.now(timezone.utc) + timedelta(**kwargs)).isoformat()


def _align_to_reset(interval: int, next_reset: float | None) -> tuple[int, bool]:
    """Shift the next poll so the confirming poll lands just after the reset.

    Every returned interval stays at or above ``POLL_FAST`` (the cache
    cooldown), so the reset is caught without polling faster.  The poll before
    the reset is pulled forward to ``POLL_FAST`` seconds before it; from there
    the confirming poll lands ``RESET_BUFFER`` seconds after the reset.  When
    the current poll is already too close to pull the previous one forward
    without breaking the cooldown, the confirming poll is committed directly.

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
        # the window start (POLL_FAST before the reset); if that is too close
        # to keep POLL_FAST spacing, commit to the confirming poll directly.
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
        self._idle_reset_pending = False
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
                pystray.Menu.SEPARATOR,
                pystray.MenuItem(
                    T['autostart'], self.on_toggle_autostart,
                    checked=lambda item: is_autostart_enabled(),
                    visible=getattr(sys, 'frozen', False),
                ),
                pystray.MenuItem(T['test_commands'], pystray.Menu(
                    pystray.MenuItem(T['test_reset_5h'], self.on_test_reset_5h, enabled=bool(ON_RESET_COMMAND)),
                    pystray.MenuItem(T['test_reset_7d'], self.on_test_reset_7d, enabled=bool(ON_RESET_COMMAND)),
                    pystray.MenuItem(T['test_threshold_5h'], self.on_test_threshold_5h, enabled=bool(ON_THRESHOLD_COMMAND)),
                    pystray.MenuItem(T['test_threshold_7d'], self.on_test_threshold_7d, enabled=bool(ON_THRESHOLD_COMMAND)),
                    pystray.MenuItem(T['test_startup'], self.on_test_startup, enabled=bool(ON_STARTUP_COMMAND)),
                    pystray.MenuItem(T['test_double_click'], self.on_test_double_click, enabled=bool(ON_DOUBLE_CLICK_COMMAND)),
                ), enabled=bool(ON_RESET_COMMAND or ON_STARTUP_COMMAND or ON_THRESHOLD_COMMAND or ON_DOUBLE_CLICK_COMMAND)),
                pystray.MenuItem(T['restart'], self.on_restart),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem(T['menu_project'], self.on_open_project),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem(T['quit'], self.on_quit),
            ),
        )

        # Double-click support.  pystray fires the default action (the popup) on
        # every left-button release, so a double-click command requires deferring
        # that single click by the system double-click interval and cancelling it
        # when a second click arrives.  Only wired up when a command is
        # configured, so the default single-click behavior is otherwise untouched.
        self._click_lock = threading.Lock()
        self._single_click_timer: threading.Timer | None = None
        self._swallow_next_up = False
        if ON_DOUBLE_CLICK_COMMAND:
            self._double_click_seconds = ctypes.windll.user32.GetDoubleClickTime() / 1000.0
            self._install_double_click_handler()

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

    def on_test_double_click(self, icon: Any = None, item: Any = None) -> None:
        run_event_command(ON_DOUBLE_CLICK_COMMAND, {
            'USAGE_MONITOR_EVENT': 'double_click',
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

    def _install_double_click_handler(self) -> None:
        """Replace pystray's tray-message handler with a double-click-aware one.

        Locates the ``WM_NOTIFY`` entry in pystray's handler table by identity
        and swaps in :meth:`_on_tray_message`, keeping the original handler for
        right-click and every other message.
        """
        self._pystray_on_notify = self.icon._on_notify
        for code, handler in self.icon._message_handlers.items():
            if handler == self._pystray_on_notify:
                self.icon._message_handlers[code] = self._on_tray_message
                break

    def _on_tray_message(self, wparam: int, lparam: int) -> int:
        """Dispatch a tray mouse message, adding double-click handling.

        A left-button release schedules the popup after the double-click
        interval; a double-click cancels that pending popup and runs the
        configured command instead.  The trailing release that follows every
        double-click is swallowed so it does not schedule a second popup.  All
        other messages (right-click menu, etc.) fall through to pystray's own
        handler.
        """
        if lparam == WM_LBUTTONUP:
            with self._click_lock:
                if self._swallow_next_up:
                    self._swallow_next_up = False
                    return 0
                if self._single_click_timer is not None:
                    self._single_click_timer.cancel()
                self._single_click_timer = threading.Timer(self._double_click_seconds, self._fire_single_click)
                self._single_click_timer.daemon = True
                self._single_click_timer.start()
            return 0

        if lparam == WM_LBUTTONDBLCLK:
            with self._click_lock:
                self._swallow_next_up = True
                if self._single_click_timer is not None:
                    self._single_click_timer.cancel()
                    self._single_click_timer = None
            self._run_double_click_command()
            return 0

        return self._pystray_on_notify(wparam, lparam)

    def _fire_single_click(self) -> None:
        """Open the popup once the double-click interval passes without a second click.

        Bails out if the timer was cleared meanwhile - a double-click that
        arrived right as the timer fired cancels it here, so the popup never
        opens for a completed double-click.
        """
        with self._click_lock:
            if self._single_click_timer is None:
                return
            self._single_click_timer = None
        self.on_show_popup()

    # Tray rendering

    def _render_tray(self) -> None:
        """Re-render tray icon and tooltip from current state."""
        data = self._last_response
        if 'error' in data:
            self.icon.icon = create_status_image('C!' if data.get('auth_error') else '!', self._light_taskbar)
        else:
            top_field, top_mode = ICON_FIELDS[0].split(':', 1) if ':' in ICON_FIELDS[0] else (ICON_FIELDS[0], 'utilization')
            bottom_field, bottom_mode = ICON_FIELDS[1].split(':', 1) if ':' in ICON_FIELDS[1] else (ICON_FIELDS[1], 'utilization')
            top_entry = data.get(top_field) or {}
            bottom_entry = data.get(bottom_field) or {}
            pct_top = top_entry.get('utilization', 0) or 0
            pct_bottom = bottom_entry.get('utilization', 0) or 0
            top_period = field_period(top_field)
            bottom_period = field_period(bottom_field)
            time_pct_top = elapsed_pct(top_entry.get('resets_at', ''), top_period) if top_period else None
            time_pct_bottom = elapsed_pct(bottom_entry.get('resets_at', ''), bottom_period) if bottom_period else None
            extra = data.get('extra_usage') or {}
            extra_limit = extra.get('monthly_limit') or 0
            extra_used = extra.get('used_credits') or 0
            extra_usage_available = bool(extra.get('is_enabled')) and extra_limit > 0 and extra_used < extra_limit
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

        # Detect account switch: re-fetch profile if the access token changed, then compare UUIDs.
        # When the user runs 'claude auth login', the token changes and the next profile fetch
        # returns a different account UUID, preventing a false quota-reset notification.
        self.cache.ensure_profile()
        current_profile = self.cache.profile
        current_account_uuid = current_profile.get('account', {}).get('uuid') if isinstance(current_profile, dict) else None
        if self._prev_account_uuid is not None and current_account_uuid is not None and current_account_uuid != self._prev_account_uuid:
            email = current_profile.get('account', {}).get('email', '')
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
                self._notify_or_defer('reset', T['notify_reset'], T['notify_reset_title'])

        # Run reset command on any detected usage drop (independent of notification threshold)
        for key, pct in quota_fields.items():
            prev = self._prev_utilization.get(key)
            if prev is not None and pct < prev:
                self._run_reset_command(key, pct, prev, data=result.data, entry=result.data.get(key, {}))
                self._idle_reset_pending = False

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
            self._deferred_notifications[category] = (message, title)
        else:
            self.icon.notify(message, title)

    def _flush_deferred_notifications(self) -> None:
        """Show all deferred notifications and clear the queue."""
        for message, title in self._deferred_notifications.values():
            self.icon.notify(message, title)
        self._deferred_notifications.clear()

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

        limit = extra.get('monthly_limit', 0) or 0
        if limit <= 0:
            return

        used = extra.get('used_credits', 0) or 0
        pct = used / limit * 100

        thresholds = get_alert_thresholds('extra_usage')
        if not thresholds:
            return

        exceeded = [t for t in thresholds if pct >= t]
        highest_exceeded = max(exceeded) if exceeded else 0
        last_notified = self._notified_thresholds.get('extra_usage', 0)

        if highest_exceeded > last_notified:
            title = T['notify_threshold_title']
            currency = extra.get('currency')
            decimal_places = extra.get('decimal_places')
            used_text = format_credits(used, currency, decimal_places)
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

    # Event commands

    def _quota_snapshot_env(self, data: dict[str, Any]) -> dict[str, str]:
        """Build environment variables describing the current quota state.

        Emits one ``USAGE_MONITOR_UTILIZATION_<FIELD>`` /
        ``USAGE_MONITOR_RESETS_AT_<FIELD>`` pair per detected quota field, plus
        ``USAGE_MONITOR_EXTRA_USED`` / ``USAGE_MONITOR_EXTRA_LIMIT`` when paid
        extra usage is enabled.  Shared by the startup and double-click commands.
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

    def _run_double_click_command(self) -> None:
        """Run the user-configured double-click command if set.

        Receives the latest quota state (from the most recent successful
        update) so the command can act on current usage, mirroring the
        startup command's environment.  A double-click is a user-driven
        action, so a command that exits with a non-zero code surfaces its
        stderr in an error dialog (``capture_output``) instead of failing
        silently - unlike the automatic reset/threshold/startup commands.
        """
        if not ON_DOUBLE_CLICK_COMMAND:
            return

        env_vars = {'USAGE_MONITOR_EVENT': 'double_click', **self._quota_snapshot_env(self._last_response)}
        run_event_command(ON_DOUBLE_CLICK_COMMAND, env_vars, capture_output=True)

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
            'USAGE_MONITOR_RESETS_AT': entry.get('resets_at', ''),
            'USAGE_MONITOR_TITLE': T['notify_reset_title'],
            'USAGE_MONITOR_MESSAGE': T['notify_reset'],
        })

    def _run_threshold_command(
        self, variant: str, pct: float, threshold: float,
        entry: dict[str, Any], title: str, message: str,
        *, extra_used: str = '', extra_limit: str = '',
    ) -> None:
        """Run the user-configured threshold command if set.

        Skipped on the first update (before ``_first_update_done`` is set)
        so that already-exceeded thresholds at app startup do not trigger
        commands.  Notifications still fire - commands react to *events*,
        not *state*.
        """
        if not ON_THRESHOLD_COMMAND or not self._first_update_done:
            return

        env_vars = {
            'USAGE_MONITOR_EVENT': 'threshold',
            'USAGE_MONITOR_VARIANT': variant,
            'USAGE_MONITOR_UTILIZATION': str(round(pct)),
            'USAGE_MONITOR_THRESHOLD': str(round(threshold)),
            'USAGE_MONITOR_RESETS_AT': entry.get('resets_at', ''),
            'USAGE_MONITOR_TITLE': title,
            'USAGE_MONITOR_MESSAGE': message,
        }
        if extra_used:
            env_vars['USAGE_MONITOR_EXTRA_USED'] = extra_used
        if extra_limit:
            env_vars['USAGE_MONITOR_EXTRA_LIMIT'] = extra_limit

        run_event_command(ON_THRESHOLD_COMMAND, env_vars)

    # Polling

    def _seconds_until_next_reset(self) -> float | None:
        """Return seconds until the earliest upcoming quota reset, or None."""
        now = datetime.now(timezone.utc)
        earliest = None
        for key, entry in self._last_response.items():
            if not isinstance(entry, dict) or not entry.get('resets_at'):
                continue
            try:
                reset_time = datetime.fromisoformat(entry['resets_at'])
                seconds = (reset_time - now).total_seconds()
                if seconds > 0 and (earliest is None or seconds < earliest):
                    earliest = seconds
            except Exception:
                continue

        return earliest

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
        current_uuid = profile.get('account', {}).get('uuid') if isinstance(profile, dict) else None

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

    def _calculate_poll_interval(self) -> int:
        """Determine the next poll interval based on current state.

        Returns
        -------
        int
            Seconds to wait before the next poll.
        """
        data = self._last_response

        if data.get('rate_limited'):
            remaining = self.cache.rate_limit_remaining
            interval = max(math.ceil(remaining), POLL_INTERVAL) if remaining > 0 else POLL_INTERVAL
        elif 'error' in data:
            interval = POLL_ERROR
        elif self._fast_polls_remaining > 0:
            interval = POLL_FAST
        else:
            interval = POLL_INTERVAL

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

    def _wait_for_activity(self, until: float | None = None) -> None:
        """Block until user activity resumes or the app is stopping.

        Parameters
        ----------
        until : float | None
            Optional deadline (``time.time()`` epoch).  When set, the
            wait ends even if the user is still away, allowing a
            time-critical poll (e.g. quota reset command) to proceed.
        """
        while self.running and self._is_user_away():
            if until is not None and time.time() >= until:
                break
            time.sleep(2)

    def poll_loop(self) -> None:
        """Poll the API in a loop with adaptive intervals.

        Pauses polling when the user is idle or the workstation is
        locked.  On resume, polls immediately if the regular interval
        has elapsed since the last successful fetch.
        """
        self.cache.ensure_profile()
        force_next = False
        while self.running:
            self.update(force=force_next)
            force_next = False
            interval = self._calculate_poll_interval()

            target = time.time() + interval
            self._next_poll_time = target
            last_success_seen = self.cache.last_success_time
            token_seen = read_access_token()
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

                # If another thread (popup) fetched successfully, push the next
                # poll a full interval past that fetch to avoid a redundant one.
                # Only react to an actual new fetch (last_success advanced), not
                # to a target the idle-return path lowered on its own.
                lst = self.cache.last_success_time
                if lst is not None and (last_success_seen is None or lst > last_success_seen):
                    last_success_seen = lst
                    new_target = max(target, lst + interval)
                    # Never let that push move the poll past a reset-aligned slot,
                    # so the confirming poll still lands just after the reset
                    # instead of a full interval later.
                    next_reset = self._seconds_until_next_reset()
                    if next_reset is not None and next_reset < interval:
                        new_target = min(new_target, self._reset_aligned_poll_target(next_reset))
                    target = new_target
                    self._next_poll_time = target

                # Pause polling while the user is away.
                # Regular polling stops entirely during idle/lock.
                # The only exception: when on_reset_command is configured
                # and a quota reset is due, the idle pause is interrupted
                # so the command fires on time.  The flag
                # _idle_reset_pending keeps polling at POLL_INTERVAL
                # until the reset is actually confirmed (usage drop) -
                # this covers server-side delays and transient network
                # errors.  The flag is cleared when update() detects the
                # drop, or when the user returns (they'll see it anyway).
                if self._is_user_away():
                    reset_deadline = None
                    if ON_RESET_COMMAND:
                        next_reset = self._seconds_until_next_reset()
                        if next_reset is not None:
                            reset_deadline = time.time() + next_reset + RESET_BUFFER
                            self._idle_reset_pending = True
                        elif self._idle_reset_pending:
                            reset_deadline = time.time() + POLL_INTERVAL

                    self._wait_for_activity(until=reset_deadline)

                    if reset_deadline is not None and self._is_user_away():
                        # Woke up for a reset while still idle - poll once
                        break

                    # User returned - show any notifications deferred
                    # during idle and poll immediately if interval elapsed.
                    # _idle_reset_pending is intentionally kept: if the
                    # user locks again before a successful poll confirms
                    # the reset (e.g. network was down), idle polling
                    # must resume.  The flag is only cleared by update()
                    # when a usage drop is actually detected.
                    self._flush_deferred_notifications()
                    lst = self.cache.last_success_time
                    if lst is None:
                        continue

                    next_reset = self._seconds_until_next_reset()
                    if next_reset is not None and next_reset < POLL_FAST:
                        # Returned within the cooldown window before a reset:
                        # polling now would advance last_success into that window
                        # and force the confirming poll to overshoot.  Realign the
                        # wait to just after the reset and keep waiting for it.
                        target = self._reset_aligned_poll_target(next_reset)
                        self._next_poll_time = target
                        continue

                    if time.time() - lst >= interval:
                        break

    # Lifecycle

    def _on_icon_ready(self, icon: Any) -> None:
        """Called by pystray in a separate thread once the tray icon is set up."""
        try:
            icon.visible = True
            if getattr(sys, 'frozen', False):
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
    ctypes.windll.user32.MessageBoxW(0, msg[:2000], 'Usage Monitor for Claude - Error', 0x10)

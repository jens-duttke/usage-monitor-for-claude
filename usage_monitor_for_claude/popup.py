"""
Popup Window
=============

Dark-themed HTML popup window showing account info and usage bars.
Uses pywebview with Edge WebView2 for smooth CSS transitions and
flexible layout.
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes
import json
import threading
import time
import webbrowser
from pathlib import Path
from typing import TYPE_CHECKING, Any

import webview  # type: ignore[import-untyped]  # no type stubs available

from .claude_cli import CHANGELOG_URL, find_installations
from .formatting import PERIOD_5H, PERIOD_7D, elapsed_pct, format_credits, format_status, midnight_positions, time_until
from .i18n import T
from .settings import BAR_BG, BAR_FG, BAR_FG_WARN, BAR_MARKER, BG, FG, FG_DIM, FG_HEADING, FG_LINK

_POPUP_DIR = Path(__file__).parent / 'popup'
_BASELINE_DPI = 96

__all__ = ['UsagePopup']

if TYPE_CHECKING:
    from .app import UsageMonitorForClaude
    from .cache import CacheSnapshot


# ---------------------------------------------------------------------------
# Data helpers
# ---------------------------------------------------------------------------

def _usage_entries(usage: dict[str, Any]) -> list[tuple[str, dict[str, Any] | None, int]]:
    """Return the list of usage entry tuples from the given usage data."""
    return [
        (T['session'], usage.get('five_hour'), PERIOD_5H),
        (T['weekly'], usage.get('seven_day'), PERIOD_7D),
        (T['weekly_sonnet'], usage.get('seven_day_sonnet'), PERIOD_7D),
        (T['weekly_opus'], usage.get('seven_day_opus'), PERIOD_7D),
    ]


def _snapshot_to_dict(snap: CacheSnapshot, installations: list[dict[str, str]] | None = None) -> dict[str, Any]:
    """Convert a CacheSnapshot to a JSON-serializable dict for the popup JS.

    Parameters
    ----------
    snap : CacheSnapshot
        Immutable snapshot of the cache state.
    installations : list or None
        Pre-computed installation list, or None to detect now.
    """
    # Profile - truthiness check (not `is not None`): hides the account section when the API
    # returns an empty or incomplete response, instead of rendering empty Email/Plan fields.
    profile = None
    if snap.profile:
        account = snap.profile.get('account', {})
        org = snap.profile.get('organization', {})
        profile = {
            'email': account.get('email', ''),
            'plan': org.get('organization_type', '').replace('_', ' ').title(),
        }

    # Usage bars
    usage = []
    if snap.usage:
        for label, entry, period in _usage_entries(snap.usage):
            if not entry or entry.get('utilization') is None:
                continue
            pct = entry.get('utilization', 0) or 0
            resets_at = entry.get('resets_at', '')
            time_pct = elapsed_pct(resets_at, period)
            warn = time_pct is not None and pct > time_pct
            marker_rel = max(0.0, min(1.0, time_pct / 100)) if time_pct is not None else None

            usage.append({
                'label': label,
                'pct_text': f'{pct:.0f}%',
                'fill_pct': max(0.0, min(1.0, pct / 100)),
                'warn': warn,
                'reset_text': time_until(resets_at) if resets_at else '',
                'midnights': midnight_positions(resets_at, period),
                'marker_rel': marker_rel,
            })

    # Extra usage
    extra = None
    if snap.usage:
        extra_data = snap.usage.get('extra_usage')
        if extra_data and extra_data.get('is_enabled'):
            limit = extra_data.get('monthly_limit', 0) or 0
            if limit > 0:
                used = extra_data.get('used_credits', 0) or 0
                pct = used / limit * 100
                extra = {
                    'pct_text': f'{pct:.0f}%',
                    'fill_pct': max(0.0, min(1.0, pct / 100)),
                    'spent_text': T['extra_usage_spent'].format(
                        used=format_credits(used), limit=format_credits(limit),
                    ),
                }

    # Installations
    if installations is None:
        installations = [{'name': i.name, 'version': i.version} for i in find_installations()]

    # Status
    if not snap.usage:
        if snap.last_error:
            status = {'text': snap.last_error[:120], 'is_error': True}
        else:
            status = {'text': T['status_refreshing'], 'is_error': False}
    else:
        text, has_error = format_status(snap.last_success_time, snap.refreshing, snap.last_error)
        status = {'text': text, 'is_error': has_error}

    return {
        'profile': profile,
        'usage': usage,
        'extra': extra,
        'installations': installations,
        'status': status,
    }


def _init_config(snap: CacheSnapshot) -> dict[str, Any]:
    """Build the config object passed to JS ``init()`` after the page loads."""
    return {
        'colors': {
            'bg': BG, 'fg': FG, 'fg_dim': FG_DIM, 'fg_heading': FG_HEADING, 'fg_link': FG_LINK,
            'bar_bg': BAR_BG, 'bar_fg': BAR_FG, 'bar_fg_warn': BAR_FG_WARN, 'bar_marker': BAR_MARKER,
        },
        't': {
            'title': T['popup_title'], 'account': T['account'], 'email': T['email'], 'plan': T['plan'],
            'usage': T['usage'], 'extra_usage': T['extra_usage'],
            'claude_code': T['claude_code'], 'changelog': T['changelog'],
        },
        'data': _snapshot_to_dict(snap),
    }


# ---------------------------------------------------------------------------
# JS-callable API
# ---------------------------------------------------------------------------

class _PopupApi:
    """Methods exposed to JavaScript via pywebview's JS bridge."""

    def __init__(self, popup: UsagePopup) -> None:
        self._popup = popup

    def close(self) -> None:
        self._popup._close()

    def open_url(self) -> None:
        webbrowser.open(CHANGELOG_URL)

    def report_height(self, height: int) -> None:
        """Called by JS ResizeObserver when content height changes."""
        if height and height != self._popup._last_height:
            self._popup._last_height = height
            self._popup._resize_and_position(height)


# ---------------------------------------------------------------------------
# Popup window
# ---------------------------------------------------------------------------

class UsagePopup:
    """Dark-themed HTML popup window showing account info and usage bars."""

    WIDTH = 340
    _CHECK_MS = 2000

    def __init__(self, app: UsageMonitorForClaude) -> None:
        """Create and display a popup window with usage details.

        Blocks the calling thread until the window is closed.
        Requires ``webview.start()`` to be running on the main thread.

        Parameters
        ----------
        app : UsageMonitorForClaude
            Parent application providing ``cache`` for data access.
        """
        self.app = app
        self._running = True
        self._closed = threading.Event()
        self._popup_hwnd = 0
        initial_height = 400
        self._last_height = initial_height
        snap = app.cache.snapshot
        self._last_version = snap.version

        api = _PopupApi(self)

        # create_window uses logical pixels (WinForms auto-scales via
        # AutoScaleMode.Dpi), but _tray_position needs the resulting
        # physical size to compute the correct screen position.
        dpi = ctypes.windll.user32.GetDpiForSystem()
        scale = dpi / _BASELINE_DPI
        x, y = self._tray_position(int(self.WIDTH * scale), int(initial_height * scale))

        self._window = webview.create_window(
            '', url=str(_POPUP_DIR / 'popup.html'),
            width=self.WIDTH, height=initial_height,
            x=x, y=y,
            resizable=False, frameless=True, shadow=False,
            easy_drag=False,
            on_top=True, hidden=True,
            background_color=BG,
            js_api=api,
        )
        self._shown = False
        self._window.events.loaded += self._on_loaded
        self._window.events.closed += self._on_window_closed
        threading.Thread(target=self._dismiss_watch, daemon=True).start()
        self._closed.wait()

    def _on_loaded(self) -> None:
        """Inject config, show the window, and start the update loop."""
        config = _init_config(self.app.cache.snapshot)
        self._window.evaluate_js(f'init({json.dumps(config)})')
        self._window.show()
        self._popup_hwnd = self._window.native.Handle.ToInt32()
        self._shown = True
        threading.Thread(target=self._update_loop, daemon=True).start()

    def _dismiss_watch(self) -> None:
        """Close the popup on click-outside, Escape, or focus change.

        Combines three Win32 mechanisms in a single message pump:

        * ``WH_MOUSE_LL`` - catches clicks outside the popup bounds
        * ``WH_KEYBOARD_LL`` - catches Escape even without focus
        * ``EVENT_SYSTEM_FOREGROUND`` - catches Alt-Tab, browser open, etc.

        The foreground hook uses a short delay to ride out the brief
        focus bounce that WebView2 causes between its host and renderer
        process on every click inside the content area.
        """
        this_thread = ctypes.windll.kernel32.GetCurrentThreadId()
        WM_QUIT = 0x0012

        def _post_quit() -> None:
            if self._shown:
                ctypes.windll.user32.PostThreadMessageW(this_thread, WM_QUIT, 0, 0)

        # -- Shared argtypes for CallNextHookEx --
        _call_next = ctypes.windll.user32.CallNextHookEx
        _call_next.argtypes = [ctypes.wintypes.HANDLE, ctypes.c_int, ctypes.wintypes.WPARAM, ctypes.wintypes.LPARAM]
        _call_next.restype = ctypes.c_long

        # -- Mouse hook: click outside popup bounds --
        class MSLLHOOKSTRUCT(ctypes.Structure):
            _fields_ = [('pt', ctypes.wintypes.POINT), ('mouseData', ctypes.wintypes.DWORD),
                         ('flags', ctypes.wintypes.DWORD), ('time', ctypes.wintypes.DWORD),
                         ('dwExtraInfo', ctypes.POINTER(ctypes.c_ulong))]

        @ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_int, ctypes.wintypes.WPARAM, ctypes.wintypes.LPARAM)
        def mouse_proc(code, wparam, lparam):
            if code >= 0 and wparam == 0x0201:  # WM_LBUTTONDOWN
                popup_hwnd = self._popup_hwnd
                if popup_hwnd:
                    rect = ctypes.wintypes.RECT()
                    ctypes.windll.user32.GetWindowRect(popup_hwnd, ctypes.byref(rect))
                    info = ctypes.cast(lparam, ctypes.POINTER(MSLLHOOKSTRUCT)).contents
                    if not (rect.left <= info.pt.x <= rect.right and rect.top <= info.pt.y <= rect.bottom):
                        _post_quit()
            return _call_next(None, code, wparam, lparam)

        # -- Keyboard hook: Escape key --
        class KBDLLHOOKSTRUCT(ctypes.Structure):
            _fields_ = [('vkCode', ctypes.wintypes.DWORD), ('scanCode', ctypes.wintypes.DWORD),
                         ('flags', ctypes.wintypes.DWORD), ('time', ctypes.wintypes.DWORD),
                         ('dwExtraInfo', ctypes.POINTER(ctypes.c_ulong))]

        @ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_int, ctypes.wintypes.WPARAM, ctypes.wintypes.LPARAM)
        def kb_proc(code, wparam, lparam):
            if code >= 0 and wparam == 0x0100:  # WM_KEYDOWN
                info = ctypes.cast(lparam, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
                if info.vkCode == 0x1B:  # VK_ESCAPE
                    _post_quit()
            return _call_next(None, code, wparam, lparam)

        # -- Foreground event with delayed check --
        WINEVENT_CALLBACK = ctypes.WINFUNCTYPE(
            None, ctypes.wintypes.HANDLE, ctypes.wintypes.DWORD, ctypes.wintypes.HWND,
            ctypes.wintypes.LONG, ctypes.wintypes.LONG, ctypes.wintypes.DWORD, ctypes.wintypes.DWORD,
        )

        _fg_timer: threading.Timer | None = None

        def _delayed_fg_check() -> None:
            """Check if focus is still outside the popup after the delay."""
            popup_hwnd = self._popup_hwnd
            if not popup_hwnd or not self._shown:
                return
            fg = ctypes.windll.user32.GetForegroundWindow()
            if fg == popup_hwnd:
                return
            if ctypes.windll.user32.IsChild(popup_hwnd, fg):
                return
            if ctypes.windll.user32.GetAncestor(fg, 3) == popup_hwnd:  # GA_ROOTOWNER
                return
            _post_quit()

        @WINEVENT_CALLBACK
        def fg_proc(_hook, _event, hwnd, _id_obj, _id_child, _thread, _time):
            nonlocal _fg_timer
            popup_hwnd = self._popup_hwnd
            if not popup_hwnd:
                return
            # Quick accept: focus moved to a child/owned window of our popup
            if ctypes.windll.user32.IsChild(popup_hwnd, hwnd):
                return
            if ctypes.windll.user32.GetAncestor(hwnd, 3) == popup_hwnd:  # GA_ROOTOWNER
                return
            # Delay the dismiss to ride out WebView2's focus bounce
            # between host and renderer process on content clicks.
            if _fg_timer is not None:
                _fg_timer.cancel()
            _fg_timer = threading.Timer(0.2, _delayed_fg_check)
            _fg_timer.daemon = True
            _fg_timer.start()

        mouse_hook = ctypes.windll.user32.SetWindowsHookExW(14, mouse_proc, None, 0)  # WH_MOUSE_LL
        kb_hook = ctypes.windll.user32.SetWindowsHookExW(13, kb_proc, None, 0)  # WH_KEYBOARD_LL
        # EVENT_SYSTEM_FOREGROUND with WINEVENT_SKIPOWNPROCESS
        fg_hook = ctypes.windll.user32.SetWinEventHook(0x0003, 0x0003, None, fg_proc, 0, 0, 0x0002)

        try:
            msg = ctypes.wintypes.MSG()
            while self._running and ctypes.windll.user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                pass
        finally:
            if _fg_timer is not None:
                _fg_timer.cancel()
            ctypes.windll.user32.UnhookWindowsHookEx(mouse_hook)
            ctypes.windll.user32.UnhookWindowsHookEx(kb_hook)
            ctypes.windll.user32.UnhookWinEvent(fg_hook)

        self._close()

    def _on_window_closed(self) -> None:
        self._running = False
        self._closed.set()

    def _close(self) -> None:
        self._running = False
        try:
            self._window.destroy()
        except Exception:
            pass
        self._closed.set()

    def _update_loop(self) -> None:
        """Poll for data changes and push updates to the popup."""
        cached_installations = [{'name': i.name, 'version': i.version} for i in find_installations()]
        while self._running:
            time.sleep(self._CHECK_MS / 1000)
            if not self._running:
                break
            try:
                snap = self.app.cache.snapshot
                if snap.version != self._last_version:
                    self._last_version = snap.version
                    cached_installations = [{'name': i.name, 'version': i.version} for i in find_installations()]
                data = _snapshot_to_dict(snap, installations=cached_installations)
                self._window.evaluate_js(f'updateData({json.dumps(data)})')
            except Exception:
                break

    def _tray_position(self, physical_width: int, physical_height: int) -> tuple[int, int]:
        """Calculate popup position near the system tray.

        Parameters
        ----------
        physical_width : int
            Actual window width in physical pixels.
        physical_height : int
            Actual window height in physical pixels.

        Returns
        -------
        tuple[int, int]
            Logical (x, y) coordinates for pywebview's ``move()``, which
            scales them back to physical internally.
        """
        work_area = ctypes.wintypes.RECT()
        ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(work_area), 0)

        dpi = ctypes.windll.user32.GetDpiForSystem()
        scale = dpi / _BASELINE_DPI

        margin = 12

        if work_area.left > 0:
            x = work_area.left + margin
        else:
            x = work_area.right - physical_width - margin

        if work_area.top > 0:
            y = work_area.top + margin
        else:
            y = work_area.bottom - physical_height - margin

        return int(x / scale), int(y / scale)

    def _resize_and_position(self, height: int) -> None:
        """Resize the window and reposition it near the system tray.

        pywebview's ``resize()`` calls ``SetWindowPos`` directly without
        DPI scaling, so it expects physical pixels.  The *height* from JS
        ``ResizeObserver`` is in CSS pixels, so we multiply by the system
        DPI factor.  ``move()`` scales internally, and ``_tray_position``
        already accounts for that.
        """
        dpi = ctypes.windll.user32.GetDpiForSystem()
        scale = dpi / _BASELINE_DPI
        physical_width = int(self.WIDTH * scale)
        physical_height = int(height * scale)
        self._window.resize(physical_width, physical_height)
        x, y = self._tray_position(physical_width, physical_height)
        self._window.move(x, y)

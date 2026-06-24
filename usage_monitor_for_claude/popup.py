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

from . import __version__
from .claude_cli import CHANGELOG_URL, find_installations
from .formatting import divider_positions, elapsed_pct, expand_popup_fields, field_period, format_credits, popup_label, time_until
from .i18n import T
from .settings import BAR_BG, BAR_DIVIDER, BAR_FG, BAR_FG_WARN, BAR_MARKER, BG, FG, FG_DIM, FG_HEADING, FG_LINK, POPUP_FIELDS

_POPUP_DIR = Path(__file__).parent / 'popup'
_BASELINE_DPI = 96
_GWL_EXSTYLE = -20
_WS_EX_APPWINDOW = 0x00040000
_WS_EX_TOOLWINDOW = 0x00000080
_WS_EX_LAYERED = 0x00080000
_LWA_ALPHA = 0x00000002


class _MONITORINFO(ctypes.Structure):
    _fields_ = [
        ('cbSize', ctypes.wintypes.DWORD),
        ('rcMonitor', ctypes.wintypes.RECT),
        ('rcWork', ctypes.wintypes.RECT),
        ('dwFlags', ctypes.wintypes.DWORD),
    ]


__all__ = ['UsagePopup']

if TYPE_CHECKING:
    from .app import UsageMonitorForClaude
    from .cache import CacheSnapshot


# ---------------------------------------------------------------------------
# Data helpers
# ---------------------------------------------------------------------------

def _usage_entries(usage: dict[str, Any]) -> list[tuple[str, dict[str, Any] | None, int | None]]:
    """Return the list of usage entry tuples from the given usage data."""
    fields = expand_popup_fields(POPUP_FIELDS, usage)
    return [(popup_label(key), usage.get(key), field_period(key)) for key in fields]


def _snapshot_to_dict(
    snap: CacheSnapshot, installations: list[dict[str, str]] | None = None, next_poll_time: float | None = None,
) -> dict[str, Any]:
    """Convert a CacheSnapshot to a JSON-serializable dict for the popup JS.

    Parameters
    ----------
    snap : CacheSnapshot
        Immutable snapshot of the cache state.
    installations : list or None
        Pre-computed installation list, or None to detect now.
    next_poll_time : float or None
        Unix timestamp of the next scheduled API poll.
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
            time_pct = elapsed_pct(resets_at, period) if period else None
            warn = pct >= 100 or (time_pct is not None and pct > time_pct)
            marker_rel = max(0.0, min(1.0, time_pct / 100)) if time_pct is not None else None

            usage.append({
                'label': label,
                'pct_text': f'{pct:.0f}%',
                'fill_pct': max(0.0, min(1.0, pct / 100)),
                'warn': warn,
                'reset_text': time_until(resets_at) if resets_at else '',
                'dividers': divider_positions(resets_at, period) if period else [],
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

    # Status - pass raw timestamps for JS live timer; fallback text for initial load
    if not snap.usage:
        if snap.last_error:
            status: dict[str, Any] = {'text': snap.last_error[:120], 'is_error': True}
        else:
            status = {'text': T['status_refreshing'], 'is_error': False, 'refreshing': True}
    else:
        status = {
            'last_success_time': snap.last_success_time,
            'next_poll_time': next_poll_time,
            'refreshing': snap.refreshing,
            'error': snap.last_error[:120] if snap.last_error else None,
        }

    return {
        'profile': profile,
        'usage': usage,
        'extra': extra,
        'installations': installations,
        'status': status,
    }


def _init_config(snap: CacheSnapshot, next_poll_time: float | None = None) -> dict[str, Any]:
    """Build the config object passed to JS ``init()`` after the page loads."""
    return {
        'colors': {
            'bg': BG, 'fg': FG, 'fg_dim': FG_DIM, 'fg_heading': FG_HEADING, 'fg_link': FG_LINK,
            'bar_bg': BAR_BG, 'bar_fg': BAR_FG, 'bar_fg_warn': BAR_FG_WARN, 'bar_divider': BAR_DIVIDER, 'bar_marker': BAR_MARKER,
        },
        't': {
            'title': T['popup_title'], 'account': T['account'], 'email': T['email'], 'plan': T['plan'],
            'usage': T['usage'], 'extra_usage': T['extra_usage'],
            'claude_code': T['claude_code'], 'changelog': T['changelog'],
            'status_updated_s': T['status_updated_s'], 'status_updated': T['status_updated'],
            'status_next_update': T['status_next_update'], 'status_refreshing': T['status_refreshing'],
            'duration_hm': T['duration_hm'], 'duration_m': T['duration_m'], 'duration_s': T['duration_s'],
        },
        'app_version': __version__,
        'data': _snapshot_to_dict(snap, next_poll_time=next_poll_time),
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
            if not self._popup._shown:
                self._popup._show_window()


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

        self._window = webview.create_window(
            '', url=str(_POPUP_DIR / 'popup.html'),
            width=self.WIDTH, height=initial_height,
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
        """Inject config and show the window transparently for layout."""
        config = _init_config(self.app.cache.snapshot, next_poll_time=self.app._next_poll_time)
        self._window.evaluate_js(f'init({json.dumps(config)})')

        self._popup_hwnd = self._window.native.Handle.ToInt32()

        # Hide the taskbar icon and enable layered mode for opacity control.
        # WinForms sets WS_EX_APPWINDOW by default, which forces a taskbar
        # button even when WS_EX_TOOLWINDOW is present - both must be fixed.
        # WS_EX_LAYERED is needed for SetLayeredWindowAttributes (opacity).
        ex_style = ctypes.windll.user32.GetWindowLongW(self._popup_hwnd, _GWL_EXSTYLE)
        ctypes.windll.user32.SetWindowLongW(
            self._popup_hwnd, _GWL_EXSTYLE,
            (ex_style | _WS_EX_TOOLWINDOW | _WS_EX_LAYERED) & ~_WS_EX_APPWINDOW,
        )

        # Show fully transparent so JS can layout and report the real height
        ctypes.windll.user32.SetLayeredWindowAttributes(self._popup_hwnd, 0, 0, _LWA_ALPHA)
        self._window.show()

    def _show_window(self) -> None:
        """Make the popup visible after the first resize positioned it correctly."""
        # Remove the layered style to restore normal rendering
        ex_style = ctypes.windll.user32.GetWindowLongW(self._popup_hwnd, _GWL_EXSTYLE)
        ctypes.windll.user32.SetWindowLongW(self._popup_hwnd, _GWL_EXSTYLE, ex_style & ~_WS_EX_LAYERED)
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
        last_next_poll_time = self.app._next_poll_time
        while self._running:
            time.sleep(self._CHECK_MS / 1000)
            if not self._running:
                break
            try:
                snap = self.app.cache.snapshot
                next_poll_time = self.app._next_poll_time
                if snap.version == self._last_version and next_poll_time == last_next_poll_time:
                    continue
                if snap.version != self._last_version:
                    self._last_version = snap.version
                    cached_installations = [{'name': i.name, 'version': i.version} for i in find_installations()]
                last_next_poll_time = next_poll_time
                data = _snapshot_to_dict(snap, installations=cached_installations, next_poll_time=next_poll_time)
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
            Logical (x, y) coordinates.  Callers that need physical pixels
            must multiply by the DPI scale factor.
        """
        tray_hwnd = ctypes.windll.user32.FindWindowW('Shell_TrayWnd', None)
        hmon = ctypes.windll.user32.MonitorFromWindow(tray_hwnd, 2)  # MONITOR_DEFAULTTONEAREST

        mon_info = _MONITORINFO()
        mon_info.cbSize = ctypes.sizeof(_MONITORINFO)
        ctypes.windll.user32.GetMonitorInfoW(hmon, ctypes.byref(mon_info))
        mon = mon_info.rcMonitor
        work = mon_info.rcWork

        dpi = ctypes.windll.user32.GetDpiForWindow(self._popup_hwnd) or ctypes.windll.user32.GetDpiForSystem()
        scale = dpi / _BASELINE_DPI

        margin = 12

        if work.left > mon.left:    # left-side taskbar
            x = work.left + margin
        else:
            x = work.right - physical_width - margin

        if work.top > mon.top:      # top taskbar
            y = work.top + margin
        else:
            y = work.bottom - physical_height - margin

        return int(x / scale), int(y / scale)

    def _resize_and_position(self, height: int) -> None:
        """Resize the window and reposition it near the system tray.

        The first call happens while the window is still transparent
        (opacity 0), so separate resize/move calls cause no visible jump.

        pywebview 6.x ``resize()`` applies DPI scaling internally (consistent
        with ``move()``), so both expect logical pixels.  Physical dimensions
        are still computed for ``_tray_position``, which needs them to
        calculate the correct logical position against the physical work-area
        coordinates returned by Win32.
        """
        dpi = ctypes.windll.user32.GetDpiForWindow(self._popup_hwnd) or ctypes.windll.user32.GetDpiForSystem()
        scale = dpi / _BASELINE_DPI
        physical_width = int(self.WIDTH * scale)
        physical_height = int(height * scale)
        self._window.resize(self.WIDTH, height)
        x, y = self._tray_position(physical_width, physical_height)
        self._window.move(x, y)

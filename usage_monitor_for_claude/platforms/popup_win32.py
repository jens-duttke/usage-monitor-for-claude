"""
Windows Popup Host
===================

Owns everything about the popup window that is specific to Windows: extended
window styles, the layered-window transparency used while the content is
measured, tray-anchored positioning, the dismiss watch built from low-level
hooks, and the pinned-popup drag.

The popup's data flow and orchestration live in
:mod:`usage_monitor_for_claude.popup`; this module never touches usage data.
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes
import threading
from typing import Any, Callable

__all__ = ['WINDOW_KWARGS', 'PopupHost', 'popup_url']

# Extra ``webview.create_window`` options for this platform.  A non-resizable
# window is what keeps the frameless popup from being dragged by its edges.
WINDOW_KWARGS = {'resizable': False, 'shadow': False}

_BASELINE_DPI = 96
_GWL_EXSTYLE = -20
_WS_EX_APPWINDOW = 0x00040000
_WS_EX_TOOLWINDOW = 0x00000080
_WS_EX_LAYERED = 0x00080000
_LWA_ALPHA = 0x00000002
_SWP_NOSIZE = 0x0001
_SWP_NOZORDER = 0x0004
_SWP_NOACTIVATE = 0x0010
_WM_QUIT = 0x0012
_MARGIN = 12

# Seconds to wait before acting on a foreground change, to ride out the focus
# bounce WebView2 causes between its host and renderer process on every click
# inside the content area.
_FOREGROUND_SETTLE = 0.2


def popup_url(path: Any) -> str:
    """Return the URL to load the popup document from.

    WebView2 accepts a plain filesystem path.
    """
    return str(path)


class _MONITORINFO(ctypes.Structure):
    _fields_ = [
        ('cbSize', ctypes.wintypes.DWORD),
        ('rcMonitor', ctypes.wintypes.RECT),
        ('rcWork', ctypes.wintypes.RECT),
        ('dwFlags', ctypes.wintypes.DWORD),
    ]


class PopupHost:
    """Window mechanics for the popup on Windows."""

    def __init__(self, window: Any, width: int) -> None:
        self._window = window
        self._width = width
        self._hwnd = 0
        self._pump_tid = 0
        self._drag_offset = (0, 0)
        self._drag_start_dpi = 0
        self._dragging = False

    def prepare(self) -> None:
        """Put the window on screen but fully transparent.

        The content must be laid out before its height is known, and the
        window must be shown for that.  Hiding it behind zero alpha keeps the
        resize and move that follow invisible.

        WinForms sets ``WS_EX_APPWINDOW`` by default, which forces a taskbar
        button even when ``WS_EX_TOOLWINDOW`` is present - both must be fixed.
        ``WS_EX_LAYERED`` is what makes the alpha trick possible.
        """
        self._hwnd = self._window.native.Handle.ToInt32()

        ex_style = ctypes.windll.user32.GetWindowLongW(self._hwnd, _GWL_EXSTYLE)
        ctypes.windll.user32.SetWindowLongW(
            self._hwnd, _GWL_EXSTYLE,
            (ex_style | _WS_EX_TOOLWINDOW | _WS_EX_LAYERED) & ~_WS_EX_APPWINDOW,
        )
        ctypes.windll.user32.SetLayeredWindowAttributes(self._hwnd, 0, 0, _LWA_ALPHA)
        self._window.show()

    def reveal(self) -> None:
        """Make the prepared window visible by dropping the layered style."""
        ex_style = ctypes.windll.user32.GetWindowLongW(self._hwnd, _GWL_EXSTYLE)
        ctypes.windll.user32.SetWindowLongW(self._hwnd, _GWL_EXSTYLE, ex_style & ~_WS_EX_LAYERED)

    def apply_geometry(self, height: int, *, keep_position: bool) -> None:
        """Resize to *height* and, unless *keep_position*, move to the tray anchor.

        pywebview 6.x ``resize()`` applies DPI scaling internally (consistent
        with ``move()``), so both expect logical pixels.  Physical dimensions
        are still computed for the anchor, which needs them to calculate the
        correct logical position against the physical work-area coordinates
        returned by Win32.
        """
        scale = self._scale()
        physical_width = int(self._width * scale)
        physical_height = int(height * scale)

        self._window.resize(self._width, height)
        if keep_position:
            return

        x, y = self._anchor(physical_width, physical_height, scale)
        self._window.move(x, y)

    def watch_dismiss(self, should_dismiss: Callable[[], bool], is_running: Callable[[], bool]) -> None:
        """Block until the popup should close or :meth:`stop_watch` is called.

        Combines three Win32 mechanisms in a single message pump:

        * ``WH_MOUSE_LL`` - catches clicks outside the popup bounds
        * ``WH_KEYBOARD_LL`` - catches Escape even without focus
        * ``EVENT_SYSTEM_FOREGROUND`` - catches Alt-Tab, browser open, etc.
        """
        this_thread = ctypes.windll.kernel32.GetCurrentThreadId()

        # Force creation of this thread's message queue before publishing the
        # thread id, so a WM_QUIT posted by stop_watch() from another thread
        # cannot be lost in the queue-creation window.
        msg = ctypes.wintypes.MSG()
        ctypes.windll.user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 0)  # PM_NOREMOVE
        self._pump_tid = this_thread

        def _post_quit() -> None:
            if should_dismiss():
                ctypes.windll.user32.PostThreadMessageW(this_thread, _WM_QUIT, 0, 0)

        call_next = ctypes.windll.user32.CallNextHookEx
        call_next.argtypes = [ctypes.wintypes.HANDLE, ctypes.c_int, ctypes.wintypes.WPARAM, ctypes.wintypes.LPARAM]
        call_next.restype = ctypes.c_long

        class MSLLHOOKSTRUCT(ctypes.Structure):
            _fields_ = [('pt', ctypes.wintypes.POINT), ('mouseData', ctypes.wintypes.DWORD),
                        ('flags', ctypes.wintypes.DWORD), ('time', ctypes.wintypes.DWORD),
                        ('dwExtraInfo', ctypes.POINTER(ctypes.c_ulong))]

        @ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_int, ctypes.wintypes.WPARAM, ctypes.wintypes.LPARAM)
        def mouse_proc(code, wparam, lparam):
            if code >= 0 and wparam == 0x0201 and self._hwnd:  # WM_LBUTTONDOWN
                rect = ctypes.wintypes.RECT()
                ctypes.windll.user32.GetWindowRect(self._hwnd, ctypes.byref(rect))
                info = ctypes.cast(lparam, ctypes.POINTER(MSLLHOOKSTRUCT)).contents
                if not (rect.left <= info.pt.x <= rect.right and rect.top <= info.pt.y <= rect.bottom):
                    _post_quit()
            return call_next(None, code, wparam, lparam)

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
            return call_next(None, code, wparam, lparam)

        winevent_callback = ctypes.WINFUNCTYPE(
            None, ctypes.wintypes.HANDLE, ctypes.wintypes.DWORD, ctypes.wintypes.HWND,
            ctypes.wintypes.LONG, ctypes.wintypes.LONG, ctypes.wintypes.DWORD, ctypes.wintypes.DWORD,
        )

        settle_timer: threading.Timer | None = None

        def _delayed_foreground_check() -> None:
            """Dismiss only if focus is still outside the popup after the delay."""
            if not self._hwnd or not should_dismiss():
                return
            foreground = ctypes.windll.user32.GetForegroundWindow()
            if self._owns_window(foreground):
                return
            _post_quit()

        @winevent_callback
        def fg_proc(_hook, _event, hwnd, _id_obj, _id_child, _thread, _time):
            nonlocal settle_timer
            if not self._hwnd or self._owns_window(hwnd):
                return
            if settle_timer is not None:
                settle_timer.cancel()
            settle_timer = threading.Timer(_FOREGROUND_SETTLE, _delayed_foreground_check)
            settle_timer.daemon = True
            settle_timer.start()

        mouse_hook = ctypes.windll.user32.SetWindowsHookExW(14, mouse_proc, None, 0)  # WH_MOUSE_LL
        kb_hook = ctypes.windll.user32.SetWindowsHookExW(13, kb_proc, None, 0)  # WH_KEYBOARD_LL
        # EVENT_SYSTEM_FOREGROUND with WINEVENT_SKIPOWNPROCESS
        fg_hook = ctypes.windll.user32.SetWinEventHook(0x0003, 0x0003, None, fg_proc, 0, 0, 0x0002)

        try:
            while is_running() and ctypes.windll.user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                pass
        finally:
            if settle_timer is not None:
                settle_timer.cancel()
            ctypes.windll.user32.UnhookWindowsHookEx(mouse_hook)
            ctypes.windll.user32.UnhookWindowsHookEx(kb_hook)
            ctypes.windll.user32.UnhookWinEvent(fg_hook)
            self._pump_tid = 0

    def stop_watch(self) -> None:
        """Wake the dismiss-watch pump so it can remove its hooks and exit.

        The pump blocks inside ``GetMessageW`` and re-checks its running flag
        only after a message arrives, so clearing the flag alone is not enough
        - especially while pinned, where the user-dismissal path never posts.
        """
        if self._pump_tid:
            ctypes.windll.user32.PostThreadMessageW(self._pump_tid, _WM_QUIT, 0, 0)

    def begin_drag(self) -> bool:
        """Anchor the cursor to the window for a pinned-popup drag.

        Records the physical offset between the cursor and the window's
        top-left corner.  Dragging is then done entirely in physical screen
        coordinates, which keeps the cursor anchored even across monitors with
        different DPI scaling, where logical-pixel deltas would jump at the
        boundary.
        """
        if not self._hwnd:
            return False

        cursor = ctypes.wintypes.POINT()
        ctypes.windll.user32.GetCursorPos(ctypes.byref(cursor))
        rect = ctypes.wintypes.RECT()
        ctypes.windll.user32.GetWindowRect(self._hwnd, ctypes.byref(rect))
        self._drag_offset = (cursor.x - rect.left, cursor.y - rect.top)
        self._drag_start_dpi = self._dpi()
        self._dragging = True

        return True

    def drag(self) -> bool:
        """Reposition the popup so the cursor keeps its initial grab offset.

        Each step computes the absolute window position from the current
        physical cursor position, so out-of-order calls converge on the right
        spot instead of accumulating drift.
        """
        if not self._dragging or not self._hwnd:
            return False

        cursor = ctypes.wintypes.POINT()
        ctypes.windll.user32.GetCursorPos(ctypes.byref(cursor))
        x = cursor.x - self._drag_offset[0]
        y = cursor.y - self._drag_offset[1]
        ctypes.windll.user32.SetWindowPos(self._hwnd, 0, x, y, 0, 0, _SWP_NOSIZE | _SWP_NOZORDER | _SWP_NOACTIVATE)

        return True

    def end_drag(self, height: int) -> None:
        """Finish a drag and correct the size after a cross-monitor DPI change.

        Crossing a monitor boundary triggers Windows' Per-Monitor-V2 rescale,
        which can race with pywebview's size handling and leave the popup
        mis-sized.  Re-asserting the size once, against the destination
        monitor's DPI, makes the final dimensions deterministic.  Position is
        preserved by ``resize``'s default top-left fix point.
        """
        self._dragging = False
        if not self._hwnd:
            return

        if self._dpi() != self._drag_start_dpi:
            self._window.resize(self._width, height)

    def _owns_window(self, hwnd: int) -> bool:
        """Return True if *hwnd* is the popup itself or one of its children."""
        if hwnd == self._hwnd:
            return True
        if ctypes.windll.user32.IsChild(self._hwnd, hwnd):
            return True

        return ctypes.windll.user32.GetAncestor(hwnd, 3) == self._hwnd  # GA_ROOTOWNER

    def _dpi(self) -> int:
        """Return the popup's DPI, falling back to the system DPI."""
        return ctypes.windll.user32.GetDpiForWindow(self._hwnd) or ctypes.windll.user32.GetDpiForSystem()

    def _scale(self) -> float:
        """Return the popup's DPI scale factor."""
        return self._dpi() / _BASELINE_DPI

    def _anchor(self, physical_width: int, physical_height: int, scale: float) -> tuple[int, int]:
        """Calculate the popup position near the system tray.

        Returns logical (x, y) coordinates; callers that need physical pixels
        must multiply by the DPI scale factor.
        """
        tray_hwnd = ctypes.windll.user32.FindWindowW('Shell_TrayWnd', None)
        hmon = ctypes.windll.user32.MonitorFromWindow(tray_hwnd, 2)  # MONITOR_DEFAULTTONEAREST

        mon_info = _MONITORINFO()
        mon_info.cbSize = ctypes.sizeof(_MONITORINFO)
        ctypes.windll.user32.GetMonitorInfoW(hmon, ctypes.byref(mon_info))
        mon = mon_info.rcMonitor
        work = mon_info.rcWork

        if work.left > mon.left:    # left-side taskbar
            x = work.left + _MARGIN
        else:
            x = work.right - physical_width - _MARGIN

        if work.top > mon.top:      # top taskbar
            y = work.top + _MARGIN
        else:
            y = work.bottom - physical_height - _MARGIN

        return int(x / scale), int(y / scale)

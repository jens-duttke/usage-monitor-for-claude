"""
Windows Popup Host Tests
=========================

Unit tests for the Win32 popup host: tray anchoring, DPI-aware geometry and
the pinned-popup drag.  The module reaches into ``ctypes.windll`` at call
time, so the whole file is skipped off Windows.
"""
from __future__ import annotations

import ctypes
import sys
import unittest
from unittest.mock import MagicMock, call, patch

if sys.platform != 'win32':
    raise unittest.SkipTest('Win32 popup host is only usable on Windows')

import ctypes.wintypes  # noqa: E402

from usage_monitor_for_claude.platforms.popup_win32 import (  # noqa: E402
    _BASELINE_DPI, _GWL_EXSTYLE, _LWA_ALPHA, _MONITORINFO, _SWP_NOACTIVATE, _SWP_NOSIZE, _SWP_NOZORDER,
    _WS_EX_APPWINDOW, _WS_EX_LAYERED, _WS_EX_TOOLWINDOW, PopupHost,
)

WIDTH = 340


def _host(window=None):
    """Build a host with a mocked pywebview window and a known handle."""
    host = PopupHost(window or MagicMock(), WIDTH)
    host._hwnd = 12345

    return host


def _monitor_filler(work_left, work_top, work_right, work_bottom, mon_left=0, mon_top=0):
    """Return a GetMonitorInfoW side effect that reports the given bounds."""
    def fill(_hmon, ptr):
        info = ctypes.cast(ptr, ctypes.POINTER(_MONITORINFO)).contents
        info.cbSize = ctypes.sizeof(_MONITORINFO)
        info.rcMonitor.left = mon_left
        info.rcMonitor.top = mon_top
        info.rcMonitor.right = work_right
        info.rcMonitor.bottom = work_bottom
        info.rcWork.left = work_left
        info.rcWork.top = work_top
        info.rcWork.right = work_right
        info.rcWork.bottom = work_bottom

    return fill


class TestAnchor(unittest.TestCase):
    """Tests for popup placement near the tray.

    ``_anchor`` receives physical-pixel dimensions (the actual window size
    after DPI scaling) and work-area bounds in physical pixels.  It returns
    logical coordinates suitable for pywebview's ``move()``.
    """

    def _call(self, work_left, work_top, work_right, work_bottom, dpi, physical_width, physical_height,
              mon_left=0, mon_top=0):
        host = _host()
        with patch('ctypes.windll.user32.FindWindowW', return_value=99999), \
             patch('ctypes.windll.user32.MonitorFromWindow', return_value=11111), \
             patch('ctypes.windll.user32.GetMonitorInfoW',
                   side_effect=_monitor_filler(work_left, work_top, work_right, work_bottom, mon_left, mon_top)):
            return host._anchor(physical_width, physical_height, dpi / _BASELINE_DPI)

    def test_bottom_right_at_100_percent_scaling(self):
        """At 100% DPI, popup aligns to bottom-right of work area."""
        x, y = self._call(0, 0, 1920, 1040, _BASELINE_DPI, 340, 400)
        self.assertEqual(x, 1920 - 340 - 12)
        self.assertEqual(y, 1040 - 400 - 12)

    def test_bottom_right_at_125_percent_scaling(self):
        """At 125% DPI, logical coordinates place the popup within the work area."""
        scale = 120 / _BASELINE_DPI
        pw, ph = int(340 * scale), int(400 * scale)
        x, y = self._call(0, 0, 2400, 1300, 120, pw, ph)
        self.assertEqual(x, int((2400 - pw - 12) / scale))
        self.assertEqual(y, int((1300 - ph - 12) / scale))

    def test_bottom_right_at_150_percent_scaling(self):
        """At 150% DPI, logical coordinates place the popup within the work area."""
        scale = 144 / _BASELINE_DPI
        pw, ph = int(340 * scale), int(400 * scale)
        x, y = self._call(0, 0, 2880, 1560, 144, pw, ph)
        self.assertEqual(x, int((2880 - pw - 12) / scale))
        self.assertEqual(y, int((1560 - ph - 12) / scale))

    def test_taskbar_on_left(self):
        """When the taskbar is on the left, the popup goes to the left edge."""
        x, y = self._call(60, 0, 1920, 1080, _BASELINE_DPI, 340, 400)
        self.assertEqual(x, 60 + 12)
        self.assertEqual(y, 1080 - 400 - 12)

    def test_taskbar_on_top(self):
        """When the taskbar is on top, the popup goes to the top edge."""
        x, y = self._call(0, 40, 1920, 1080, _BASELINE_DPI, 340, 400)
        self.assertEqual(x, 1920 - 340 - 12)
        self.assertEqual(y, 40 + 12)

    def test_popup_fits_within_work_area_at_125_percent(self):
        """The popup's physical extent must not exceed the work area at 125% scaling."""
        scale = 120 / _BASELINE_DPI
        pw, ph = int(340 * scale), int(400 * scale)
        x, y = self._call(0, 0, 2400, 1300, 120, pw, ph)
        self.assertLessEqual(x * scale + pw, 2400)
        self.assertLessEqual(y * scale + ph, 1300)

    def test_taskbar_on_bottom_when_monitor_offset_left(self):
        """Popup goes to bottom-right even when the primary monitor is not at virtual x=0.

        Regression: comparing ``work.left > 0`` instead of ``work.left >
        mon.left`` fired whenever a secondary monitor sat to the left of the
        primary, putting the popup at the left edge instead of the corner.
        """
        x, y = self._call(1920, 0, 3840, 1040, _BASELINE_DPI, 340, 400, mon_left=1920)
        self.assertEqual(x, 3840 - 340 - 12)
        self.assertEqual(y, 1040 - 400 - 12)

    def test_taskbar_on_left_with_negative_virtual_coordinates(self):
        """A left-side taskbar remains left of a monitor at a negative virtual x."""
        x, y = self._call(-1860, -1080, 0, 1040, _BASELINE_DPI, 340, 400, mon_left=-1920, mon_top=-1080)
        self.assertEqual(x, -1860 + 12)
        self.assertEqual(y, 1040 - 400 - 12)

    def test_taskbar_on_top_with_negative_virtual_coordinates(self):
        """A top taskbar remains above content on a monitor at a negative virtual y."""
        x, y = self._call(-1920, -1040, 0, 0, _BASELINE_DPI, 340, 400, mon_left=-1920, mon_top=-1080)
        self.assertEqual(x, 0 - 340 - 12)
        self.assertEqual(y, -1040 + 12)


class TestApplyGeometry(unittest.TestCase):
    """Tests for DPI-aware resize and positioning."""

    def _call(self, css_height, dpi, keep_position=False, window_dpi=None):
        window = MagicMock()
        host = _host(window)
        with patch('ctypes.windll.user32.GetDpiForWindow', return_value=dpi if window_dpi is None else window_dpi), \
             patch('ctypes.windll.user32.GetDpiForSystem', return_value=dpi), \
             patch('ctypes.windll.user32.FindWindowW', return_value=99999), \
             patch('ctypes.windll.user32.MonitorFromWindow', return_value=11111), \
             patch('ctypes.windll.user32.GetMonitorInfoW', side_effect=_monitor_filler(0, 0, 1920, 1040)):
            host.apply_geometry(css_height, keep_position=keep_position)

        return window

    def test_resize_at_100_percent(self):
        """At 100% DPI, resize uses CSS pixels directly."""
        self._call(500, 96).resize.assert_called_once_with(WIDTH, 500)

    def test_resize_at_125_percent(self):
        """At 125% DPI, resize receives logical pixels; pywebview scales internally."""
        self._call(500, 120).resize.assert_called_once_with(WIDTH, 500)

    def test_resize_at_150_percent(self):
        """At 150% DPI, resize receives logical pixels; pywebview scales internally."""
        self._call(500, 144).resize.assert_called_once_with(WIDTH, 500)

    def test_move_receives_logical_coordinates(self):
        """move() receives logical coordinates regardless of DPI."""
        x, y = self._call(500, 120).move.call_args[0]
        self.assertLess(x, 1920)
        self.assertLess(y, 1040)

    def test_window_fits_within_work_area_at_125_percent(self):
        """After resize + move at 125% DPI, the window stays within the work area."""
        scale = 120 / _BASELINE_DPI
        window = self._call(500, 120)
        resize_w, resize_h = window.resize.call_args[0]
        move_x, move_y = window.move.call_args[0]
        self.assertLessEqual((move_x + resize_w) * scale, 1920)
        self.assertLessEqual((move_y + resize_h) * scale, 1040)

    def test_falls_back_to_system_dpi_when_window_dpi_unavailable(self):
        """A window DPI of 0 falls back to the system DPI."""
        window = MagicMock()
        host = _host(window)
        with patch('ctypes.windll.user32.GetDpiForWindow', return_value=0), \
             patch('ctypes.windll.user32.GetDpiForSystem', return_value=144) as mock_system_dpi, \
             patch('ctypes.windll.user32.FindWindowW', return_value=99999), \
             patch('ctypes.windll.user32.MonitorFromWindow', return_value=11111), \
             patch('ctypes.windll.user32.GetMonitorInfoW', side_effect=_monitor_filler(0, 0, 1920, 1040)):
            host.apply_geometry(500, keep_position=False)

        mock_system_dpi.assert_called()
        window.resize.assert_called_once_with(WIDTH, 500)

    def test_kept_position_resizes_without_moving(self):
        """A moved pinned popup keeps its position when the content height changes."""
        window = self._call(500, _BASELINE_DPI, keep_position=True)
        window.resize.assert_called_once_with(WIDTH, 500)
        window.move.assert_not_called()

    def test_geometry_converts_physical_anchor_dimensions_to_logical_move(self):
        """resize() stays logical while anchor calculations receive physical dimensions."""
        window = MagicMock()
        host = _host(window)
        anchor = MagicMock(return_value=(932, 212))

        with patch.object(host, '_scale', return_value=1.5), patch.object(host, '_anchor', anchor):
            host.apply_geometry(500, keep_position=False)

        window.resize.assert_called_once_with(WIDTH, 500)
        anchor.assert_called_once_with(510, 750, 1.5)
        window.move.assert_called_once_with(932, 212)

    def test_geometry_uses_window_dpi_when_taskbar_monitor_has_different_scale(self):
        """Logical placement is converted with the HWND DPI, not the taskbar monitor's assumed scale."""
        window = MagicMock()
        host = _host(window)
        with patch('ctypes.windll.user32.GetDpiForWindow', return_value=144), \
             patch('ctypes.windll.user32.FindWindowW', return_value=99999), \
             patch('ctypes.windll.user32.MonitorFromWindow', return_value=11111), \
             patch('ctypes.windll.user32.GetMonitorInfoW', side_effect=_monitor_filler(0, 0, 1920, 1080)):
            host.apply_geometry(500, keep_position=False)

        window.resize.assert_called_once_with(WIDTH, 500)
        window.move.assert_called_once_with(int((1920 - 510 - 12) / 1.5), int((1080 - 750 - 12) / 1.5))


class TestPrepareReveal(unittest.TestCase):
    """Tests for making the native window transparent before revealing it."""

    def test_prepare_hides_taskbar_window_and_shows_transparent_host(self):
        """Preparation applies tool-window and layered styles before showing the host."""
        window = MagicMock()
        window.native.Handle.ToInt32.return_value = 54321
        host = PopupHost(window, WIDTH)
        original_style = _WS_EX_APPWINDOW

        with patch('ctypes.windll.user32.GetWindowLongW', return_value=original_style) as get_style, \
             patch('ctypes.windll.user32.SetWindowLongW') as set_style, \
             patch('ctypes.windll.user32.SetLayeredWindowAttributes') as set_alpha:
            host.prepare()

        expected_style = (original_style | _WS_EX_TOOLWINDOW | _WS_EX_LAYERED) & ~_WS_EX_APPWINDOW
        self.assertEqual(host._hwnd, 54321)
        get_style.assert_called_once_with(54321, _GWL_EXSTYLE)
        set_style.assert_called_once_with(54321, _GWL_EXSTYLE, expected_style)
        set_alpha.assert_called_once_with(54321, 0, 0, _LWA_ALPHA)
        window.show.assert_called_once_with()

    def test_reveal_removes_layered_style_without_recreating_window(self):
        """Revealing drops transparency while retaining the existing native window."""
        window = MagicMock()
        host = _host(window)
        original_style = _WS_EX_LAYERED | _WS_EX_TOOLWINDOW

        with patch('ctypes.windll.user32.GetWindowLongW', return_value=original_style) as get_style, \
             patch('ctypes.windll.user32.SetWindowLongW') as set_style:
            host.reveal()

        get_style.assert_called_once_with(12345, _GWL_EXSTYLE)
        set_style.assert_called_once_with(12345, _GWL_EXSTYLE, _WS_EX_TOOLWINDOW)
        window.show.assert_not_called()


class TestDrag(unittest.TestCase):
    """Tests for the pinned-popup drag."""

    @staticmethod
    def _cursor_filler(x, y):
        def fill(ptr):
            point = ctypes.cast(ptr, ctypes.POINTER(ctypes.wintypes.POINT)).contents
            point.x = x
            point.y = y

        return fill

    @staticmethod
    def _rect_filler(left, top):
        def fill(_hwnd, ptr):
            rect = ctypes.cast(ptr, ctypes.POINTER(ctypes.wintypes.RECT)).contents
            rect.left = left
            rect.top = top
            rect.right = left + WIDTH
            rect.bottom = top + 400

        return fill

    def test_begin_drag_anchors_physical_cursor_offset(self):
        """The grab offset is recorded in physical pixels."""
        host = _host()
        with patch('ctypes.windll.user32.GetCursorPos', side_effect=self._cursor_filler(660, 580)), \
             patch('ctypes.windll.user32.GetWindowRect', side_effect=self._rect_filler(620, 540)), \
             patch('ctypes.windll.user32.GetDpiForWindow', return_value=96):
            self.assertTrue(host.begin_drag())

        self.assertTrue(host._dragging)
        self.assertEqual(host._drag_offset, (40, 40))
        self.assertEqual(host._drag_start_dpi, 96)

    def test_begin_drag_without_window_is_refused(self):
        """No window handle means nothing to drag."""
        host = _host()
        host._hwnd = 0
        self.assertFalse(host.begin_drag())

    def test_drag_ignored_when_not_dragging(self):
        """Drag steps outside a drag must not move the window."""
        host = _host()
        with patch('ctypes.windll.user32.SetWindowPos') as mock_set_pos:
            self.assertFalse(host.drag())
        mock_set_pos.assert_not_called()

    def test_drag_moves_popup_to_physical_cursor(self):
        """Each step positions the window from the absolute cursor position."""
        host = _host()
        host._dragging = True
        host._drag_offset = (40, 40)

        with patch('ctypes.windll.user32.GetCursorPos', side_effect=self._cursor_filler(700, 620)), \
             patch('ctypes.windll.user32.SetWindowPos') as mock_set_pos:
            self.assertTrue(host.drag())

        mock_set_pos.assert_called_once_with(
            12345, 0, 660, 580, 0, 0, _SWP_NOSIZE | _SWP_NOZORDER | _SWP_NOACTIVATE,
        )

    def test_end_drag_reasserts_size_on_dpi_change(self):
        """Crossing a monitor boundary needs the size re-asserted once."""
        window = MagicMock()
        host = _host(window)
        host._dragging = True
        host._drag_start_dpi = 96

        with patch('ctypes.windll.user32.GetDpiForWindow', return_value=144):
            host.end_drag(500)

        self.assertFalse(host._dragging)
        window.resize.assert_called_once_with(WIDTH, 500)

    def test_end_drag_keeps_size_without_dpi_change(self):
        """A drag within one monitor needs no correction."""
        window = MagicMock()
        host = _host(window)
        host._dragging = True
        host._drag_start_dpi = 96

        with patch('ctypes.windll.user32.GetDpiForWindow', return_value=96):
            host.end_drag(500)

        self.assertFalse(host._dragging)
        window.resize.assert_not_called()


class TestDismissHooks(unittest.TestCase):
    """Tests for dismiss-watch hook installation and cleanup."""

    def _run_watch(self, get_message):
        host = _host()
        raised = None
        with patch('ctypes.windll.kernel32.GetCurrentThreadId', return_value=77), \
             patch('ctypes.windll.user32.PeekMessageW'), \
             patch('ctypes.windll.user32.CallNextHookEx'), \
             patch('ctypes.windll.user32.SetWindowsHookExW', side_effect=[101, 202]), \
             patch('ctypes.windll.user32.SetWinEventHook', return_value=303), \
             patch('ctypes.windll.user32.GetMessageW', side_effect=get_message), \
             patch('ctypes.windll.user32.UnhookWindowsHookEx') as unhook, \
             patch('ctypes.windll.user32.UnhookWinEvent') as unhook_event:
            try:
                host.watch_dismiss(lambda: False, lambda: True)
            except RuntimeError as error:
                raised = error

        return host, unhook, unhook_event, raised

    def test_watch_removes_all_hooks_when_message_pump_exits(self):
        """A normal WM_QUIT exits only after every installed hook is removed."""
        host, unhook, unhook_event, raised = self._run_watch([0])

        self.assertIsNone(raised)
        unhook.assert_has_calls([call(101), call(202)])
        unhook_event.assert_called_once_with(303)
        self.assertEqual(host._pump_tid, 0)

    def test_watch_removes_hooks_when_message_pump_fails(self):
        """An exception from the message pump cannot leave system-wide hooks active."""
        host, unhook, unhook_event, raised = self._run_watch([RuntimeError('message pump failed')])

        self.assertIsInstance(raised, RuntimeError)
        unhook.assert_has_calls([call(101), call(202)])
        unhook_event.assert_called_once_with(303)
        self.assertEqual(host._pump_tid, 0)




class TestWindowOptions(unittest.TestCase):
    """Tests for the platform's window creation options."""

    def test_popup_url_is_a_plain_path(self):
        """WebView2 accepts a filesystem path."""
        from pathlib import Path

        from usage_monitor_for_claude.platforms.popup_win32 import popup_url

        self.assertEqual(popup_url(Path(r'C:\app\popup.html')), r'C:\app\popup.html')


if __name__ == '__main__':
    unittest.main()

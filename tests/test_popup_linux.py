"""
Linux Popup Host Tests
=======================

Unit tests for the GTK popup host: anchoring, the map-then-move ordering,
dismissal on focus loss or Escape, and the pinned-popup drag.  The host
defers every ``gi`` import, so these tests run without PyGObject.
"""
from __future__ import annotations

import sys
import threading
import unittest
from pathlib import Path
from unittest.mock import MagicMock, call, patch

if sys.platform == 'win32':
    raise unittest.SkipTest('GTK popup host is not used on Windows')

from usage_monitor_for_claude.platforms.popup_linux import (  # noqa: E402
    _KEY_ESCAPE, _MARGIN, WINDOW_KWARGS, PopupHost, popup_url,
)

WIDTH = 340


def _host(window=None, gtk_window=None):
    """Build a host whose GTK calls run inline instead of on a main loop."""
    host = PopupHost(window or MagicMock(), WIDTH)
    gtk_window = gtk_window or MagicMock()
    host._gtk_window = lambda: gtk_window
    host._on_main_loop = lambda action: action(gtk_window)

    return host, gtk_window


def _monitor(work_x=2560, work_y=32, work_width=3440, work_height=1357):
    monitor = MagicMock()
    monitor.get_workarea.return_value = MagicMock(x=work_x, y=work_y, width=work_width, height=work_height)

    return monitor


class TestWindowOptions(unittest.TestCase):
    """Tests for the platform's window creation options."""

    def test_popup_url_is_a_file_uri(self):
        """A bare path leaves WebKitGTK on about:blank, so a real URI is required."""
        self.assertEqual(popup_url(Path('/opt/app/popup.html')), 'file:///opt/app/popup.html')

    def test_window_is_resizable(self):
        """GTK ignores resize() on a non-resizable window."""
        self.assertTrue(WINDOW_KWARGS['resizable'])


class TestAnchor(unittest.TestCase):
    """Tests for placement in the work area's tray corner."""

    def test_anchors_to_top_right_of_work_area(self):
        """The panel sits at the top, so the popup hangs below its right corner."""
        host, _ = _host()
        with patch.object(host, '_primary_monitor', return_value=_monitor()):
            self.assertEqual(host._anchor(), (2560 + 3440 - WIDTH - _MARGIN, 32 + _MARGIN))

    def test_anchor_uses_absolute_coordinates(self):
        """Gtk.Window.move() takes root coordinates, so the monitor origin is included."""
        host, _ = _host()
        with patch.object(host, '_primary_monitor', return_value=_monitor(work_x=0, work_y=0)):
            x, _y = host._anchor()
        self.assertEqual(x, 3440 - WIDTH - _MARGIN)

    def test_anchor_without_display(self):
        """No display means no anchor rather than a crash."""
        host, _ = _host()
        with patch.object(host, '_primary_monitor', return_value=None):
            self.assertIsNone(host._anchor())


class TestPrepareAndReveal(unittest.TestCase):
    """Tests for the invisible-measure-then-show sequence."""

    def test_prepare_hides_and_shows(self):
        """The window goes on screen at zero opacity so the layout can settle."""
        window = MagicMock()
        host, gtk_window = _host(window)
        host.prepare()
        gtk_window.set_opacity.assert_called_once_with(0.0)
        window.show.assert_called_once()

    def test_reveal_maps_before_moving(self):
        """A position set on an unmapped window is discarded by the compositor."""
        host, gtk_window = _host()
        with patch.object(host, '_primary_monitor', return_value=_monitor()):
            host.reveal()

        order = [name for name, _args, _kwargs in gtk_window.method_calls]
        self.assertLess(order.index('show_all'), order.index('move'))
        self.assertLess(order.index('present'), order.index('move'))

    def test_reveal_forces_focus(self):
        """Without focus the window can never emit focus-out, so it could never dismiss."""
        host, gtk_window = _host()
        gdk_window = MagicMock()
        gtk_window.get_window.return_value = gdk_window
        fake_gdk = MagicMock()
        fake_gdk.CURRENT_TIME = 0

        with patch.object(host, '_primary_monitor', return_value=_monitor()), \
             patch.dict(sys.modules, {'gi.repository': MagicMock(Gdk=fake_gdk)}):
            host.reveal()

        gdk_window.focus.assert_called_once()

    def test_reveal_makes_the_window_opaque_last(self):
        """Opacity is restored only after the window sits where it belongs."""
        host, gtk_window = _host()
        with patch.object(host, '_primary_monitor', return_value=_monitor()):
            host.reveal()

        self.assertEqual(gtk_window.set_opacity.call_args, call(1.0))
        order = [name for name, _args, _kwargs in gtk_window.method_calls]
        self.assertLess(order.index('move'), order.index('set_opacity'))

    def test_reveal_keeps_position_when_asked(self):
        """A dragged pinned popup must not snap back on reveal."""
        host, gtk_window = _host()
        host._keep_position = True
        with patch.object(host, '_primary_monitor', return_value=_monitor()):
            host.reveal()
        gtk_window.move.assert_not_called()


class TestApplyGeometry(unittest.TestCase):
    """Tests for resizing and repositioning."""

    def test_resize_uses_logical_pixels(self):
        """GTK reports and consumes logical pixels, so no DPI arithmetic is needed."""
        window = MagicMock()
        host, _ = _host(window)
        with patch.object(host, '_primary_monitor', return_value=_monitor()):
            host.apply_geometry(565, keep_position=False)
        window.resize.assert_called_once_with(WIDTH, 565)

    def test_moves_to_anchor(self):
        """An unpinned popup follows the work-area corner."""
        host, gtk_window = _host()
        with patch.object(host, '_primary_monitor', return_value=_monitor()):
            host.apply_geometry(565, keep_position=False)
        gtk_window.move.assert_called_once_with(2560 + 3440 - WIDTH - _MARGIN, 32 + _MARGIN)

    def test_kept_position_resizes_without_moving(self):
        """A moved pinned popup keeps its position when the content height changes."""
        window = MagicMock()
        host, gtk_window = _host(window)
        host.apply_geometry(565, keep_position=True)
        window.resize.assert_called_once_with(WIDTH, 565)
        gtk_window.move.assert_not_called()


class TestDismissWatch(unittest.TestCase):
    """Tests for dismissal on focus loss and Escape."""

    def _watching(self, should_dismiss):
        host, gtk_window = _host()
        running = threading.Event()
        running.set()
        thread = threading.Thread(
            target=lambda: host.watch_dismiss(should_dismiss, running.is_set), daemon=True,
        )
        thread.start()
        self.addCleanup(host.stop_watch)

        return host, gtk_window, thread

    def test_connects_both_signals(self):
        """Focus loss covers clicks elsewhere, Escape covers the keyboard."""
        host, gtk_window, _thread = self._watching(lambda: True)
        connected = {args[0] for name, args, _kwargs in gtk_window.method_calls if name == 'connect'}
        self.assertEqual(connected, {'focus-out-event', 'key-press-event'})

    def test_focus_out_dismisses(self):
        """Losing focus closes an unpinned popup."""
        host, _gtk_window, thread = self._watching(lambda: True)
        host._on_focus_out()
        thread.join(timeout=2)
        self.assertFalse(thread.is_alive())

    def test_focus_out_ignored_while_pinned(self):
        """A pinned popup stays open when focus moves elsewhere."""
        host, _gtk_window, thread = self._watching(lambda: False)
        host._on_focus_out()
        thread.join(timeout=0.5)
        self.assertTrue(thread.is_alive())

    def test_escape_dismisses(self):
        """Escape closes an unpinned popup."""
        host, _gtk_window, thread = self._watching(lambda: True)
        host._on_key_press(None, MagicMock(keyval=_KEY_ESCAPE))
        thread.join(timeout=2)
        self.assertFalse(thread.is_alive())

    def test_other_keys_are_ignored(self):
        """Only Escape dismisses; the content keeps every other key."""
        host, _gtk_window, thread = self._watching(lambda: True)
        host._on_key_press(None, MagicMock(keyval=0x61))  # 'a'
        thread.join(timeout=0.5)
        self.assertTrue(thread.is_alive())

    def test_escape_ignored_while_pinned(self):
        """A pinned popup ignores Escape as well."""
        host, _gtk_window, thread = self._watching(lambda: False)
        host._on_key_press(None, MagicMock(keyval=_KEY_ESCAPE))
        thread.join(timeout=0.5)
        self.assertTrue(thread.is_alive())

    def test_stop_watch_releases_the_wait(self):
        """Closing the window must end the watch even while pinned."""
        host, _gtk_window, thread = self._watching(lambda: False)
        host.stop_watch()
        thread.join(timeout=2)
        self.assertFalse(thread.is_alive())

    def test_signal_handlers_never_swallow_the_event(self):
        """Returning True would stop GTK from delivering the event elsewhere."""
        host, _gtk_window, _thread = self._watching(lambda: True)
        self.assertFalse(host._on_focus_out())
        self.assertFalse(host._on_key_press(None, MagicMock(keyval=_KEY_ESCAPE)))


class TestDrag(unittest.TestCase):
    """Tests for the pinned-popup drag."""

    def _host_with_pointer(self, pointer, origin):
        host, gtk_window = _host()
        host._pointer_and_origin = lambda: (*pointer, *origin)

        return host, gtk_window

    def test_begin_drag_records_the_grab_offset(self):
        host, _gtk_window = self._host_with_pointer((700, 620), (660, 580))
        self.assertTrue(host.begin_drag())
        self.assertEqual(host._drag_offset, (40, 40))
        self.assertTrue(host._dragging)

    def test_begin_drag_without_pointer_is_refused(self):
        """No display means no pointer to anchor to."""
        host, _gtk_window = _host()
        host._pointer_and_origin = lambda: None
        self.assertFalse(host.begin_drag())

    def test_drag_ignored_when_not_dragging(self):
        host, gtk_window = self._host_with_pointer((700, 620), (660, 580))
        self.assertFalse(host.drag())
        gtk_window.move.assert_not_called()

    def test_drag_moves_to_pointer_minus_offset(self):
        """Each step is absolute, so out-of-order calls converge instead of drifting."""
        host, gtk_window = self._host_with_pointer((700, 620), (660, 580))
        host.begin_drag()
        host._pointer_and_origin = lambda: (900, 700, 660, 580)
        self.assertTrue(host.drag())
        gtk_window.move.assert_called_once_with(860, 660)

    def test_end_drag_clears_the_flag(self):
        host, _gtk_window = self._host_with_pointer((700, 620), (660, 580))
        host.begin_drag()
        host.end_drag(565)
        self.assertFalse(host._dragging)
        self.assertEqual(host._height, 565)


if __name__ == '__main__':
    unittest.main()

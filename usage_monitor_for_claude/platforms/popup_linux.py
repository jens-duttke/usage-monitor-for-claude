"""
Linux Popup Host
=================

Owns everything about the popup window that is specific to Linux: the GTK
window handle behind pywebview, opacity-based invisibility while the content
is measured, work-area anchoring, dismissal on focus loss or Escape, and the
pinned-popup drag.

Four properties of the GTK backend shape this module, and each one is silent
when violated - every pywebview call still reports success:

* ``Window.show()`` does not map a window created with ``hidden=True``.  The
  GTK window must be mapped through ``show_all()`` and ``present()``.
* A position set before the window is mapped is discarded, and the compositor
  then places the window by its own policy.  The move must follow the map.
* Without forced focus the compositor's focus-stealing prevention leaves a
  frameless keep-above window unfocused, so ``focus-out-event`` never fires
  and the popup could never dismiss itself.
* GTK signal handlers run on the main loop, and pywebview's calls wait for
  that same loop - calling one from a handler deadlocks the whole session.
"""
from __future__ import annotations

import threading
from typing import Any, Callable

__all__ = ['WINDOW_KWARGS', 'PopupHost', 'popup_url']

# Extra ``webview.create_window`` options for this platform.  GTK ignores
# ``resize()`` on a non-resizable window, so the content-driven height would
# never be applied; a frameless window has no visible grips to drag anyway.
WINDOW_KWARGS = {'resizable': True}

_MARGIN = 8

# Seconds between safety-net checks in the dismiss watch; see watch_dismiss().
_WATCH_HEARTBEAT = 2.0

# Escape, as reported by Gdk key events.
_KEY_ESCAPE = 0xFF1B


def popup_url(path: Any) -> str:
    """Return the URL to load the popup document from.

    WebKitGTK needs a real ``file://`` URI - handed a bare path it silently
    stays on ``about:blank``, and the injected ``init()`` call then fails with
    a ReferenceError.
    """
    return path.as_uri()


class PopupHost:
    """Window mechanics for the popup on Linux."""

    def __init__(self, window: Any, width: int) -> None:
        self._window = window
        self._width = width
        self._height = 0
        self._keep_position = False
        self._dismissed = threading.Event()
        self._should_dismiss: Callable[[], bool] = lambda: False
        self._drag_offset = (0, 0)
        self._dragging = False

    def prepare(self) -> None:
        """Put the window on screen but fully transparent.

        The content must be laid out before its height is known, and
        pywebview's JS bridge only completes once the window is shown.
        Zero opacity keeps the resize and move that follow invisible.
        """
        self._on_main_loop(lambda gtk_window: gtk_window.set_opacity(0.0))
        self._window.show()

    def reveal(self) -> None:
        """Map, position, focus and finally show the prepared window."""
        self._on_main_loop(self._reveal_on_main_loop)

    def apply_geometry(self, height: int, *, keep_position: bool) -> None:
        """Resize to *height* and remember whether the anchor still applies.

        Only the resize happens here.  Positioning is deferred to
        :meth:`reveal`, because a move on an unmapped window is discarded;
        once the window is mapped, the move is applied immediately.
        """
        self._height = height
        self._keep_position = keep_position
        self._window.resize(self._width, height)

        if not keep_position:
            self._on_main_loop(self._move_to_anchor)

    def watch_dismiss(self, should_dismiss: Callable[[], bool], is_running: Callable[[], bool]) -> None:
        """Block until the popup should close or :meth:`stop_watch` is called.

        Dismissal is driven by two GTK signals: ``focus-out-event`` covers
        clicks elsewhere and window switches, ``key-press-event`` covers
        Escape.  Both only close the popup when *should_dismiss* agrees, so a
        pinned popup stays put.
        """
        self._should_dismiss = should_dismiss
        self._dismissed.clear()
        self._on_main_loop(self._connect_dismiss_signals)

        # Every close path calls stop_watch(), so the event is what actually
        # ends this wait.  The timeout is only a safety net for a caller that
        # stops running without saying so - a pinned popup can live for days,
        # and there is no reason to wake up often for that.
        while is_running() and not self._dismissed.wait(_WATCH_HEARTBEAT):
            pass

    def stop_watch(self) -> None:
        """Wake :meth:`watch_dismiss` so it can return."""
        self._dismissed.set()

    def begin_drag(self) -> bool:
        """Anchor the pointer to the window for a pinned-popup drag."""
        position = self._pointer_and_origin()
        if position is None:
            return False

        pointer_x, pointer_y, window_x, window_y = position
        self._drag_offset = (pointer_x - window_x, pointer_y - window_y)
        self._dragging = True

        return True

    def drag(self) -> bool:
        """Reposition the popup so the pointer keeps its initial grab offset.

        Each step computes the absolute window position from the current
        pointer position, so out-of-order calls converge on the right spot
        instead of accumulating drift.
        """
        if not self._dragging:
            return False

        position = self._pointer_and_origin()
        if position is None:
            return False

        pointer_x, pointer_y, _, _ = position
        target = (pointer_x - self._drag_offset[0], pointer_y - self._drag_offset[1])
        self._on_main_loop(lambda gtk_window: gtk_window.move(*target))

        return True

    def end_drag(self, height: int) -> None:
        """Finish a drag.

        GTK reports and consumes logical pixels on both sides, so unlike the
        Windows host there is no cross-monitor DPI correction to make.
        """
        self._dragging = False
        self._height = height

    def _reveal_on_main_loop(self, gtk_window: Any) -> None:
        """Map, position and focus the window.  Runs on the GTK main loop."""
        gtk_window.set_keep_above(True)
        gtk_window.show_all()
        gtk_window.present()

        if not self._keep_position:
            self._move_to_anchor(gtk_window)

        gtk_window.set_accept_focus(True)
        self._force_focus(gtk_window)
        gtk_window.set_opacity(1.0)

    def _force_focus(self, gtk_window: Any) -> None:
        """Take the input focus, so the window can later report losing it.

        Best effort: a session that refuses the focus request still gets a
        usable popup, it just has to be closed explicitly.
        """
        gdk_window = gtk_window.get_window()
        if gdk_window is None:
            return

        try:
            from gi.repository import Gdk
        except ImportError:
            return

        gdk_window.focus(Gdk.CURRENT_TIME)

    def _move_to_anchor(self, gtk_window: Any) -> None:
        """Move the window to the work area's tray corner.  Runs on the main loop."""
        anchor = self._anchor()
        if anchor is not None:
            gtk_window.move(*anchor)

    def _anchor(self) -> tuple[int, int] | None:
        """Return absolute logical coordinates for the popup's resting place.

        StatusNotifierItem never reports where the panel drew the icon - no
        protocol carries that - so the popup anchors to the work-area corner
        the icon sits in.
        """
        monitor = self._primary_monitor()
        if monitor is None:
            return None

        work = monitor.get_workarea()

        return work.x + work.width - self._width - _MARGIN, work.y + _MARGIN

    def _connect_dismiss_signals(self, gtk_window: Any) -> None:
        """Wire the dismissal signals.  Runs on the GTK main loop."""
        gtk_window.connect('focus-out-event', self._on_focus_out)
        gtk_window.connect('key-press-event', self._on_key_press)

    def _on_focus_out(self, *_args: object) -> bool:
        if self._should_dismiss():
            self._dismissed.set()

        return False

    def _on_key_press(self, _widget: Any, event: Any) -> bool:
        if getattr(event, 'keyval', None) == _KEY_ESCAPE and self._should_dismiss():
            self._dismissed.set()

        return False

    def _pointer_and_origin(self) -> tuple[int, int, int, int] | None:
        """Return ``(pointer_x, pointer_y, window_x, window_y)`` in logical pixels."""
        gtk_window = self._gtk_window()
        if gtk_window is None:
            return None

        display = self._display()
        if display is None:
            return None

        seat = display.get_default_seat()
        if seat is None:
            return None

        _screen, pointer_x, pointer_y = seat.get_pointer().get_position()
        window_x, window_y = gtk_window.get_position()

        return pointer_x, pointer_y, window_x, window_y

    def _gtk_window(self) -> Any:
        """Return the ``Gtk.Window`` behind the pywebview window, or None."""
        try:
            from webview.platforms.gtk import BrowserView

            return BrowserView.instances[self._window.uid].window
        except Exception:
            return None

    def _display(self) -> Any:
        """Return the default Gdk display, or None without a session."""
        try:
            import gi

            gi.require_version('Gdk', '3.0')
            from gi.repository import Gdk

            return Gdk.Display.get_default()
        except (ImportError, ValueError):
            return None

    def _primary_monitor(self) -> Any:
        """Return the monitor the panel lives on, or None without a display."""
        display = self._display()
        if display is None:
            return None

        return display.get_primary_monitor() or (display.get_monitor(0) if display.get_n_monitors() else None)

    def _on_main_loop(self, action: Callable[[Any], None]) -> None:
        """Run *action* against the GTK window on the main loop.

        Every GTK call has to happen there, and the callers are worker
        threads.  Scheduling instead of blocking is also what keeps a handler
        from waiting on the loop it is running in.
        """
        try:
            from gi.repository import GLib
        except ImportError:
            return

        def run() -> bool:
            gtk_window = self._gtk_window()
            if gtk_window is not None:
                action(gtk_window)

            return False

        GLib.idle_add(run)

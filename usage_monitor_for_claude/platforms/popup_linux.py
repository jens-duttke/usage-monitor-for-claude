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
import time
from typing import Any, Callable

__all__ = ['PREWARM_WINDOW', 'REUSE_WINDOW', 'WINDOW_KWARGS', 'PopupHost', 'popup_url']

# Extra ``webview.create_window`` options for this platform.  GTK ignores
# ``resize()`` on a non-resizable window, so the content-driven height would
# never be applied; a frameless window has no visible grips to drag anyway.
WINDOW_KWARGS = {'resizable': True}

# Whether one popup window is kept and reloaded for every opening.  On GTK
# every pywebview window starts its own WebKit web and network process, and
# ``destroy()`` does not stop them, so each destroyed window would hold about
# 200 MB until the app quits.  See :class:`~usage_monitor_for_claude.popup.PopupWindowCache`.
REUSE_WINDOW = True
PREWARM_WINDOW = False

_MARGIN = 8

_BASELINE_DPI = 96

# Relative deviation from _BASELINE_DPI below which WebKitGTK leaves the page
# zoom at 1.0; see _css_scale().
_ZOOM_THRESHOLD = 0.02

# First WebKitGTK (major, minor) that zooms pages by the font resolution.
_WEBKIT_DPI_ZOOM_VERSION = (2, 46)

# Seconds between safety-net checks in the dismiss watch; see watch_dismiss().
_WATCH_HEARTBEAT = 2.0

# Escape, as reported by Gdk key events.
_KEY_ESCAPE = 0xFF1B

# Seconds after the reveal during which a focus-out is not a dismissal.  The
# compositor reports one for a window it has just mapped; whether it lands
# before or after the reveal depends on how quickly the page answered, and a
# popup shown again answers quickly enough to lose that race.
_FOCUS_SETTLE_SECONDS = 0.5


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
        self._scale = 1.0
        self._keep_position = False
        self._dismissed = threading.Event()
        self._should_dismiss: Callable[[], bool] = lambda: False
        self._drag_offset = (0, 0)
        self._dragging = False
        self._signals_connected = False
        self._focus_settled_at = 0.0
        self._reveal_count = 0

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
        self._focus_settled_at = time.monotonic() + _FOCUS_SETTLE_SECONDS
        self._reveal_count += 1
        self._on_main_loop(self._reveal_on_main_loop)

    def hide(self) -> None:
        """Take the popup off screen but keep its window for the next opening."""
        self._dragging = False
        self._on_main_loop(lambda gtk_window: gtk_window.hide())

    def apply_geometry(self, height: int, *, keep_position: bool) -> None:
        """Resize to *height* and remember whether the anchor still applies.

        *height* is in CSS pixels; the window gets the content size times
        :meth:`_css_scale`.  Only the resize happens here.  Positioning is
        deferred to :meth:`reveal`, because a move on an unmapped window is
        discarded; once the window is mapped, the move is applied immediately.
        """
        self._scale = self._css_scale()
        self._height = round(height * self._scale)
        self._keep_position = keep_position
        self._window.resize(round(self._width * self._scale), self._height)

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
        self._height = round(height * self._scale)

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
        the icon sits in.  That is the top corner unless the work area gives
        positive evidence of a bottom panel: no space reserved at the top and
        space reserved at the bottom.  A work area equal to the monitor
        geometry says nothing about the panel - a fullscreen window, an
        auto-hiding panel and a missing primary monitor all look like that -
        and neither does a side panel, so those keep the top corner.
        """
        monitor = self._primary_monitor()
        if monitor is None:
            return None

        work = monitor.get_workarea()
        geometry = monitor.get_geometry()
        x = work.x + work.width - round(self._width * self._scale) - _MARGIN

        bottom_panel = work.y == geometry.y and work.y + work.height < geometry.y + geometry.height
        if bottom_panel:
            return x, work.y + work.height - self._height - _MARGIN

        return x, work.y + _MARGIN

    def _connect_dismiss_signals(self, gtk_window: Any) -> None:
        """Wire the dismissal signals once.  Runs on the GTK main loop.

        The window outlives a single opening, so later watches reuse the
        handlers; connecting again would stack one more per opening.
        """
        if self._signals_connected:
            return

        self._signals_connected = True
        gtk_window.connect('focus-out-event', self._on_focus_out)
        gtk_window.connect('key-press-event', self._on_key_press)

    def _on_focus_out(self, gtk_window: Any = None, *_args: object) -> bool:
        remaining = self._focus_settled_at - time.monotonic()
        if remaining > 0:
            self._recheck_focus_after(gtk_window, remaining)

            return False

        if self._should_dismiss():
            self._dismissed.set()

        return False

    def _recheck_focus_after(self, gtk_window: Any, delay: float) -> None:
        """Dismiss once the settle period is over if the window still has no focus.

        A focus-out inside the period is usually the compositor settling, but it
        can also be the user clicking away at once, and ignoring that would leave
        the popup open without a way to dismiss it.  Runs on the GTK main loop.
        """
        if gtk_window is None:
            return

        try:
            from gi.repository import GLib
        except ImportError:
            return

        reveal_count = self._reveal_count

        def check() -> bool:
            # A later opening has its own period and its own check.
            if reveal_count == self._reveal_count and not gtk_window.has_toplevel_focus() and self._should_dismiss():
                self._dismissed.set()

            return False

        GLib.timeout_add(round(delay * 1000) + 50, check)

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

    def _css_scale(self) -> float:
        """Return how many window pixels one CSS pixel of the popup needs.

        WebKitGTK zooms every page by the font resolution over 96 dpi
        (``refreshInternalScaling()`` in ``WebKitWebViewBase.cpp``), while GTK
        scales the window only by its integer scale factor.  At 250% on X11
        (``Xft.dpi`` 240, GTK scale 2) GDK reports 120 dpi, the page is zoomed
        by 1.25 on top of the scale factor, and a window sized to the content
        would cut its bottom off.  A text scaling factor has the same effect,
        on every backend.

        On GTK 3 WebKit reads the resolution through the same
        ``gdk_screen_get_resolution()`` call, and it keeps the zoom at 1.0 for
        a resolution within ``_ZOOM_THRESHOLD`` of 96 dpi, so this does too.
        """
        if not self._webkit_zooms_by_dpi():
            return 1.0

        display = self._display()
        if display is None:
            return 1.0

        # GDK reports -1 when no resolution is configured.
        resolution = display.get_default_screen().get_resolution()
        if resolution <= 0:
            return 1.0

        scale = resolution / _BASELINE_DPI
        if abs(scale - 1) <= _ZOOM_THRESHOLD:
            return 1.0

        return scale

    def _webkit_zooms_by_dpi(self) -> bool:
        """Return whether the WebKitGTK in use zooms pages by the font resolution.

        The zoom arrived with WebKitGTK 2.46 (WebKit commit 5713584438, bug
        247980).  Before that the resolution scaled text only and a CSS pixel
        stayed one window pixel, so enlarging the window would leave blank
        space.  pywebview has already chosen the ``WebKit2`` version by the
        time a popup exists, so the import gets the library actually in use.
        """
        try:
            from gi.repository import WebKit2
        except ImportError:
            return False

        return (WebKit2.get_major_version(), WebKit2.get_minor_version()) >= _WEBKIT_DPI_ZOOM_VERSION

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

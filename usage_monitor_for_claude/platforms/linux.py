"""
Linux Backend
==============

Linux implementations of the platform API, targeting a freedesktop session
(GNOME, KDE, wlroots compositors).  Imported on every non-Windows system;
see :mod:`usage_monitor_for_claude.platforms` for the dispatch.

``gi`` is imported lazily inside the functions that need it, so this module
stays importable in a plain virtual environment - the test suite depends on
that.  Every helper degrades to a documented fallback when the session bus
or GTK is unavailable rather than raising.
"""
from __future__ import annotations

import functools
import locale
import os
import platform
import shlex
import sys
import time
from pathlib import Path
from typing import Any, Callable

from PIL import ImageFont

from ..instance_id import config_dir_suffix, effective_config_dir, is_default_config_dir

__all__ = [
    'AUTOSTART_DIRECTORY', 'DIAGNOSTIC_PACKAGES', 'ask_yes_no', 'diagnostic_display_rows',
    'autostart_supported', 'install_tray_click_handler', 'prepare_gui_environment', 'set_dpi_awareness',
    'diagnostic_post_init_rows', 'diagnostic_runtime_rows', 'diagnostic_system_rows',
    'double_click_seconds', 'get_idle_seconds', 'is_autostart_enabled',
    'is_screensaver_running', 'is_workstation_locked', 'load_font', 'no_window_kwargs', 'register_notification_identity',
    'set_autostart', 'setup_console', 'show_error_box', 'show_topmost_error', 'show_warning_box',
    'sync_autostart_path', 'system_time_format', 'taskbar_uses_light_theme', 'watch_theme_change',
]

# Third-party packages worth reporting in the diagnostics output.  pythonnet
# and clr-loader are part of the Windows WebView2 host and never installed here.
DIAGNOSTIC_PACKAGES = ('pywebview', 'PyGObject', 'pystray', 'Pillow', 'requests')

# XDG autostart: a .desktop file here is launched when the session starts.
AUTOSTART_DIRECTORY = Path.home() / '.config' / 'autostart'
AUTOSTART_BASE_NAME = 'usage-monitor-for-claude'
APPLICATION_NAME = 'Usage Monitor for Claude'

# Session-bus endpoints. GNOME's Mutter reports idle time even on Wayland,
# where no X11 equivalent of GetLastInputInfo exists.
_IDLE_SERVICE = ('org.gnome.Mutter.IdleMonitor', '/org/gnome/Mutter/IdleMonitor/Core')
_SCREENSAVER_SERVICE = ('org.gnome.ScreenSaver', '/org/gnome/ScreenSaver')

_PORTAL_SERVICE = ('org.freedesktop.portal.Desktop', '/org/freedesktop/portal/desktop')
_APPEARANCE_NAMESPACE = 'org.freedesktop.appearance'
_COLOR_SCHEME_KEY = 'color-scheme'
_COLOR_SCHEME_LIGHT = 2  # 0 = no preference, 1 = prefer dark, 2 = prefer light

_DBUS_TIMEOUT_MS = 2000
_DEFAULT_DOUBLE_CLICK_SECONDS = 0.4

# The portal emits a SettingChanged signal, but delivering it needs a GLib main
# context on this thread, which would collide with the GTK loop the app already
# runs.  Polling a cached proxy costs one cheap DBus round trip per interval.
_THEME_POLL_SECONDS = 5.0

# Font families shipped by every mainstream desktop; the first match wins.
_FONT_DIRECTORIES = ('/usr/share/fonts/truetype/dejavu', '/usr/share/fonts/truetype/ubuntu')
_TEXT_FONTS = ('DejaVuSans-Bold.ttf', 'Ubuntu-B.ttf')
_SYMBOL_FONTS = ('DejaVuSans.ttf', 'NotoSansSymbols2-Regular.ttf')

# Cached proxies; a failed lookup is retried, an unavailable ``gi`` is not.
_proxies: dict[tuple[tuple[str, str], str | None], Any] = {}
_gi_available: bool | None = None


def no_window_kwargs() -> dict[str, Any]:
    """Return ``subprocess`` keyword arguments that suppress a console window.

    POSIX has no console-window concept, so no flags are needed.
    """
    return {}


def show_error_box(message: str, title: str) -> None:
    """Show a modal error dialog, falling back to stderr without a display."""
    _show_box(message, title, error=True)


def show_warning_box(message: str, title: str) -> None:
    """Show a modal warning dialog, falling back to stderr without a display."""
    _show_box(message, title, error=False)


def ask_yes_no(message: str, title: str) -> bool:
    """Ask a yes/no question in a modal dialog.

    Without a reachable display there is nobody to answer, so the question
    is declined - the safe direction, since every caller uses "yes" to
    replace or terminate something.
    """
    gi = _import_gi()
    if gi is None:
        return False

    try:
        gi.require_version('Gtk', '3.0')
        from gi.repository import Gtk
    except (ImportError, ValueError):
        return False

    if not Gtk.init_check()[0]:
        return False

    dialog = Gtk.MessageDialog(
        transient_for=None, modal=True, message_type=Gtk.MessageType.QUESTION,
        buttons=Gtk.ButtonsType.YES_NO, text=title, secondary_text=message,
    )
    dialog.set_title(title)
    dialog.set_keep_above(True)
    response = dialog.run()
    dialog.destroy()
    _drain_gtk_events()

    return response == Gtk.ResponseType.YES


def show_topmost_error(message: str, title: str) -> None:
    """Show an error dialog, used while no window exists yet."""
    show_error_box(message, title)


def _show_box(message: str, title: str, error: bool) -> None:
    """Show a GTK dialog, or print to stderr when no display is reachable."""
    if not _try_gtk_dialog(message[:2000], title, error):
        print(f'{title}: {message}', file=sys.stderr, flush=True)


def system_time_format() -> str:
    """Return ``'24h'`` or ``'12h'`` for the current locale.

    Inspects the locale's time format for an AM/PM marker.  Falls back to
    ``'24h'`` when the locale provides no time format.
    """
    try:
        pattern = locale.nl_langinfo(locale.T_FMT)
    except (AttributeError, ValueError):
        return '24h'

    if not pattern:
        return '24h'

    return '12h' if ('%p' in pattern or '%I' in pattern) else '24h'


def double_click_seconds() -> float:
    """Return the session's double-click interval in seconds."""
    settings = _gtk_settings()
    if settings is None:
        return _DEFAULT_DOUBLE_CLICK_SECONDS

    try:
        millis = settings.get_property('gtk-double-click-time')
    except Exception:
        return _DEFAULT_DOUBLE_CLICK_SECONDS

    return millis / 1000.0 if millis else _DEFAULT_DOUBLE_CLICK_SECONDS


def get_idle_seconds() -> float:
    """Return seconds since the last keyboard or mouse input, 0.0 on failure.

    Reads ``org.gnome.Mutter.IdleMonitor``, which works under both X11 and
    Wayland.  A session without that service reports 0.0, which keeps the
    idle-pause feature inactive rather than pausing polling incorrectly.
    """
    millis = _call_session_method(_IDLE_SERVICE, 'GetIdletime')

    return 0.0 if millis is None else float(millis) / 1000.0


def is_workstation_locked() -> bool:
    """Return True if the session's screen lock is active.

    An unreachable screensaver service reports False - treating an unknown
    state as "locked" would suppress notifications indefinitely.
    """
    return bool(_call_session_method(_SCREENSAVER_SERVICE, 'GetActive'))


def is_screensaver_running() -> bool:
    """Return False - a running screensaver is already reported as locked.

    ``org.gnome.ScreenSaver.GetActive`` is True as soon as the screen is
    blanked, whether or not unlocking asks for a password, so
    :func:`is_workstation_locked` covers this state on its own.
    """
    return False


def _import_gi() -> Any:
    """Return the ``gi`` module, or None when PyGObject is unavailable."""
    global _gi_available
    if _gi_available is False:
        return None

    try:
        import gi
    except ImportError:
        _gi_available = False
        return None

    _gi_available = True

    return gi


def _call_session_method(service: tuple[str, str], method: str) -> Any:
    """Call a no-argument session-bus method, returning None when unavailable."""
    proxy = _session_proxy(service)
    if proxy is None:
        return None

    from gi.repository import Gio, GLib

    try:
        result = proxy.call_sync(method, None, Gio.DBusCallFlags.NONE, _DBUS_TIMEOUT_MS, None)
    except GLib.Error:
        return None

    unpacked = result.unpack()

    return unpacked[0] if unpacked else None


def _session_proxy(service: tuple[str, str], interface: str | None = None) -> Any:
    """Return a cached DBus proxy for *service*, or None when unavailable.

    *interface* defaults to the bus name, which holds for the GNOME services;
    the portal exposes its settings under a different interface name.
    """
    cache_key = (service, interface)
    if cache_key in _proxies:
        return _proxies[cache_key]

    if _import_gi() is None:
        return None

    from gi.repository import Gio, GLib

    name, path = service
    try:
        proxy = Gio.DBusProxy.new_for_bus_sync(
            Gio.BusType.SESSION, Gio.DBusProxyFlags.NONE, None, name, path, interface or name, None,
        )
    except GLib.Error:
        return None

    _proxies[cache_key] = proxy

    return proxy


def _gtk_settings() -> Any:
    """Return the default ``Gtk.Settings``, or None without a display."""
    gi = _import_gi()
    if gi is None:
        return None

    try:
        gi.require_version('Gtk', '3.0')
        from gi.repository import Gtk

        return Gtk.Settings.get_default()
    except (ImportError, ValueError):
        return None


def _try_gtk_dialog(message: str, title: str, error: bool = True) -> bool:
    """Show a GTK error dialog.  Returns False when GTK is unavailable."""
    gi = _import_gi()
    if gi is None:
        return False

    try:
        gi.require_version('Gtk', '3.0')
        from gi.repository import Gtk
    except (ImportError, ValueError):
        return False

    if not Gtk.init_check()[0]:
        return False

    message_type = Gtk.MessageType.ERROR if error else Gtk.MessageType.WARNING
    dialog = Gtk.MessageDialog(
        transient_for=None, modal=True, message_type=message_type,
        buttons=Gtk.ButtonsType.OK, text=title, secondary_text=message,
    )
    dialog.set_title(title)
    dialog.run()
    dialog.destroy()
    _drain_gtk_events()

    return True


def _drain_gtk_events() -> None:
    """Process pending GTK events so a dismissed dialog is really gone."""
    from gi.repository import Gtk

    deadline = time.monotonic() + 1.0
    while Gtk.events_pending() and time.monotonic() < deadline:
        Gtk.main_iteration_do(False)


@functools.lru_cache(maxsize=None)
def load_font(size: int, symbol: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """Load font at given size. Use symbol=True for Unicode glyphs not in the text face."""
    names = _SYMBOL_FONTS if symbol else _TEXT_FONTS
    for directory in _FONT_DIRECTORIES:
        for name in names:
            try:
                return ImageFont.truetype(str(Path(directory) / name), size)
            except OSError:
                continue

    return ImageFont.load_default()


def taskbar_uses_light_theme() -> bool:
    """Return True if the panel uses the light theme.

    Reads ``color-scheme`` from the XDG desktop portal, which works under both
    X11 and Wayland.  Returns False (dark) when the portal is unreachable or
    reports no preference - panels default to dark on the mainstream desktops.
    """
    return _read_color_scheme() == _COLOR_SCHEME_LIGHT


def watch_theme_change(callback: Callable[[], None]) -> None:
    """Block the current thread and call *callback* whenever the panel theme changes.

    Designed to run in a daemon thread.  Returns immediately when the portal
    is unreachable, which leaves the icon at its startup theme rather than
    spinning on a service that will never answer.
    """
    previous = _read_color_scheme()
    if previous is None:
        return

    while True:
        time.sleep(_THEME_POLL_SECONDS)
        current = _read_color_scheme()
        if current is None or current == previous:
            continue

        previous = current
        try:
            callback()
        except Exception:
            # A transient callback failure (icon re-render, tray reconnect after
            # a shell restart) must not end theme watching for the session.
            pass


def _read_color_scheme() -> int | None:
    """Return the portal's ``color-scheme`` value, or None when unavailable."""
    proxy = _session_proxy(_PORTAL_SERVICE, 'org.freedesktop.portal.Settings')
    if proxy is None:
        return None

    from gi.repository import Gio, GLib

    try:
        result = proxy.call_sync(
            'Read', GLib.Variant('(ss)', (_APPEARANCE_NAMESPACE, _COLOR_SCHEME_KEY)),
            Gio.DBusCallFlags.NONE, _DBUS_TIMEOUT_MS, None,
        )
    except GLib.Error:
        return None

    unpacked = result.unpack()

    return unpacked[0] if unpacked else None


def _autostart_file() -> Path:
    """Return the per-instance .desktop path."""
    return AUTOSTART_DIRECTORY / f'{AUTOSTART_BASE_NAME}{config_dir_suffix()}.desktop'


def _autostart_command() -> str:
    """Return the Exec line for this instance.

    A frozen build is its own executable; running from source needs the
    interpreter plus the module, or the entry would not start.
    """
    if getattr(sys, 'frozen', False):
        command = shlex.quote(sys.executable)
    else:
        command = f'{shlex.quote(sys.executable)} -m usage_monitor_for_claude'

    if not is_default_config_dir():
        command += f' --config-dir={shlex.quote(str(effective_config_dir()))}'

    return command


def _autostart_entry() -> str:
    """Return the full .desktop file contents for this instance.

    A source checkout needs ``Path``: the session starts applications from the
    home directory, and ``-m usage_monitor_for_claude`` only resolves with the
    project root as the working directory.
    """
    lines = [
        '[Desktop Entry]',
        'Type=Application',
        f'Name={APPLICATION_NAME}',
        f'Exec={_autostart_command()}',
    ]
    if not getattr(sys, 'frozen', False):
        lines.append(f'Path={_project_root()}')
    lines += ['Terminal=false', 'X-GNOME-Autostart-enabled=true']

    return '\n'.join(lines) + '\n'


def _project_root() -> Path:
    """Return the checkout directory the package is imported from."""
    return Path(__file__).resolve().parent.parent.parent


def autostart_supported() -> bool:
    """Whether an autostart entry can be written that actually starts the app.

    True either way here: the .desktop entry carries both the interpreter and
    the working directory, so a source checkout starts as reliably as a
    packaged build.
    """
    return True


def prepare_gui_environment() -> None:
    """Select the X11 backend unless the session was told otherwise.

    Wayland does not let a client place its own windows, so the popup could
    not be anchored to the tray corner; running as an XWayland client
    restores that.  An explicit ``GDK_BACKEND`` is left untouched, so the
    native backend can still be tried on purpose.
    """
    os.environ.setdefault('GDK_BACKEND', 'x11')


def is_autostart_enabled() -> bool:
    """Return True if this instance has an XDG autostart entry."""
    return _autostart_file().is_file()


def set_autostart(enable: bool) -> None:
    """Create or remove the XDG autostart entry."""
    path = _autostart_file()
    if not enable:
        path.unlink(missing_ok=True)
        return

    AUTOSTART_DIRECTORY.mkdir(parents=True, exist_ok=True)
    path.write_text(_autostart_entry(), encoding='utf-8')


def sync_autostart_path() -> None:
    """Rewrite the autostart entry if the executable has been moved."""
    path = _autostart_file()
    try:
        stored = path.read_text(encoding='utf-8')
    except OSError:
        return

    if stored != _autostart_entry():
        set_autostart(True)


def register_notification_identity() -> None:
    """No-op.

    The Windows counterpart pins a toast identity so notifications do not
    borrow the live tray icon.  Freedesktop notifications carry their icon
    per message instead, so there is no process-wide identity to register.
    """


def setup_console() -> None:
    """Enable verbose console output.

    A POSIX process already inherits usable standard streams, so unlike the
    Windows counterpart there is no console to attach or allocate.
    """
    os.environ['PYWEBVIEW_LOG'] = 'DEBUG'


def diagnostic_system_rows() -> list[tuple[str, str]]:
    """Return the platform's rows for the System section."""
    return [
        ('OS', f'{platform.platform()} ({_os_release_name()})'),
        ('Architecture', platform.machine()),
        ('Root', 'Yes' if os.geteuid() == 0 else 'No'),
        ('Desktop', os.environ.get('XDG_CURRENT_DESKTOP') or 'unknown'),
        ('Session type', os.environ.get('XDG_SESSION_TYPE') or 'unknown'),
    ]


def diagnostic_display_rows() -> list[tuple[str, str]]:
    """Return the platform's rows for the Display section."""
    rows = [('GDK backend', os.environ.get('GDK_BACKEND') or '(auto)')]

    display = _gdk_display()
    if display is None:
        rows.append(('Monitors', 'unavailable (no display)'))
        return rows

    count = display.get_n_monitors()
    rows.append(('Monitors', str(count)))

    monitor = display.get_primary_monitor() or (display.get_monitor(0) if count else None)
    if monitor is None:
        return rows

    geometry = monitor.get_geometry()
    work = monitor.get_workarea()
    rows.append(('Primary resolution', f'{geometry.width} x {geometry.height}'))
    rows.append(('Scale factor', str(monitor.get_scale_factor())))
    rows.append(('Work area', f'{work.width} x {work.height} (left={work.x}, top={work.y})'))

    return rows


def diagnostic_runtime_rows() -> list[tuple[str, str]]:
    """Return the platform's rows for the Runtimes section."""
    return [
        ('GTK', _gtk_version()),
        ('WebKit2GTK', _webkit_version()),
        ('AppIndicator', _appindicator_version()),
        ('Notifications', _notify_version()),
    ]


def diagnostic_post_init_rows() -> list[tuple[str, str]]:
    """Return rows that are only available once the GUI toolkit has loaded."""
    gi = _import_gi()
    if gi is None:
        return [('PyGObject', 'not available')]

    return [('PyGObject', getattr(gi, '__version__', 'unknown'))]


def _os_release_name() -> str:
    """Return the distribution's pretty name from /etc/os-release."""
    try:
        content = Path('/etc/os-release').read_text(encoding='utf-8')
    except OSError:
        return 'unknown distribution'

    for line in content.splitlines():
        if line.startswith('PRETTY_NAME='):
            return line.partition('=')[2].strip().strip('"')

    return 'unknown distribution'


def _gtk_version() -> str:
    """Report the GTK version behind the popup window."""
    def load() -> Any:
        from gi.repository import Gtk

        return Gtk

    return _toolkit_version('Gtk', '3.0', load)


def _webkit_version() -> str:
    """Report the WebKit version that renders the popup content."""
    def load() -> Any:
        from gi.repository import WebKit2

        return WebKit2

    return _toolkit_version('WebKit2', '4.1', load)


def _appindicator_version() -> str:
    """Report whether the tray icon's typelib is installed.

    Without it no icon appears at all, which is the single most common
    reason for a "nothing happens" report on Linux.
    """
    def load() -> Any:
        from gi.repository import AyatanaAppIndicator3

        return AyatanaAppIndicator3

    return _toolkit_version('AyatanaAppIndicator3', '0.1', load)


def _notify_version() -> str:
    """Report whether desktop notifications are available."""
    def load() -> Any:
        from gi.repository import Notify

        return Notify

    return _toolkit_version('Notify', '0.7', load)


def _toolkit_version(namespace: str, version: str, load: Callable[[], Any]) -> str:
    """Report the version of an introspected library, or why it is unavailable.

    *load* performs the one explicit ``from gi.repository import X`` for this
    namespace.  Importing by name would be a dynamic import, which this
    project does not use - a reader must be able to see every import.
    """
    gi = _import_gi()
    if gi is None:
        return 'not available (no PyGObject)'

    try:
        gi.require_version(namespace, version)
        module = load()
    except (ImportError, ValueError):
        return 'not found'

    if not all(hasattr(module, name) for name in ('MAJOR_VERSION', 'MINOR_VERSION', 'MICRO_VERSION')):
        return f'{version} (available)'

    return f'{module.MAJOR_VERSION}.{module.MINOR_VERSION}.{module.MICRO_VERSION}'


def _gdk_display() -> Any:
    """Return the default Gdk display, or None without a session."""
    gi = _import_gi()
    if gi is None:
        return None

    try:
        gi.require_version('Gdk', '3.0')
        from gi.repository import Gdk
    except (ImportError, ValueError):
        return None

    return Gdk.Display.get_default()


def install_tray_click_handler(
    icon: Any, on_single_click: Callable[[], None], on_double_click: Callable[[], None],
) -> bool:
    """Report that double-click handling is unavailable.

    A StatusNotifierItem is drawn and driven by the panel, not by the
    application: libayatana-appindicator sets ``HAS_DEFAULT_ACTION`` to False,
    so a click opens the menu and no button event ever reaches this process.
    There is nothing to defer or swallow, and the double-click command is
    surfaced through the menu instead.
    """
    return False


def set_dpi_awareness() -> None:
    """No-op.

    GTK reads the scale factor from the display and reports logical pixels on
    both sides, so there is no process-wide awareness mode to opt into.
    """

"""
Windows Backend
================

Win32 implementations of the platform API.  Imported only on Windows;
see :mod:`usage_monitor_for_claude.platforms` for the dispatch.
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes
import functools
import msvcrt
import os
import platform
import subprocess
import sys
import threading
import winreg
from pathlib import Path
from typing import Any, Callable, TextIO

from PIL import ImageFont

from ..instance_id import config_dir_suffix, effective_config_dir, is_default_config_dir

__all__ = [
    'AUTOSTART_REG_BASE_NAME', 'AUTOSTART_REG_KEY', 'DIAGNOSTIC_PACKAGES', 'ask_yes_no',
    'autostart_supported', 'install_tray_click_handler', 'prepare_gui_environment', 'set_dpi_awareness',
    'diagnostic_display_rows', 'diagnostic_post_init_rows', 'diagnostic_runtime_rows',
    'diagnostic_system_rows', 'double_click_seconds', 'get_idle_seconds',
    'is_autostart_enabled', 'is_screensaver_running', 'is_workstation_locked', 'load_font', 'no_window_kwargs',
    'register_notification_identity', 'set_autostart', 'show_error_box', 'show_warning_box',
    'setup_console', 'show_topmost_error', 'sync_autostart_path', 'system_time_format',
    'taskbar_uses_light_theme', 'watch_theme_change',
]

# Third-party packages worth reporting in the diagnostics output.
DIAGNOSTIC_PACKAGES = ('pywebview', 'pythonnet', 'clr-loader', 'pystray', 'Pillow', 'requests')

# WebView2 registry GUIDs (runtime, beta, dev, canary)
_WEBVIEW2_GUIDS = [
    ('{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}', 'Runtime'),
    ('{2CD8A007-E189-409D-A2C8-9AF4EF3C72AA}', 'Beta'),
    ('{0D50BFEC-CD6A-4F9A-964C-C7416E3ACB10}', 'Developer'),
    ('{65C35B14-6C1D-4122-AC46-7148CC9D6497}', 'Canary'),
]

AUTOSTART_REG_KEY = r'Software\Microsoft\Windows\CurrentVersion\Run'
AUTOSTART_REG_BASE_NAME = 'UsageMonitorForClaude'

# Stable per-application identity.  Every instance (one per Claude account)
# shares it, so notifications group under one name and logo.
APP_USER_MODEL_ID = 'JensDuttke.UsageMonitorForClaude'
DISPLAY_NAME = 'Usage Monitor for Claude'

# Neutral branded logo (empty usage bars) shown as the notification icon.
# A multi-size .ico (16-256 px) so Windows picks a crisp frame for the small
# toast header instead of downscaling a single large image.
_NOTIFICATION_LOGO = Path(__file__).resolve().parent.parent / 'notification_logo.ico'

# HKCU key the shell reads to resolve the identity's display name and icon.
# A registry entry is enough - no Start Menu shortcut is required.
_IDENTITY_REG_PATH = r'Software\Classes\AppUserModelId\{}'.format(APP_USER_MODEL_ID)

_MB_ICONERROR = 0x10
_MB_ICONWARNING = 0x30
_MB_YESNO = 0x04
_MB_ICONQUESTION = 0x20
_MB_TOPMOST = 0x40000
_IDYES = 6

# Theme registry
THEME_REG_KEY = r'Software\Microsoft\Windows\CurrentVersion\Themes\Personalize'
THEME_REG_VALUE = 'SystemUsesLightTheme'
REG_NOTIFY_CHANGE_LAST_SET = 0x00000004

# Ensure GetTickCount returns unsigned DWORD (default c_int overflows after ~24.8 days of uptime)
ctypes.windll.kernel32.GetTickCount.restype = ctypes.wintypes.DWORD

# Standard handle identifiers, and the GetFileType results that mark a handle
# as redirected (a console reports FILE_TYPE_CHAR instead).
_STD_OUTPUT_HANDLE = -11
_STD_ERROR_HANDLE = -12
_FILE_TYPE_DISK = 0x0001
_FILE_TYPE_PIPE = 0x0003
_INVALID_HANDLE = ctypes.c_void_p(-1).value

# A HANDLE is pointer-sized; the default c_int return would truncate it on 64-bit.
ctypes.windll.kernel32.GetStdHandle.restype = ctypes.wintypes.HANDLE


class _LASTINPUTINFO(ctypes.Structure):
    _fields_ = [
        ('cbSize', ctypes.wintypes.UINT),
        ('dwTime', ctypes.wintypes.DWORD),
    ]


def no_window_kwargs() -> dict[str, Any]:
    """Return ``subprocess`` keyword arguments that suppress a console window."""
    return {'creationflags': subprocess.CREATE_NO_WINDOW}


def show_error_box(message: str, title: str) -> None:
    """Show a modal error dialog."""
    ctypes.windll.user32.MessageBoxW(0, message[:2000], title, _MB_ICONERROR)


def show_warning_box(message: str, title: str) -> None:
    """Show a modal warning dialog."""
    ctypes.windll.user32.MessageBoxW(0, message[:2000], title, _MB_ICONWARNING)


def ask_yes_no(message: str, title: str) -> bool:
    """Ask a yes/no question in a topmost modal dialog.  Returns True for yes."""
    answer = ctypes.windll.user32.MessageBoxW(
        None, message, title, _MB_YESNO | _MB_ICONQUESTION | _MB_TOPMOST,
    )

    return answer == _IDYES


def show_topmost_error(message: str, title: str) -> None:
    """Show a topmost error dialog, used while no window exists yet."""
    ctypes.windll.user32.MessageBoxW(None, message, title, _MB_ICONERROR | _MB_TOPMOST)


def system_time_format() -> str:
    """Return ``'24h'`` or ``'12h'`` for the current user locale.

    Reads ``LOCALE_ITIME``, which returns ``1`` for a 24-hour clock and ``0``
    for a 12-hour (AM/PM) clock and honors regional customizations.  Falls
    back to ``'24h'`` if the query fails.
    """
    LOCALE_NAME_USER_DEFAULT = None  # NULL selects the current user locale
    LOCALE_ITIME = 0x00000023
    LOCALE_RETURN_NUMBER = 0x20000000
    value = ctypes.wintypes.DWORD()
    chars = ctypes.windll.kernel32.GetLocaleInfoEx(
        LOCALE_NAME_USER_DEFAULT, LOCALE_ITIME | LOCALE_RETURN_NUMBER,
        ctypes.cast(ctypes.byref(value), ctypes.c_wchar_p), 2,
    )
    if chars == 0:
        return '24h'

    return '24h' if value.value == 1 else '12h'


def double_click_seconds() -> float:
    """Return the system double-click interval in seconds."""
    return ctypes.windll.user32.GetDoubleClickTime() / 1000.0


def get_idle_seconds() -> float:
    """Return seconds since the last keyboard or mouse input, 0.0 on failure."""
    lii = _LASTINPUTINFO()
    lii.cbSize = ctypes.sizeof(_LASTINPUTINFO)
    if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(lii)):
        return 0.0

    # Simulate unsigned 32-bit subtraction so the result stays correct
    # when GetTickCount wraps after ~49 days of uptime.
    millis = (ctypes.windll.kernel32.GetTickCount() - lii.dwTime) & 0xFFFFFFFF

    return millis / 1000.0


def is_workstation_locked() -> bool:
    """Return True if the workstation is locked.

    ``OpenInputDesktop`` returns NULL while the secure desktop (lock screen)
    is active.
    """
    hdesk = ctypes.windll.user32.OpenInputDesktop(0, False, 0)
    if hdesk:
        ctypes.windll.user32.CloseDesktop(hdesk)
        return False

    return True


def is_screensaver_running() -> bool:
    """Return True while a screensaver is drawing over the desktop.

    A screensaver without password protection leaves the input desktop open,
    so ``is_workstation_locked`` does not report it.  Returns False when the
    query fails - an unknown state must not look like a covered screen.
    """
    SPI_GETSCREENSAVERRUNNING = 0x0072
    running = ctypes.wintypes.BOOL()
    if not ctypes.windll.user32.SystemParametersInfoW(SPI_GETSCREENSAVERRUNNING, 0, ctypes.byref(running), 0):
        return False

    return bool(running.value)


@functools.lru_cache(maxsize=None)
def load_font(size: int, symbol: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """Load font at given size. Use symbol=True for Unicode glyphs not in Arial."""
    windir = os.environ.get('WINDIR', 'C:\\Windows')
    if symbol:
        names = (f'{windir}\\Fonts\\seguisym.ttf', 'seguisym.ttf')
    else:
        names = (f'{windir}\\Fonts\\arialbd.ttf', 'arialbd.ttf', f'{windir}\\Fonts\\arial.ttf', 'arial.ttf')
    for name in names:
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue

    return ImageFont.load_default()


def taskbar_uses_light_theme() -> bool:
    """Return True if the taskbar uses the light theme.

    Reads ``SystemUsesLightTheme`` from the Personalize registry key.
    Returns False (dark) if the value cannot be read.
    """
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, THEME_REG_KEY) as key:
            value, _ = winreg.QueryValueEx(key, THEME_REG_VALUE)
            return bool(value)
    except OSError:
        return False


def watch_theme_change(callback: Callable[[], None]) -> None:
    """Block the current thread and call *callback* whenever the taskbar theme changes.

    Uses ``RegNotifyChangeKeyValue`` to sleep until the registry key
    is modified, avoiding any polling.  Designed to run in a daemon thread.
    """
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, THEME_REG_KEY, 0, winreg.KEY_READ) as key:
        while True:
            if ctypes.windll.advapi32.RegNotifyChangeKeyValue(int(key), False, REG_NOTIFY_CHANGE_LAST_SET, None, False) != 0:
                return
            try:
                callback()
            except Exception:
                # A transient callback failure (icon re-render, Shell_NotifyIcon
                # during an Explorer restart) must not end theme watching for
                # the rest of the session.
                pass


def _autostart_reg_name() -> str:
    """Return the per-instance registry value name."""
    return AUTOSTART_REG_BASE_NAME + config_dir_suffix()


def _autostart_command() -> str:
    """Return the command line to store in the registry for this instance."""
    command = f'"{sys.executable}"'
    if not is_default_config_dir():
        command += f' --config-dir="{effective_config_dir()}"'

    return command


def is_autostart_enabled() -> bool:
    """Return True if a matching value exists under ``HKCU\\...\\Run``."""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, AUTOSTART_REG_KEY) as key:
            winreg.QueryValueEx(key, _autostart_reg_name())
            return True
    except FileNotFoundError:
        return False


def set_autostart(enable: bool) -> None:
    """Create or remove the autostart registry entry."""
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, AUTOSTART_REG_KEY, 0, winreg.KEY_SET_VALUE) as key:
        if enable:
            winreg.SetValueEx(key, _autostart_reg_name(), 0, winreg.REG_SZ, _autostart_command())
        else:
            try:
                winreg.DeleteValue(key, _autostart_reg_name())
            except FileNotFoundError:
                pass


def sync_autostart_path() -> None:
    """Update the stored autostart command if the executable has been moved."""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, AUTOSTART_REG_KEY) as key:
            stored, _ = winreg.QueryValueEx(key, _autostart_reg_name())
    except FileNotFoundError:
        return

    if stored != _autostart_command():
        set_autostart(True)


def register_notification_identity() -> None:
    """Adopt a fixed notification identity for this process.

    Writes the ``DisplayName`` and ``IconUri`` registration to ``HKCU`` and,
    only if that succeeds, sets the process ``AppUserModelID`` so toasts use
    the registered name and logo.  Re-run on every startup because a frozen
    build extracts the logo to a fresh temporary directory each run, changing
    its path.

    On any failure - a missing logo file or a registry write error - the
    process keeps its default identity (the live tray icon).  This is never
    fatal: a notification icon must not stop the app from starting, and
    falling back to the tray icon is better than an empty one.
    """
    if not _NOTIFICATION_LOGO.is_file():
        return

    try:
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, _IDENTITY_REG_PATH) as key:
            winreg.SetValueEx(key, 'DisplayName', 0, winreg.REG_SZ, DISPLAY_NAME)
            winreg.SetValueEx(key, 'IconUri', 0, winreg.REG_EXPAND_SZ, str(_NOTIFICATION_LOGO))
    except OSError:
        return

    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(ctypes.c_wchar_p(APP_USER_MODEL_ID))


def setup_console() -> None:
    """Point stdout/stderr at the caller's redirection, or at a console.

    A stream the caller redirected to a file or a pipe keeps that
    destination.  Only a stream without one falls back to the console -
    attached from the parent process, or allocated when there is none.

    ``CONOUT$`` addresses the console device itself and therefore bypasses
    any redirection the caller set up.  Opening it for an already redirected
    stream would leave ``--verbose > log.txt`` with an empty file, and a
    packaged build with no way to hand over verbose output as a file.
    """
    ATTACH_PARENT_PROCESS = -1

    stdout_handle = _redirected_handle(_STD_OUTPUT_HANDLE)
    stderr_handle = _redirected_handle(_STD_ERROR_HANDLE)

    stdout_stream = _stream_from_handle(stdout_handle)
    # A parent may hand the same handle to both streams.  Wrapping it twice
    # would produce two file objects that each close it, and the second close
    # fails at interpreter shutdown.
    stderr_stream = stdout_stream if stderr_handle == stdout_handle else _stream_from_handle(stderr_handle)

    # The console is only needed for the streams that were not redirected.
    if stdout_stream is None or stderr_stream is None:
        if not ctypes.windll.kernel32.AttachConsole(ATTACH_PARENT_PROCESS):
            ctypes.windll.kernel32.AllocConsole()

    sys.stdout = stdout_stream if stdout_stream is not None else open('CONOUT$', 'w', encoding='utf-8')  # noqa: SIM115
    sys.stderr = stderr_stream if stderr_stream is not None else open('CONOUT$', 'w', encoding='utf-8')  # noqa: SIM115

    os.environ['PYWEBVIEW_LOG'] = 'DEBUG'


def _redirected_handle(std_handle: int) -> int | None:
    """Return the standard handle behind *std_handle* if the caller redirected it.

    A console-backed handle reports ``FILE_TYPE_CHAR``, and a process
    started without a console has no usable handle at all; both yield
    ``None``, leaving the caller on the console path.

    Parameters
    ----------
    std_handle : int
        One of the ``STD_*_HANDLE`` identifiers.

    Returns
    -------
    int or None
        The handle when it refers to a file or a pipe, otherwise ``None``.
    """
    handle = ctypes.windll.kernel32.GetStdHandle(std_handle)
    if not handle or handle == _INVALID_HANDLE:
        return None

    if ctypes.windll.kernel32.GetFileType(handle) not in (_FILE_TYPE_DISK, _FILE_TYPE_PIPE):
        return None

    return handle


def _stream_from_handle(handle: int | None) -> TextIO | None:
    """Wrap a redirected standard handle in a line-buffered text stream.

    Line buffering keeps the output on disk as it is produced, so a crash
    leaves the diagnostics that led up to it in the file.
    """
    if handle is None:
        return None

    try:
        descriptor = msvcrt.open_osfhandle(handle, os.O_WRONLY)
        return open(descriptor, 'w', encoding='utf-8', buffering=1)  # noqa: SIM115
    except OSError:
        return None


def _webview2_version() -> str:
    """Read WebView2 runtime version from the registry."""
    for guid, channel in _WEBVIEW2_GUIDS:
        for root_key in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            for sub_path in (
                rf'SOFTWARE\Microsoft\EdgeUpdate\Clients\{guid}',
                rf'SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{guid}',
            ):
                try:
                    with winreg.OpenKey(root_key, sub_path) as key:
                        build, _ = winreg.QueryValueEx(key, 'pv')
                        if build and build != '0.0.0.0':
                            suffix = f' ({channel})' if channel != 'Runtime' else ''
                            return f'{build}{suffix}'
                except OSError:
                    pass

    return 'not found'


def _dotnet_version() -> str:
    """Read .NET Framework version from the registry."""
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r'SOFTWARE\Microsoft\NET Framework Setup\NDP\v4\Full') as key:
            release, _ = winreg.QueryValueEx(key, 'Release')
            # https://learn.microsoft.com/en-us/dotnet/framework/migration-guide/how-to-determine-which-versions-are-installed
            version_map = [
                (533320, '4.8.1'), (528040, '4.8'), (461808, '4.7.2'), (461308, '4.7.1'),
                (460798, '4.7'), (394802, '4.6.2'), (394254, '4.6.1'), (393295, '4.6'),
            ]
            for min_release, version in version_map:
                if release >= min_release:
                    return f'{version} (release {release})'
            return f'< 4.6 (release {release})'
    except OSError:
        return 'not found'


def _dpi_info() -> tuple[str, str]:
    """Get DPI awareness mode and system DPI."""
    user32 = ctypes.windll.user32

    # DPI awareness context
    try:
        ctx = user32.GetThreadDpiAwarenessContext()
        awareness = user32.GetAwarenessFromDpiAwarenessContext(ctx)
        awareness_names = {0: 'Unaware', 1: 'System', 2: 'Per-Monitor V2'}
        awareness_str = awareness_names.get(awareness, f'Unknown ({awareness})')
    except Exception:
        awareness_str = 'unavailable'

    # System DPI
    try:
        dpi = user32.GetDpiForSystem()
        scale = round(dpi / 96 * 100)
        dpi_str = f'{dpi} ({scale}%)'
    except Exception:
        dpi_str = 'unavailable'

    return awareness_str, dpi_str


def _screen_info() -> tuple[str, str, str]:
    """Get monitor count, primary resolution, and work area."""
    user32 = ctypes.windll.user32

    try:
        monitor_count = str(user32.GetSystemMetrics(80))  # SM_CMONITORS
    except Exception:
        monitor_count = 'unavailable'

    try:
        screen_w = user32.GetSystemMetrics(0)  # SM_CXSCREEN
        screen_h = user32.GetSystemMetrics(1)  # SM_CYSCREEN
        primary = f'{screen_w} x {screen_h}'
    except Exception:
        primary = 'unavailable'

    try:
        rect = ctypes.wintypes.RECT()
        ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(rect), 0)  # SPI_GETWORKAREA
        work_area = f'{rect.right - rect.left} x {rect.bottom - rect.top} (left={rect.left}, top={rect.top})'
    except Exception:
        work_area = 'unavailable'

    return monitor_count, primary, work_area


def diagnostic_system_rows() -> list[tuple[str, str]]:
    """Return the platform's rows for the System section."""
    winver = sys.getwindowsversion()

    return [
        ('OS', f'{platform.platform()} (build {winver.build})'),
        ('Architecture', platform.machine()),
        ('Admin', 'Yes' if ctypes.windll.shell32.IsUserAnAdmin() else 'No'),
    ]


def diagnostic_display_rows() -> list[tuple[str, str]]:
    """Return the platform's rows for the Display section."""
    awareness, dpi = _dpi_info()
    monitor_count, primary, work_area = _screen_info()

    return [
        ('DPI awareness', awareness),
        ('System DPI', dpi),
        ('Monitors', monitor_count),
        ('Primary resolution', primary),
        ('Work area', work_area),
    ]


def diagnostic_runtime_rows() -> list[tuple[str, str]]:
    """Return the platform's rows for the Runtimes section."""
    return [
        ('WebView2', _webview2_version()),
        ('.NET Framework', _dotnet_version()),
    ]


def diagnostic_post_init_rows() -> list[tuple[str, str]]:
    """Return rows that are only available once the GUI toolkit has loaded."""
    rows = []

    try:
        import pythonnet  # type: ignore[import-untyped]  # no type stubs available

        runtime_info = pythonnet.get_runtime_info()
        if runtime_info:
            rows.append(('.NET runtime', f'{runtime_info.kind} {runtime_info.version}'))
            rows.append(('.NET initialized', str(runtime_info.initialized)))
        else:
            rows.append(('.NET runtime', 'info not available'))
    except Exception as exc:
        rows.append(('.NET runtime', f'error: {exc}'))

    try:
        from System import Environment  # type: ignore[import-untyped]  # .NET import via pythonnet

        rows.append(('.NET CLR version', str(Environment.Version)))
    except Exception:
        pass

    return rows


# Tray mouse messages, delivered by the shell as the WM_NOTIFY lParam.
WM_LBUTTONUP = 0x0202
WM_LBUTTONDBLCLK = 0x0203


def install_tray_click_handler(
    icon: Any, on_single_click: Callable[[], None], on_double_click: Callable[[], None],
) -> bool:
    """Add double-click handling to a pystray tray icon.

    pystray has no double-click support: it fires the default menu item on
    every ``WM_LBUTTONUP``.  A double-click command therefore requires
    deferring that single click by the system double-click interval and
    cancelling it when a second click arrives.

    This reaches into pystray internals - it locates the ``WM_NOTIFY`` entry
    in the private handler table by identity and swaps in its own dispatcher,
    keeping the original handler for right-click and every other message.  A
    pystray release that renames ``_message_handlers`` or ``_on_notify`` is
    what would break it, which is why the swap reports whether it happened.

    Returns
    -------
    bool
        True if the handler was installed.
    """
    lock = threading.Lock()
    interval = double_click_seconds()
    pystray_on_notify = icon._on_notify
    pending: dict[str, Any] = {'timer': None, 'swallow_next_up': False}

    def fire_single_click() -> None:
        """Open the popup once the double-click interval passes without a second click.

        Bails out if the timer was cleared meanwhile - a double-click that
        arrived right as the timer fired cancels it here, so the popup never
        opens for a completed double-click.
        """
        with lock:
            if pending['timer'] is None:
                return
            pending['timer'] = None

        on_single_click()

    def on_tray_message(wparam: int, lparam: int) -> int:
        """Dispatch a tray mouse message, adding double-click handling.

        A left-button release schedules the single-click action after the
        double-click interval; a double-click cancels that pending action and
        runs the command instead.  The trailing release that follows every
        double-click is swallowed so it does not schedule a second action.
        """
        if lparam == WM_LBUTTONUP:
            with lock:
                if pending['swallow_next_up']:
                    pending['swallow_next_up'] = False
                    return 0
                if pending['timer'] is not None:
                    pending['timer'].cancel()
                timer = threading.Timer(interval, fire_single_click)
                timer.daemon = True
                pending['timer'] = timer
                timer.start()
            return 0

        if lparam == WM_LBUTTONDBLCLK:
            with lock:
                pending['swallow_next_up'] = True
                if pending['timer'] is not None:
                    pending['timer'].cancel()
                    pending['timer'] = None
            on_double_click()
            return 0

        return pystray_on_notify(wparam, lparam)

    for code, handler in icon._message_handlers.items():
        if handler == pystray_on_notify:
            icon._message_handlers[code] = on_tray_message
            return True

    return False


def set_dpi_awareness() -> None:
    """Opt the process into Per-Monitor-V2 DPI awareness.

    Must run before pywebview's legacy ``SetProcessDPIAware()`` call, which
    only sets SYSTEM_DPI_AWARE and breaks native menu hover at high DPI.  The
    API exists only from Windows 10 1703; a missing export raises
    ``AttributeError`` in ctypes, and that must not kill startup - pywebview's
    legacy call is the fallback on older systems.
    """
    try:
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_ssize_t(-4))
    except AttributeError:
        pass


def autostart_supported() -> bool:
    """Whether an autostart entry can be written that actually starts the app.

    Only for a packaged build: from source ``sys.executable`` is the
    interpreter, and a registry Run value holding just that path would start
    Python without the application.
    """
    return getattr(sys, 'frozen', False)


def prepare_gui_environment() -> None:
    """No-op.

    The Linux counterpart selects a GTK backend here; Windows has one host.
    """

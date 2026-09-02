"""
Linux Backend Tests
====================

Unit tests for the Linux platform backend.  The backend defers every ``gi``
import, so these tests run in a plain virtual environment without PyGObject.
"""
from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch

if sys.platform == 'win32':
    raise unittest.SkipTest('Linux backend is not used on Windows')

import usage_monitor_for_claude.platforms.linux as linux  # noqa: E402


class TestNoWindowKwargs(unittest.TestCase):

    def test_is_empty(self):
        """POSIX has no console-window concept, so no subprocess flags are added."""
        self.assertEqual(linux.no_window_kwargs(), {})


class TestSystemTimeFormat(unittest.TestCase):
    """Tests for locale-based 12h/24h detection."""

    def _detect(self, pattern: object) -> str:
        with patch.object(linux.locale, 'nl_langinfo', return_value=pattern):
            return linux.system_time_format()

    def test_24_hour_pattern(self):
        """A pattern without an AM/PM marker is a 24-hour clock."""
        self.assertEqual(self._detect('%H:%M:%S'), '24h')

    def test_12_hour_pattern_via_meridiem(self):
        """A pattern containing %p is a 12-hour clock."""
        self.assertEqual(self._detect('%I:%M:%S %p'), '12h')

    def test_12_hour_pattern_via_hour_code(self):
        """%I alone already implies a 12-hour clock."""
        self.assertEqual(self._detect('%I:%M'), '12h')

    def test_empty_pattern_falls_back_to_24h(self):
        """A locale without a time format falls back to 24-hour."""
        self.assertEqual(self._detect(''), '24h')

    def test_unsupported_locale_falls_back_to_24h(self):
        """A platform without nl_langinfo falls back to 24-hour."""
        with patch.object(linux.locale, 'nl_langinfo', side_effect=ValueError):
            self.assertEqual(linux.system_time_format(), '24h')


class TestIdleAndLock(unittest.TestCase):
    """Tests for the DBus-backed idle and lock queries."""

    def test_idle_converts_milliseconds(self):
        """Mutter reports milliseconds; the API reports seconds."""
        with patch.object(linux, '_call_session_method', return_value=7500):
            self.assertAlmostEqual(linux.get_idle_seconds(), 7.5)

    def test_idle_without_service_reports_zero(self):
        """An unreachable idle monitor reports 0.0 so idle-pause stays inactive."""
        with patch.object(linux, '_call_session_method', return_value=None):
            self.assertEqual(linux.get_idle_seconds(), 0.0)

    def test_locked_true(self):
        """An active screensaver reports a locked session."""
        with patch.object(linux, '_call_session_method', return_value=True):
            self.assertTrue(linux.is_workstation_locked())

    def test_locked_false(self):
        """An inactive screensaver reports an unlocked session."""
        with patch.object(linux, '_call_session_method', return_value=False):
            self.assertFalse(linux.is_workstation_locked())

    def test_unknown_lock_state_reports_unlocked(self):
        """An unreachable screensaver must not suppress notifications forever."""
        with patch.object(linux, '_call_session_method', return_value=None):
            self.assertFalse(linux.is_workstation_locked())


class TestMessageBoxes(unittest.TestCase):
    """Tests for the dialog fallbacks."""

    def test_error_box_falls_back_to_stderr(self):
        """Without GTK the message still reaches the user via stderr."""
        with patch.object(linux, '_try_gtk_dialog', return_value=False), \
             patch('builtins.print') as mock_print:
            linux.show_error_box('boom', 'Title')
        mock_print.assert_called_once()
        self.assertIn('boom', mock_print.call_args[0][0])

    def test_warning_box_falls_back_to_stderr(self):
        """The warning variant uses the same fallback."""
        with patch.object(linux, '_try_gtk_dialog', return_value=False), \
             patch('builtins.print') as mock_print:
            linux.show_warning_box('careful', 'Title')
        self.assertIn('careful', mock_print.call_args[0][0])

    def test_error_box_prefers_dialog(self):
        """A working GTK dialog suppresses the stderr fallback."""
        with patch.object(linux, '_try_gtk_dialog', return_value=True) as mock_dialog, \
             patch('builtins.print') as mock_print:
            linux.show_error_box('boom', 'Title')
        mock_dialog.assert_called_once()
        mock_print.assert_not_called()

    def test_error_box_truncates_long_messages(self):
        """Very long messages are capped before reaching the dialog."""
        with patch.object(linux, '_try_gtk_dialog', return_value=True) as mock_dialog:
            linux.show_error_box('x' * 5000, 'Title')
        self.assertEqual(len(mock_dialog.call_args[0][0]), 2000)

    def test_error_box_uses_error_type(self):
        """The error variant asks for an error dialog, the warning variant does not."""
        with patch.object(linux, '_try_gtk_dialog', return_value=True) as mock_dialog:
            linux.show_error_box('a', 'T')
            linux.show_warning_box('b', 'T')
        self.assertIs(mock_dialog.call_args_list[0][0][2], True)
        self.assertIs(mock_dialog.call_args_list[1][0][2], False)


class TestDoubleClickSeconds(unittest.TestCase):
    """Tests for the double-click interval lookup."""

    def test_falls_back_without_gtk(self):
        """No GTK settings means the documented default."""
        with patch.object(linux, '_gtk_settings', return_value=None):
            self.assertEqual(linux.double_click_seconds(), linux._DEFAULT_DOUBLE_CLICK_SECONDS)

    def test_reads_gtk_setting(self):
        """The GTK setting is milliseconds and is converted to seconds."""
        settings = MagicMock()
        settings.get_property.return_value = 250
        with patch.object(linux, '_gtk_settings', return_value=settings):
            self.assertAlmostEqual(linux.double_click_seconds(), 0.25)

    def test_zero_setting_falls_back(self):
        """A zero interval is meaningless and falls back to the default."""
        settings = MagicMock()
        settings.get_property.return_value = 0
        with patch.object(linux, '_gtk_settings', return_value=settings):
            self.assertEqual(linux.double_click_seconds(), linux._DEFAULT_DOUBLE_CLICK_SECONDS)

    def test_failing_setting_falls_back(self):
        """A GTK error must not propagate to the caller."""
        settings = MagicMock()
        settings.get_property.side_effect = RuntimeError
        with patch.object(linux, '_gtk_settings', return_value=settings):
            self.assertEqual(linux.double_click_seconds(), linux._DEFAULT_DOUBLE_CLICK_SECONDS)


class TestGiUnavailable(unittest.TestCase):
    """The backend must stay usable without PyGObject installed."""

    def setUp(self):
        self._saved = linux._gi_available
        linux._gi_available = None

    def tearDown(self):
        linux._gi_available = self._saved

    def test_import_failure_is_cached(self):
        """A missing gi is remembered so every call does not retry the import."""
        with patch.dict(sys.modules, {'gi': None}), \
             patch('builtins.__import__', side_effect=ImportError):
            self.assertIsNone(linux._import_gi())
        self.assertIs(linux._gi_available, False)
        self.assertIsNone(linux._import_gi())


class TestLoadFont(unittest.TestCase):
    """Tests for the Linux font lookup."""

    def setUp(self):
        linux.load_font.cache_clear()

    def tearDown(self):
        linux.load_font.cache_clear()

    def test_loads_first_available_text_font(self):
        """The first candidate that opens wins."""
        font = MagicMock()
        with patch.object(linux.ImageFont, 'truetype', return_value=font) as mock_truetype:
            self.assertIs(linux.load_font(42), font)
        mock_truetype.assert_called_once_with('/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf', 42)

    def test_symbol_uses_symbol_family(self):
        """symbol=True asks for a face that carries the glyphs Arial lacks."""
        font = MagicMock()
        with patch.object(linux.ImageFont, 'truetype', return_value=font) as mock_truetype:
            self.assertIs(linux.load_font(36, symbol=True), font)
        self.assertIn('DejaVuSans.ttf', mock_truetype.call_args[0][0])

    def test_tries_next_candidate_on_failure(self):
        """A missing font falls through to the next candidate."""
        font = MagicMock()
        with patch.object(linux.ImageFont, 'truetype', side_effect=[OSError, font]) as mock_truetype:
            self.assertIs(linux.load_font(42), font)
        self.assertEqual(mock_truetype.call_count, 2)

    def test_falls_back_to_default(self):
        """A system without any candidate still renders with the bitmap default."""
        default = MagicMock()
        with patch.object(linux.ImageFont, 'truetype', side_effect=OSError), \
             patch.object(linux.ImageFont, 'load_default', return_value=default):
            self.assertIs(linux.load_font(42), default)

    def test_results_are_cached(self):
        """The same size is looked up once."""
        with patch.object(linux.ImageFont, 'truetype', return_value=MagicMock()) as mock_truetype:
            first = linux.load_font(42)
            second = linux.load_font(42)
        self.assertIs(first, second)
        mock_truetype.assert_called_once()


class TestTaskbarUsesLightTheme(unittest.TestCase):
    """Tests for portal-based panel theme detection."""

    def test_light_preference(self):
        """color-scheme 2 means the panel is light."""
        with patch.object(linux, '_read_color_scheme', return_value=2):
            self.assertTrue(linux.taskbar_uses_light_theme())

    def test_dark_preference(self):
        """color-scheme 1 means dark."""
        with patch.object(linux, '_read_color_scheme', return_value=1):
            self.assertFalse(linux.taskbar_uses_light_theme())

    def test_no_preference_is_dark(self):
        """color-scheme 0 means no preference; panels default to dark."""
        with patch.object(linux, '_read_color_scheme', return_value=0):
            self.assertFalse(linux.taskbar_uses_light_theme())

    def test_unreachable_portal_is_dark(self):
        """An unreachable portal must not raise."""
        with patch.object(linux, '_read_color_scheme', return_value=None):
            self.assertFalse(linux.taskbar_uses_light_theme())


class TestWatchThemeChange(unittest.TestCase):
    """Tests for the polling theme watcher."""

    def test_returns_immediately_without_portal(self):
        """No portal means no watching - never spin on a service that cannot answer."""
        callback = MagicMock()
        with patch.object(linux, '_read_color_scheme', return_value=None), \
             patch.object(linux.time, 'sleep') as mock_sleep:
            linux.watch_theme_change(callback)
        callback.assert_not_called()
        mock_sleep.assert_not_called()

    def test_calls_back_on_change_only(self):
        """The callback fires per change, not per poll."""
        callback = MagicMock()
        # initial, unchanged, changed, then a read that ends the loop
        schemes = [1, 1, 2]

        def read():
            if schemes:
                return schemes.pop(0)
            raise StopIteration

        with patch.object(linux, '_read_color_scheme', side_effect=read), \
             patch.object(linux.time, 'sleep'):
            with self.assertRaises(StopIteration):
                linux.watch_theme_change(callback)
        callback.assert_called_once()

    def test_callback_exception_does_not_end_watcher(self):
        """A transient callback failure must not end theme watching."""
        calls = []

        def callback():
            calls.append(1)
            if len(calls) == 1:
                raise RuntimeError('transient render failure')

        schemes = [1, 2, 1]

        def read():
            if schemes:
                return schemes.pop(0)
            raise StopIteration

        with patch.object(linux, '_read_color_scheme', side_effect=read), \
             patch.object(linux.time, 'sleep'):
            with self.assertRaises(StopIteration):
                linux.watch_theme_change(callback)
        self.assertEqual(len(calls), 2)

    def test_unreadable_poll_is_skipped(self):
        """A failed read mid-watch is skipped rather than treated as a change."""
        callback = MagicMock()
        schemes = [1, None, 1]

        def read():
            if schemes:
                return schemes.pop(0)
            raise StopIteration

        with patch.object(linux, '_read_color_scheme', side_effect=read), \
             patch.object(linux.time, 'sleep'):
            with self.assertRaises(StopIteration):
                linux.watch_theme_change(callback)
        callback.assert_not_called()


class TestAutostart(unittest.TestCase):
    """Tests for the XDG autostart entry."""

    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.directory = Path(self._tmp.name) / 'autostart'
        patcher = patch.object(linux, 'AUTOSTART_DIRECTORY', self.directory)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self._tmp.cleanup)

        suffix = patch.object(linux, 'config_dir_suffix', return_value='')
        suffix.start()
        self.addCleanup(suffix.stop)

        default = patch.object(linux, 'is_default_config_dir', return_value=True)
        default.start()
        self.addCleanup(default.stop)

    def test_disabled_by_default(self):
        """No entry means autostart is off."""
        self.assertFalse(linux.is_autostart_enabled())

    def test_enable_creates_entry(self):
        """Enabling writes a .desktop file into the autostart directory."""
        linux.set_autostart(True)
        self.assertTrue(linux.is_autostart_enabled())
        content = (self.directory / 'usage-monitor-for-claude.desktop').read_text(encoding='utf-8')
        self.assertIn('[Desktop Entry]', content)
        self.assertIn('Type=Application', content)
        self.assertIn('X-GNOME-Autostart-enabled=true', content)

    def test_disable_removes_entry(self):
        """Disabling removes the file again."""
        linux.set_autostart(True)
        linux.set_autostart(False)
        self.assertFalse(linux.is_autostart_enabled())

    def test_disable_without_entry_is_safe(self):
        """Disabling an absent entry must not raise."""
        linux.set_autostart(False)
        self.assertFalse(linux.is_autostart_enabled())

    def test_config_dir_gets_own_entry(self):
        """A second monitored account writes a separate entry."""
        with patch.object(linux, 'config_dir_suffix', return_value='-abc123'):
            linux.set_autostart(True)
            self.assertTrue((self.directory / 'usage-monitor-for-claude-abc123.desktop').is_file())
        self.assertFalse(linux.is_autostart_enabled())

    def test_non_default_config_dir_in_exec(self):
        """A custom config directory is passed through on the Exec line."""
        with patch.object(linux, 'is_default_config_dir', return_value=False), \
             patch.object(linux, 'effective_config_dir', return_value=Path('/home/u/.claude-second')):
            linux.set_autostart(True)
        content = (self.directory / 'usage-monitor-for-claude.desktop').read_text(encoding='utf-8')
        self.assertIn('--config-dir=/home/u/.claude-second', content)

    def test_exec_quotes_paths_with_spaces(self):
        """A path containing spaces stays one argument."""
        with patch.object(linux.sys, 'executable', '/opt/my apps/monitor'):
            command = linux._autostart_command()
        self.assertIn("'/opt/my apps/monitor'", command)

    def test_frozen_build_runs_executable_directly(self):
        """A frozen build is its own entry point."""
        with patch.object(linux.sys, 'frozen', True, create=True), \
             patch.object(linux.sys, 'executable', '/opt/monitor'):
            self.assertEqual(linux._autostart_command(), '/opt/monitor')

    def test_source_run_includes_module(self):
        """Running from source needs the interpreter plus the module."""
        with patch.object(linux.sys, 'executable', '/usr/bin/python3'):
            self.assertEqual(linux._autostart_command(), '/usr/bin/python3 -m usage_monitor_for_claude')

    def test_sync_rewrites_moved_executable(self):
        """A moved executable updates the stored Exec line."""
        linux.set_autostart(True)
        path = self.directory / 'usage-monitor-for-claude.desktop'
        path.write_text(_autostart_entry_with_exec('/old/path'), encoding='utf-8')
        linux.sync_autostart_path()
        self.assertIn(linux._autostart_command(), path.read_text(encoding='utf-8'))

    def test_sync_without_entry_does_nothing(self):
        """Syncing an absent entry must not create one."""
        linux.sync_autostart_path()
        self.assertFalse(linux.is_autostart_enabled())

    def test_sync_leaves_matching_entry_untouched(self):
        """An up-to-date entry is not rewritten."""
        linux.set_autostart(True)
        path = self.directory / 'usage-monitor-for-claude.desktop'
        before = path.stat().st_mtime_ns
        linux.sync_autostart_path()
        self.assertEqual(path.stat().st_mtime_ns, before)


def _autostart_entry_with_exec(command: str) -> str:
    """Build a .desktop body with a specific Exec line, for sync tests."""
    return linux._autostart_entry().replace(f'Exec={linux._autostart_command()}', f'Exec={command}')


class TestAutostartSupport(unittest.TestCase):
    """Tests for whether the menu may offer autostart."""

    def test_supported_from_source(self):
        """The .desktop entry carries interpreter and working directory, so source runs work."""
        with patch.object(linux.sys, 'frozen', False, create=True):
            self.assertTrue(linux.autostart_supported())

    def test_supported_when_frozen(self):
        with patch.object(linux.sys, 'frozen', True, create=True):
            self.assertTrue(linux.autostart_supported())

    def test_source_entry_sets_the_working_directory(self):
        """Without Path, the session would start the app from the home directory."""
        with patch.object(linux.sys, 'frozen', False, create=True):
            entry = linux._autostart_entry()
        self.assertIn(f'Path={linux._project_root()}', entry)

    def test_frozen_entry_omits_the_working_directory(self):
        """A packaged build is its own entry point and needs no project root."""
        with patch.object(linux.sys, 'frozen', True, create=True), \
             patch.object(linux.sys, 'executable', '/opt/monitor'):
            entry = linux._autostart_entry()
        self.assertNotIn('Path=', entry)


class TestPrepareGuiEnvironment(unittest.TestCase):
    """Tests for choosing the GUI toolkit backend."""

    def test_defaults_to_x11(self):
        """Wayland cannot position the popup, so XWayland is the default."""
        with patch.dict(linux.os.environ, {}, clear=True):
            linux.prepare_gui_environment()
            self.assertEqual(linux.os.environ['GDK_BACKEND'], 'x11')

    def test_respects_an_explicit_choice(self):
        """Someone trying the native backend on purpose must not be overridden."""
        with patch.dict(linux.os.environ, {'GDK_BACKEND': 'wayland'}):
            linux.prepare_gui_environment()
            self.assertEqual(linux.os.environ['GDK_BACKEND'], 'wayland')


class TestRegisterNotificationIdentity(unittest.TestCase):

    def test_is_a_no_op(self):
        """Freedesktop notifications carry their icon per message."""
        self.assertIsNone(linux.register_notification_identity())


class TestSetupConsole(unittest.TestCase):
    """Tests for verbose console setup."""

    def test_sets_pywebview_log(self):
        """pywebview's own logging is raised alongside our diagnostics."""
        with patch.dict(linux.os.environ, {}, clear=True):
            linux.setup_console()
            self.assertEqual(linux.os.environ['PYWEBVIEW_LOG'], 'DEBUG')

    def test_leaves_streams_untouched(self):
        """A POSIX process already has usable streams - nothing is replaced."""
        stdout, stderr = sys.stdout, sys.stderr
        with patch.dict(linux.os.environ, {}, clear=True):
            linux.setup_console()
        self.assertIs(sys.stdout, stdout)
        self.assertIs(sys.stderr, stderr)


class TestDiagnosticRows(unittest.TestCase):
    """Tests for the diagnostics probes."""

    def test_system_rows_cover_the_session(self):
        """The System section names the desktop and session type."""
        with patch.dict(linux.os.environ, {'XDG_CURRENT_DESKTOP': 'ubuntu:GNOME', 'XDG_SESSION_TYPE': 'wayland'}):
            labels = dict(linux.diagnostic_system_rows())
        self.assertEqual(labels['Desktop'], 'ubuntu:GNOME')
        self.assertEqual(labels['Session type'], 'wayland')

    def test_system_rows_without_session_variables(self):
        """A bare environment still produces a complete section."""
        with patch.dict(linux.os.environ, {}, clear=True):
            labels = dict(linux.diagnostic_system_rows())
        self.assertEqual(labels['Desktop'], 'unknown')
        self.assertEqual(labels['Session type'], 'unknown')

    def test_display_rows_without_display(self):
        """No display reports a reason instead of raising."""
        with patch.object(linux, '_gdk_display', return_value=None):
            labels = dict(linux.diagnostic_display_rows())
        self.assertIn('unavailable', labels['Monitors'])

    def test_display_rows_report_geometry(self):
        """A reachable display reports monitor count, resolution and work area."""
        monitor = MagicMock()
        monitor.get_geometry.return_value = MagicMock(width=3440, height=1440)
        monitor.get_workarea.return_value = MagicMock(width=3440, height=1357, x=2560, y=32)
        monitor.get_scale_factor.return_value = 2
        display = MagicMock()
        display.get_n_monitors.return_value = 3
        display.get_primary_monitor.return_value = monitor

        with patch.object(linux, '_gdk_display', return_value=display):
            labels = dict(linux.diagnostic_display_rows())

        self.assertEqual(labels['Monitors'], '3')
        self.assertEqual(labels['Primary resolution'], '3440 x 1440')
        self.assertEqual(labels['Scale factor'], '2')

    def test_runtime_rows_without_gi(self):
        """Without PyGObject every toolkit row says so rather than raising."""
        with patch.object(linux, '_import_gi', return_value=None):
            values = [value for _, value in linux.diagnostic_runtime_rows()]
        self.assertTrue(all('not available' in value for value in values))

    def test_post_init_rows_without_gi(self):
        """The post-init section degrades the same way."""
        with patch.object(linux, '_import_gi', return_value=None):
            self.assertEqual(linux.diagnostic_post_init_rows(), [('PyGObject', 'not available')])

    def test_os_release_name_missing_file(self):
        """A system without /etc/os-release still reports something printable."""
        with patch.object(linux.Path, 'read_text', side_effect=OSError):
            self.assertEqual(linux._os_release_name(), 'unknown distribution')


class TestInstallTrayClickHandler(unittest.TestCase):
    """Tests for the tray click handler on a panel-driven tray."""

    def test_reports_unavailable(self):
        """A StatusNotifierItem is driven by the panel, so no click reaches us."""
        icon = MagicMock()
        self.assertFalse(linux.install_tray_click_handler(icon, MagicMock(), MagicMock()))

    def test_leaves_the_icon_untouched(self):
        """Nothing may be grafted onto pystray when the mechanism does not apply."""
        icon = MagicMock()
        handlers = {0x40B: MagicMock()}
        icon._message_handlers = handlers
        original = dict(handlers)

        linux.install_tray_click_handler(icon, MagicMock(), MagicMock())

        self.assertEqual(icon._message_handlers, original)


class TestSetDpiAwareness(unittest.TestCase):

    def test_is_a_no_op(self):
        """GTK reads the scale factor from the display; there is nothing to opt into."""
        self.assertIsNone(linux.set_dpi_awareness())


class TestImportsWithoutPyGObject(unittest.TestCase):
    """The backend must stay importable and usable without PyGObject installed.

    A virtual environment may or may not expose the distribution's ``gi`` - it
    does with ``--system-site-packages``, which is what running the app from
    source needs.  So the absence is staged explicitly in a subprocess rather
    than left to whatever the environment happens to provide.
    """

    def _run_without_gi(self, body: str) -> subprocess.CompletedProcess:
        script = 'import sys\nsys.modules["gi"] = None  # makes "import gi" raise ImportError\n' + body

        return subprocess.run(
            [sys.executable, '-c', script],
            capture_output=True, text=True, timeout=60,
            cwd=str(Path(__file__).resolve().parent.parent),
        )

    def test_backend_imports_and_degrades(self):
        """Every entry point returns its documented fallback instead of raising."""
        result = self._run_without_gi(
            'from usage_monitor_for_claude.platforms import linux\n'
            'assert linux.no_window_kwargs() == {}\n'
            "assert linux.system_time_format() in ('24h', '12h')\n"
            'assert linux.get_idle_seconds() == 0.0\n'
            'assert linux.is_workstation_locked() is False\n'
            'assert linux.taskbar_uses_light_theme() is False\n'
            'assert linux.double_click_seconds() == linux._DEFAULT_DOUBLE_CLICK_SECONDS\n'
            'assert linux.install_tray_click_handler(None, None, None) is False\n'
            'assert linux.set_dpi_awareness() is None\n'
            'assert linux.register_notification_identity() is None\n'
            "assert all('not available' in value for _, value in linux.diagnostic_runtime_rows())\n"
            "assert linux.diagnostic_post_init_rows() == [('PyGObject', 'not available')]\n",
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_popup_host_imports(self):
        """The popup host must not reach for gi at import time either."""
        result = self._run_without_gi(
            'from usage_monitor_for_claude.platforms import popup_linux\n'
            "assert popup_linux.WINDOW_KWARGS['resizable'] is True\n"
            'host = popup_linux.PopupHost(object(), 340)\n'
            'assert host._anchor() is None\n'
            'assert host.begin_drag() is False\n',
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_instance_guard_imports(self):
        """The single-instance guard uses fcntl, never gi."""
        result = self._run_without_gi(
            'from usage_monitor_for_claude.platforms import instance_linux\n'
            "assert instance_linux._lock_path().name.endswith('.lock')\n",
        )
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__':
    unittest.main()

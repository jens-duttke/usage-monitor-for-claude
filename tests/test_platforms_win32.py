"""
Windows Backend Tests
======================

Unit tests for the Win32 platform backend.  The module imports ``ctypes.windll``
at import time, so the whole file is skipped off Windows.
"""
from __future__ import annotations

import sys
import time
import unittest
from unittest.mock import MagicMock, call, patch

if sys.platform != 'win32':
    raise unittest.SkipTest('Win32 backend is only importable on Windows')

import usage_monitor_for_claude.platforms.win32 as win32  # noqa: E402


class TestNoWindowKwargs(unittest.TestCase):

    def test_sets_create_no_window(self):
        """The console window is suppressed via creationflags."""
        import subprocess

        self.assertEqual(win32.no_window_kwargs(), {'creationflags': subprocess.CREATE_NO_WINDOW})


class TestSystemTimeFormat(unittest.TestCase):
    """Tests for locale-based 12h/24h detection."""

    def _detect(self, chars: int, itime_value: int) -> str:
        """Run detection with GetLocaleInfoEx mocked to return *chars* and write *itime_value*."""
        mock_ctypes = MagicMock()
        mock_ctypes.wintypes.DWORD.return_value.value = itime_value
        mock_ctypes.windll.kernel32.GetLocaleInfoEx.return_value = chars
        with patch.object(win32, 'ctypes', mock_ctypes):
            return win32.system_time_format()

    def test_itime_one_is_24h(self):
        """LOCALE_ITIME of 1 maps to a 24-hour clock."""
        self.assertEqual(self._detect(chars=2, itime_value=1), '24h')

    def test_itime_zero_is_12h(self):
        """LOCALE_ITIME of 0 maps to a 12-hour clock."""
        self.assertEqual(self._detect(chars=2, itime_value=0), '12h')

    def test_query_failure_falls_back_to_24h(self):
        """A failed locale query (0 chars written) falls back to 24-hour."""
        self.assertEqual(self._detect(chars=0, itime_value=0), '24h')


class TestGetIdleSeconds(unittest.TestCase):
    """Tests for GetLastInputInfo-based idle detection."""

    @patch.object(win32, 'ctypes')
    def test_returns_idle_duration(self, mock_ctypes: MagicMock):
        """Tick difference is reported in seconds."""
        mock_ctypes.sizeof.return_value = 8
        mock_ctypes.windll.user32.GetLastInputInfo.return_value = 1
        mock_ctypes.windll.kernel32.GetTickCount.return_value = 15_000
        instance = mock_ctypes.Structure.return_value
        with patch.object(win32, '_LASTINPUTINFO') as mock_struct:
            mock_struct.return_value = instance
            instance.dwTime = 5_000
            self.assertAlmostEqual(win32.get_idle_seconds(), 10.0)

    @patch.object(win32, 'ctypes')
    def test_returns_zero_on_failure(self, mock_ctypes: MagicMock):
        """A failed GetLastInputInfo reports no idle time."""
        mock_ctypes.windll.user32.GetLastInputInfo.return_value = 0
        self.assertEqual(win32.get_idle_seconds(), 0.0)

    @patch.object(win32, 'ctypes')
    def test_tick_count_wraparound(self, mock_ctypes: MagicMock):
        """A wrapped GetTickCount still yields a positive idle time."""
        mock_ctypes.windll.user32.GetLastInputInfo.return_value = 1
        mock_ctypes.windll.kernel32.GetTickCount.return_value = 1_000
        instance = MagicMock()
        instance.dwTime = 0xFFFFFFFF - 1_000
        with patch.object(win32, '_LASTINPUTINFO', return_value=instance):
            self.assertAlmostEqual(win32.get_idle_seconds(), 2.001, places=2)


class TestIsWorkstationLocked(unittest.TestCase):
    """Tests for OpenInputDesktop-based lock detection."""

    @patch.object(win32, 'ctypes')
    def test_locked_when_null_handle(self, mock_ctypes: MagicMock):
        """A NULL desktop handle means the secure desktop is active."""
        mock_ctypes.windll.user32.OpenInputDesktop.return_value = None
        self.assertTrue(win32.is_workstation_locked())

    @patch.object(win32, 'ctypes')
    def test_unlocked_when_valid_handle(self, mock_ctypes: MagicMock):
        """A valid handle means the session is unlocked, and it is closed again."""
        mock_ctypes.windll.user32.OpenInputDesktop.return_value = 1234
        self.assertFalse(win32.is_workstation_locked())
        mock_ctypes.windll.user32.CloseDesktop.assert_called_once_with(1234)


class TestIsScreensaverRunning(unittest.TestCase):
    """Tests for SPI_GETSCREENSAVERRUNNING-based screensaver detection."""

    @patch.object(win32, 'ctypes')
    def test_running_when_flag_set(self, mock_ctypes: MagicMock):
        """A set flag means a screensaver is drawing over the desktop."""
        mock_ctypes.windll.user32.SystemParametersInfoW.return_value = 1
        mock_ctypes.wintypes.BOOL.return_value.value = 1
        self.assertTrue(win32.is_screensaver_running())

    @patch.object(win32, 'ctypes')
    def test_not_running_when_flag_clear(self, mock_ctypes: MagicMock):
        """A clear flag means the desktop is visible."""
        mock_ctypes.windll.user32.SystemParametersInfoW.return_value = 1
        mock_ctypes.wintypes.BOOL.return_value.value = 0
        self.assertFalse(win32.is_screensaver_running())

    @patch.object(win32, 'ctypes')
    def test_failed_query_reports_not_running(self, mock_ctypes: MagicMock):
        """A failed query must not look like a covered screen."""
        mock_ctypes.windll.user32.SystemParametersInfoW.return_value = 0
        mock_ctypes.wintypes.BOOL.return_value.value = 1
        self.assertFalse(win32.is_screensaver_running())


class TestWatchThemeChange(unittest.TestCase):
    """Tests for watch_theme_change() - the registry-based theme watcher."""

    @patch('ctypes.windll.advapi32.RegNotifyChangeKeyValue')
    @patch.object(win32, 'winreg')
    def test_callback_exception_does_not_end_watcher(self, mock_winreg, mock_notify):
        """A transient callback failure (e.g. re-render error during an Explorer
        restart) must not end theme watching for the rest of the session."""
        mock_winreg.OpenKey.return_value.__enter__.return_value = 1234
        mock_notify.side_effect = [0, 0, 1]  # two theme changes, then watcher exit

        calls = []

        def callback():
            calls.append(1)
            if len(calls) == 1:
                raise RuntimeError('transient render failure')

        win32.watch_theme_change(callback)

        self.assertEqual(len(calls), 2)

    @patch('ctypes.windll.advapi32.RegNotifyChangeKeyValue')
    @patch.object(win32, 'winreg')
    def test_watcher_exits_when_notify_fails(self, mock_winreg, mock_notify):
        """A failing RegNotifyChangeKeyValue ends the watcher without callbacks."""
        mock_winreg.OpenKey.return_value.__enter__.return_value = 1234
        mock_notify.return_value = 1

        callback = MagicMock()
        win32.watch_theme_change(callback)

        callback.assert_not_called()



class TestLoadFont(unittest.TestCase):
    """Tests for load_font()."""

    def setUp(self):
        win32.load_font.cache_clear()

    def tearDown(self):
        win32.load_font.cache_clear()

    @patch.object(win32, 'ImageFont')
    @patch.dict('os.environ', {'WINDIR': r'C:\Windows'})
    def test_loads_arial_bold_for_normal_text(self, mock_image_font):
        """Default call loads Arial Bold font."""
        mock_font = MagicMock()
        mock_image_font.truetype.return_value = mock_font

        result = win32.load_font(42)

        self.assertIs(result, mock_font)
        mock_image_font.truetype.assert_called_once_with(r'C:\Windows\Fonts\arialbd.ttf', 42)

    @patch.object(win32, 'ImageFont')
    @patch.dict('os.environ', {'WINDIR': r'C:\Windows'})
    def test_loads_segoe_symbol_for_symbol_text(self, mock_image_font):
        """symbol=True loads Segoe UI Symbol font."""
        mock_font = MagicMock()
        mock_image_font.truetype.return_value = mock_font

        result = win32.load_font(36, symbol=True)

        self.assertIs(result, mock_font)
        mock_image_font.truetype.assert_called_once_with(r'C:\Windows\Fonts\seguisym.ttf', 36)

    @patch.object(win32, 'ImageFont')
    @patch.dict('os.environ', {'WINDIR': r'C:\Windows'})
    def test_falls_back_to_default_when_all_fail(self, mock_image_font):
        """Falls back to load_default() when no TrueType font found."""
        mock_image_font.truetype.side_effect = OSError
        mock_default = MagicMock()
        mock_image_font.load_default.return_value = mock_default

        result = win32.load_font(42)

        self.assertIs(result, mock_default)
        mock_image_font.load_default.assert_called_once()

    @patch.object(win32, 'ImageFont')
    @patch.dict('os.environ', {'WINDIR': r'C:\Windows'})
    def test_tries_fallback_names_on_failure(self, mock_image_font):
        """Tries alternative font names when first attempt fails."""
        mock_font = MagicMock()
        mock_image_font.truetype.side_effect = [OSError, mock_font]

        result = win32.load_font(42)

        self.assertIs(result, mock_font)
        self.assertEqual(mock_image_font.truetype.call_count, 2)
        mock_image_font.truetype.assert_called_with('arialbd.ttf', 42)

    @patch.object(win32, 'ImageFont')
    @patch.dict('os.environ', {'WINDIR': r'C:\Windows'})
    def test_lru_cache_returns_same_instance(self, mock_image_font):
        """Cached: same size returns same font object without second truetype call."""
        mock_font = MagicMock()
        mock_image_font.truetype.return_value = mock_font

        first = win32.load_font(42)
        second = win32.load_font(42)

        self.assertIs(first, second)
        mock_image_font.truetype.assert_called_once()

    @patch.object(win32, 'ImageFont')
    @patch.dict('os.environ', {'WINDIR': r'C:\Windows'})
    def test_different_sizes_cached_separately(self, mock_image_font):
        """Different sizes produce separate cache entries."""
        mock_image_font.truetype.return_value = MagicMock()

        win32.load_font(36)
        win32.load_font(42)

        self.assertEqual(mock_image_font.truetype.call_count, 2)

    @patch.object(win32, 'ImageFont')
    @patch.dict('os.environ', {}, clear=True)
    def test_uses_default_windir_when_not_set(self, mock_image_font):
        """Falls back to C:\\Windows when WINDIR is not set."""
        mock_font = MagicMock()
        mock_image_font.truetype.return_value = mock_font

        win32.load_font(42)

        mock_image_font.truetype.assert_called_once_with(r'C:\Windows\Fonts\arialbd.ttf', 42)


class TestTaskbarUsesLightTheme(unittest.TestCase):
    """Tests for taskbar_uses_light_theme()."""

    @patch.object(win32, 'winreg')
    def test_returns_true_for_light_theme(self, mock_winreg):
        """Registry value 1 means light theme."""
        mock_key = MagicMock()
        mock_winreg.OpenKey.return_value.__enter__ = MagicMock(return_value=mock_key)
        mock_winreg.OpenKey.return_value.__exit__ = MagicMock(return_value=False)
        mock_winreg.QueryValueEx.return_value = (1, 4)

        self.assertTrue(win32.taskbar_uses_light_theme())

    @patch.object(win32, 'winreg')
    def test_returns_false_for_dark_theme(self, mock_winreg):
        """Registry value 0 means dark theme."""
        mock_key = MagicMock()
        mock_winreg.OpenKey.return_value.__enter__ = MagicMock(return_value=mock_key)
        mock_winreg.OpenKey.return_value.__exit__ = MagicMock(return_value=False)
        mock_winreg.QueryValueEx.return_value = (0, 4)

        self.assertFalse(win32.taskbar_uses_light_theme())

    @patch.object(win32, 'winreg')
    def test_returns_false_on_os_error(self, mock_winreg):
        """OSError (missing key, permissions) defaults to dark."""
        mock_winreg.OpenKey.side_effect = OSError

        self.assertFalse(win32.taskbar_uses_light_theme())

    @patch.object(win32, 'winreg')
    def test_reads_correct_registry_path(self, mock_winreg):
        """Opens the Personalize registry key."""
        mock_winreg.OpenKey.return_value.__enter__ = MagicMock()
        mock_winreg.OpenKey.return_value.__exit__ = MagicMock(return_value=False)
        mock_winreg.QueryValueEx.return_value = (0, 4)

        win32.taskbar_uses_light_theme()

        mock_winreg.OpenKey.assert_called_once_with(
            mock_winreg.HKEY_CURRENT_USER, win32.THEME_REG_KEY,
        )


class _DefaultConfigDirTestCase(unittest.TestCase):
    """Base class pinning the default config dir (no per-instance suffix)."""

    def setUp(self):
        patcher_suffix = patch.object(win32, 'config_dir_suffix', return_value='')
        patcher_default = patch.object(win32, 'is_default_config_dir', return_value=True)
        patcher_suffix.start()
        patcher_default.start()
        self.addCleanup(patcher_suffix.stop)
        self.addCleanup(patcher_default.stop)


class TestIsAutostartEnabled(_DefaultConfigDirTestCase):
    """Tests for is_autostart_enabled()."""

    @patch.object(win32, 'winreg')
    def test_returns_true_when_value_exists(self, mock_winreg):
        """Registry entry found returns True."""
        mock_key = MagicMock()
        mock_winreg.OpenKey.return_value.__enter__ = MagicMock(return_value=mock_key)
        mock_winreg.OpenKey.return_value.__exit__ = MagicMock(return_value=False)

        self.assertTrue(win32.is_autostart_enabled())
        mock_winreg.QueryValueEx.assert_called_once_with(mock_key, win32.AUTOSTART_REG_BASE_NAME)

    @patch.object(win32, 'winreg')
    def test_returns_false_when_key_missing(self, mock_winreg):
        """FileNotFoundError on key open returns False."""
        mock_winreg.OpenKey.side_effect = FileNotFoundError

        self.assertFalse(win32.is_autostart_enabled())

    @patch.object(win32, 'winreg')
    def test_returns_false_when_value_missing(self, mock_winreg):
        """Key exists but value does not returns False."""
        mock_key = MagicMock()
        mock_winreg.OpenKey.return_value.__enter__ = MagicMock(return_value=mock_key)
        mock_winreg.OpenKey.return_value.__exit__ = MagicMock(return_value=False)
        mock_winreg.QueryValueEx.side_effect = FileNotFoundError

        self.assertFalse(win32.is_autostart_enabled())

    @patch.object(win32, 'winreg')
    def test_opens_correct_registry_path(self, mock_winreg):
        """Opens HKCU Run key with correct path."""
        mock_winreg.OpenKey.return_value.__enter__ = MagicMock()
        mock_winreg.OpenKey.return_value.__exit__ = MagicMock(return_value=False)

        win32.is_autostart_enabled()

        mock_winreg.OpenKey.assert_called_once_with(
            mock_winreg.HKEY_CURRENT_USER, win32.AUTOSTART_REG_KEY,
        )


class TestSetAutostart(_DefaultConfigDirTestCase):
    """Tests for set_autostart()."""

    @patch.object(win32, 'winreg')
    def test_enable_sets_quoted_executable_path(self, mock_winreg):
        """Enabling autostart writes the quoted sys.executable path."""
        mock_key = MagicMock()
        mock_winreg.OpenKey.return_value.__enter__ = MagicMock(return_value=mock_key)
        mock_winreg.OpenKey.return_value.__exit__ = MagicMock(return_value=False)

        win32.set_autostart(True)

        mock_winreg.SetValueEx.assert_called_once_with(
            mock_key, win32.AUTOSTART_REG_BASE_NAME, 0,
            mock_winreg.REG_SZ, f'"{sys.executable}"',
        )

    @patch.object(win32, 'winreg')
    def test_disable_deletes_registry_value(self, mock_winreg):
        """Disabling autostart removes the registry value."""
        mock_key = MagicMock()
        mock_winreg.OpenKey.return_value.__enter__ = MagicMock(return_value=mock_key)
        mock_winreg.OpenKey.return_value.__exit__ = MagicMock(return_value=False)

        win32.set_autostart(False)

        mock_winreg.DeleteValue.assert_called_once_with(mock_key, win32.AUTOSTART_REG_BASE_NAME)
        mock_winreg.SetValueEx.assert_not_called()

    @patch.object(win32, 'winreg')
    def test_disable_ignores_missing_value(self, mock_winreg):
        """Disabling when value already absent does not raise."""
        mock_key = MagicMock()
        mock_winreg.OpenKey.return_value.__enter__ = MagicMock(return_value=mock_key)
        mock_winreg.OpenKey.return_value.__exit__ = MagicMock(return_value=False)
        mock_winreg.DeleteValue.side_effect = FileNotFoundError

        win32.set_autostart(False)  # should not raise

    @patch.object(win32, 'winreg')
    def test_enable_opens_with_set_value_permission(self, mock_winreg):
        """Opening registry for write uses KEY_SET_VALUE."""
        mock_winreg.OpenKey.return_value.__enter__ = MagicMock()
        mock_winreg.OpenKey.return_value.__exit__ = MagicMock(return_value=False)

        win32.set_autostart(True)

        mock_winreg.OpenKey.assert_called_once_with(
            mock_winreg.HKEY_CURRENT_USER, win32.AUTOSTART_REG_KEY,
            0, mock_winreg.KEY_SET_VALUE,
        )

    @patch.object(win32, 'winreg')
    def test_enable_uses_current_executable(self, mock_winreg):
        """Uses sys.executable for the registry value."""
        mock_key = MagicMock()
        mock_winreg.OpenKey.return_value.__enter__ = MagicMock(return_value=mock_key)
        mock_winreg.OpenKey.return_value.__exit__ = MagicMock(return_value=False)

        with patch.object(sys, 'executable', r'C:\Program Files\MyApp\app.exe'):
            win32.set_autostart(True)

        mock_winreg.SetValueEx.assert_called_once_with(
            mock_key, win32.AUTOSTART_REG_BASE_NAME, 0,
            mock_winreg.REG_SZ, r'"C:\Program Files\MyApp\app.exe"',
        )


class TestSyncAutostartPath(_DefaultConfigDirTestCase):
    """Tests for sync_autostart_path()."""

    @patch.object(win32, 'winreg')
    def test_returns_early_when_no_registry_entry(self, mock_winreg):
        """No registry entry means no update attempted."""
        mock_winreg.OpenKey.side_effect = FileNotFoundError

        win32.sync_autostart_path()  # should not raise

        mock_winreg.SetValueEx.assert_not_called()

    @patch.object(win32, 'set_autostart')
    @patch.object(win32, 'winreg')
    def test_updates_when_path_differs(self, mock_winreg, mock_set):
        """Stored path differs from sys.executable triggers update."""
        mock_key = MagicMock()
        mock_winreg.OpenKey.return_value.__enter__ = MagicMock(return_value=mock_key)
        mock_winreg.OpenKey.return_value.__exit__ = MagicMock(return_value=False)
        mock_winreg.QueryValueEx.return_value = (r'"C:\old\path.exe"', 1)

        with patch.object(sys, 'executable', r'C:\new\path.exe'):
            win32.sync_autostart_path()

        mock_set.assert_called_once_with(True)

    @patch.object(win32, 'set_autostart')
    @patch.object(win32, 'winreg')
    def test_skips_update_when_path_matches(self, mock_winreg, mock_set):
        """Matching stored path skips the update."""
        mock_key = MagicMock()
        mock_winreg.OpenKey.return_value.__enter__ = MagicMock(return_value=mock_key)
        mock_winreg.OpenKey.return_value.__exit__ = MagicMock(return_value=False)

        expected = f'"{sys.executable}"'
        mock_winreg.QueryValueEx.return_value = (expected, 1)

        win32.sync_autostart_path()

        mock_set.assert_not_called()


class TestCustomConfigDirAutostart(unittest.TestCase):
    """Per-instance registry naming and command for a non-default config dir."""

    def setUp(self):
        patcher_suffix = patch.object(win32, 'config_dir_suffix', return_value='_abc123def456')
        patcher_default = patch.object(win32, 'is_default_config_dir', return_value=False)
        patcher_env = patch.dict('os.environ', {'CLAUDE_CONFIG_DIR': r'C:\Users\test\.claude-second'})
        patcher_suffix.start()
        patcher_default.start()
        patcher_env.start()
        self.addCleanup(patcher_suffix.stop)
        self.addCleanup(patcher_default.stop)
        self.addCleanup(patcher_env.stop)

    @patch.object(win32, 'winreg')
    def test_enable_uses_suffixed_value_name(self, mock_winreg):
        """Non-default config dir writes a suffixed registry value name."""
        mock_key = MagicMock()
        mock_winreg.OpenKey.return_value.__enter__ = MagicMock(return_value=mock_key)
        mock_winreg.OpenKey.return_value.__exit__ = MagicMock(return_value=False)

        win32.set_autostart(True)

        name = mock_winreg.SetValueEx.call_args[0][1]
        self.assertEqual(name, 'UsageMonitorForClaude_abc123def456')

    @patch.object(win32, 'winreg')
    def test_enable_command_includes_config_dir(self, mock_winreg):
        """Stored command carries --config-dir so autostart targets the right account."""
        mock_key = MagicMock()
        mock_winreg.OpenKey.return_value.__enter__ = MagicMock(return_value=mock_key)
        mock_winreg.OpenKey.return_value.__exit__ = MagicMock(return_value=False)

        win32.set_autostart(True)

        command = mock_winreg.SetValueEx.call_args[0][4]
        self.assertEqual(command, f'"{sys.executable}" --config-dir="C:\\Users\\test\\.claude-second"')

    @patch.object(win32, 'winreg')
    def test_disable_deletes_suffixed_value(self, mock_winreg):
        """Disabling removes the per-instance registry value."""
        mock_key = MagicMock()
        mock_winreg.OpenKey.return_value.__enter__ = MagicMock(return_value=mock_key)
        mock_winreg.OpenKey.return_value.__exit__ = MagicMock(return_value=False)

        win32.set_autostart(False)

        mock_winreg.DeleteValue.assert_called_once_with(mock_key, 'UsageMonitorForClaude_abc123def456')

    @patch.object(win32, 'set_autostart')
    @patch.object(win32, 'winreg')
    def test_sync_skips_when_command_matches(self, mock_winreg, mock_set):
        """sync_autostart_path() compares against the command including --config-dir."""
        mock_key = MagicMock()
        mock_winreg.OpenKey.return_value.__enter__ = MagicMock(return_value=mock_key)
        mock_winreg.OpenKey.return_value.__exit__ = MagicMock(return_value=False)
        stored = f'"{sys.executable}" --config-dir="C:\\Users\\test\\.claude-second"'
        mock_winreg.QueryValueEx.return_value = (stored, 1)

        win32.sync_autostart_path()

        mock_set.assert_not_called()

    @patch.object(win32, 'set_autostart')
    @patch.object(win32, 'winreg')
    def test_sync_updates_when_flag_missing(self, mock_winreg, mock_set):
        """A stored command without --config-dir is rewritten."""
        mock_key = MagicMock()
        mock_winreg.OpenKey.return_value.__enter__ = MagicMock(return_value=mock_key)
        mock_winreg.OpenKey.return_value.__exit__ = MagicMock(return_value=False)
        mock_winreg.QueryValueEx.return_value = (f'"{sys.executable}"', 1)

        win32.sync_autostart_path()

        mock_set.assert_called_once_with(True)


class TestRegisterNotificationIdentity(unittest.TestCase):
    """Tests for register_notification_identity()."""

    @patch.object(win32, 'ctypes')
    @patch.object(win32, 'winreg')
    def test_registers_name_icon_and_sets_aumid(self, mock_winreg, mock_ctypes):
        """A present logo writes DisplayName + IconUri (the logo path) and then adopts the AUMID."""
        logo = MagicMock()
        logo.is_file.return_value = True
        logo.__str__.return_value = r'C:\fake\notification_logo.ico'

        with patch.object(win32, '_NOTIFICATION_LOGO', logo):
            win32.register_notification_identity()

        mock_winreg.CreateKey.assert_called_once_with(mock_winreg.HKEY_CURRENT_USER, win32._IDENTITY_REG_PATH)
        writes = {c.args[1]: c.args[4] for c in mock_winreg.SetValueEx.call_args_list}
        self.assertEqual(list(writes), ['DisplayName', 'IconUri'])
        self.assertEqual(writes['IconUri'], r'C:\fake\notification_logo.ico')
        mock_ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID.assert_called_once()

    @patch.object(win32, 'ctypes')
    @patch.object(win32, 'winreg')
    def test_writes_configured_display_name(self, mock_winreg, mock_ctypes):
        """DisplayName is written with the module's brand name."""
        logo = MagicMock()
        logo.is_file.return_value = True

        with patch.object(win32, '_NOTIFICATION_LOGO', logo):
            win32.register_notification_identity()

        display_call = next(c for c in mock_winreg.SetValueEx.call_args_list if c.args[1] == 'DisplayName')
        self.assertEqual(display_call.args[4], win32.DISPLAY_NAME)

    @patch.object(win32, 'ctypes')
    @patch.object(win32, 'winreg')
    def test_skips_everything_when_logo_missing(self, mock_winreg, mock_ctypes):
        """A missing logo leaves the default identity (tray icon) untouched."""
        logo = MagicMock()
        logo.is_file.return_value = False

        with patch.object(win32, '_NOTIFICATION_LOGO', logo):
            win32.register_notification_identity()

        mock_winreg.CreateKey.assert_not_called()
        mock_ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID.assert_not_called()

    @patch.object(win32, 'ctypes')
    @patch.object(win32, 'winreg')
    def test_does_not_adopt_aumid_when_registry_fails(self, mock_winreg, mock_ctypes):
        """A registry write failure keeps the tray icon rather than an empty one."""
        mock_winreg.CreateKey.side_effect = OSError('access denied')
        logo = MagicMock()
        logo.is_file.return_value = True

        with patch.object(win32, '_NOTIFICATION_LOGO', logo):
            win32.register_notification_identity()

        mock_ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID.assert_not_called()

    def test_registry_path_matches_aumid(self):
        """The registry path targets the same AUMID the process adopts."""
        self.assertTrue(win32._IDENTITY_REG_PATH.endswith(win32.APP_USER_MODEL_ID))
        self.assertIn(r'Software\Classes\AppUserModelId', win32._IDENTITY_REG_PATH)


class TestWebview2Version(unittest.TestCase):
    """Tests for win32._webview2_version() registry lookup."""

    def _mock_open_key(self, versions):
        """Create a mock winreg.OpenKey that returns versions for matching paths."""
        from contextlib import contextmanager

        @contextmanager
        def open_key(root, path):
            for guid, version in versions:
                if guid in path:
                    mock_key = MagicMock()
                    mock_key.__enter__ = MagicMock(return_value=mock_key)
                    mock_key.__exit__ = MagicMock(return_value=False)
                    yield mock_key
                    return
            raise OSError('key not found')

        return open_key

    def test_runtime_found(self):
        """Returns version when Runtime GUID is in registry."""
        runtime_guid = '{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}'
        with patch('usage_monitor_for_claude.platforms.win32.winreg') as mock_winreg:
            mock_winreg.HKEY_CURRENT_USER = 0x80000001
            mock_winreg.HKEY_LOCAL_MACHINE = 0x80000002
            mock_winreg.OpenKey = self._mock_open_key([(runtime_guid, '130.0.2849.56')])
            mock_winreg.QueryValueEx = MagicMock(return_value=('130.0.2849.56', 1))
            result = win32._webview2_version()
        self.assertEqual(result, '130.0.2849.56')

    def test_beta_channel_labeled(self):
        """Non-Runtime channels include the channel name."""
        beta_guid = '{2CD8A007-E189-409D-A2C8-9AF4EF3C72AA}'
        with patch('usage_monitor_for_claude.platforms.win32.winreg') as mock_winreg:
            mock_winreg.HKEY_CURRENT_USER = 0x80000001
            mock_winreg.HKEY_LOCAL_MACHINE = 0x80000002
            mock_winreg.OpenKey = self._mock_open_key([(beta_guid, '131.0.0.1')])
            mock_winreg.QueryValueEx = MagicMock(return_value=('131.0.0.1', 1))
            result = win32._webview2_version()
        self.assertIn('Beta', result)
        self.assertIn('131.0.0.1', result)

    def test_not_found(self):
        """Returns 'not found' when no registry keys exist."""
        with patch('usage_monitor_for_claude.platforms.win32.winreg') as mock_winreg:
            mock_winreg.HKEY_CURRENT_USER = 0x80000001
            mock_winreg.HKEY_LOCAL_MACHINE = 0x80000002
            mock_winreg.OpenKey = MagicMock(side_effect=OSError)
            result = win32._webview2_version()
        self.assertEqual(result, 'not found')

    def test_zero_version_skipped(self):
        """Version '0.0.0.0' is treated as not installed."""
        runtime_guid = '{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}'
        with patch('usage_monitor_for_claude.platforms.win32.winreg') as mock_winreg:
            mock_winreg.HKEY_CURRENT_USER = 0x80000001
            mock_winreg.HKEY_LOCAL_MACHINE = 0x80000002
            mock_winreg.OpenKey = self._mock_open_key([(runtime_guid, '0.0.0.0')])
            mock_winreg.QueryValueEx = MagicMock(return_value=('0.0.0.0', 1))
            result = win32._webview2_version()
        self.assertEqual(result, 'not found')


class TestDotnetVersion(unittest.TestCase):
    """Tests for win32._dotnet_version() registry lookup."""

    def test_dotnet_481(self):
        """Release >= 533320 reports 4.8.1."""
        with patch('usage_monitor_for_claude.platforms.win32.winreg') as mock_winreg:
            mock_key = MagicMock()
            mock_winreg.OpenKey = MagicMock(return_value=mock_key)
            mock_key.__enter__ = MagicMock(return_value=mock_key)
            mock_key.__exit__ = MagicMock(return_value=False)
            mock_winreg.QueryValueEx = MagicMock(return_value=(533509, 4))
            result = win32._dotnet_version()
        self.assertIn('4.8.1', result)
        self.assertIn('533509', result)

    def test_dotnet_462(self):
        """Release >= 394802 reports 4.6.2."""
        with patch('usage_monitor_for_claude.platforms.win32.winreg') as mock_winreg:
            mock_key = MagicMock()
            mock_winreg.OpenKey = MagicMock(return_value=mock_key)
            mock_key.__enter__ = MagicMock(return_value=mock_key)
            mock_key.__exit__ = MagicMock(return_value=False)
            mock_winreg.QueryValueEx = MagicMock(return_value=(394802, 4))
            result = win32._dotnet_version()
        self.assertIn('4.6.2', result)

    def test_dotnet_below_46(self):
        """Release below 393295 reports < 4.6."""
        with patch('usage_monitor_for_claude.platforms.win32.winreg') as mock_winreg:
            mock_key = MagicMock()
            mock_winreg.OpenKey = MagicMock(return_value=mock_key)
            mock_key.__enter__ = MagicMock(return_value=mock_key)
            mock_key.__exit__ = MagicMock(return_value=False)
            mock_winreg.QueryValueEx = MagicMock(return_value=(300000, 4))
            result = win32._dotnet_version()
        self.assertIn('< 4.6', result)

    def test_dotnet_not_found(self):
        """Missing registry key returns 'not found'."""
        with patch('usage_monitor_for_claude.platforms.win32.winreg') as mock_winreg:
            mock_winreg.OpenKey = MagicMock(side_effect=OSError)
            result = win32._dotnet_version()
        self.assertEqual(result, 'not found')


class TestDpiInfo(unittest.TestCase):
    """Tests for win32._dpi_info()."""

    def test_per_monitor_v2_150_percent(self):
        """Reports Per-Monitor V2 and 150% scaling."""
        with patch('usage_monitor_for_claude.platforms.win32.ctypes') as mock_ctypes:
            user32 = mock_ctypes.windll.user32
            user32.GetThreadDpiAwarenessContext.return_value = -4
            user32.GetAwarenessFromDpiAwarenessContext.return_value = 2
            user32.GetDpiForSystem.return_value = 144
            awareness, dpi = win32._dpi_info()
        self.assertEqual(awareness, 'Per-Monitor V2')
        self.assertEqual(dpi, '144 (150%)')

    def test_system_aware_100_percent(self):
        """Reports System aware and 100% scaling."""
        with patch('usage_monitor_for_claude.platforms.win32.ctypes') as mock_ctypes:
            user32 = mock_ctypes.windll.user32
            user32.GetThreadDpiAwarenessContext.return_value = -2
            user32.GetAwarenessFromDpiAwarenessContext.return_value = 1
            user32.GetDpiForSystem.return_value = 96
            awareness, dpi = win32._dpi_info()
        self.assertEqual(awareness, 'System')
        self.assertEqual(dpi, '96 (100%)')

    def test_unavailable_on_error(self):
        """Returns 'unavailable' when API calls fail."""
        with patch('usage_monitor_for_claude.platforms.win32.ctypes') as mock_ctypes:
            user32 = mock_ctypes.windll.user32
            user32.GetThreadDpiAwarenessContext.side_effect = Exception('no API')
            user32.GetDpiForSystem.side_effect = Exception('no API')
            awareness, dpi = win32._dpi_info()
        self.assertEqual(awareness, 'unavailable')
        self.assertEqual(dpi, 'unavailable')


class TestScreenInfo(unittest.TestCase):
    """Tests for win32._screen_info()."""

    def test_normal_values(self):
        """Returns formatted monitor count, resolution, and work area."""
        with patch('usage_monitor_for_claude.platforms.win32.ctypes') as mock_ctypes:
            user32 = mock_ctypes.windll.user32
            user32.GetSystemMetrics.side_effect = lambda x: {80: 2, 0: 2560, 1: 1440}[x]

            rect = MagicMock()
            rect.left, rect.top, rect.right, rect.bottom = 0, 0, 2560, 1392
            mock_ctypes.wintypes.RECT.return_value = rect
            user32.SystemParametersInfoW.return_value = 1

            monitors, primary, work_area = win32._screen_info()
        self.assertEqual(monitors, '2')
        self.assertEqual(primary, '2560 x 1440')
        self.assertIn('2560 x 1392', work_area)

    def test_unavailable_on_error(self):
        """Returns 'unavailable' when system calls fail."""
        with patch('usage_monitor_for_claude.platforms.win32.ctypes') as mock_ctypes:
            user32 = mock_ctypes.windll.user32
            user32.GetSystemMetrics.side_effect = Exception('fail')
            mock_ctypes.wintypes.RECT.side_effect = Exception('fail')

            monitors, primary, work_area = win32._screen_info()
        self.assertEqual(monitors, 'unavailable')
        self.assertEqual(primary, 'unavailable')
        self.assertEqual(work_area, 'unavailable')


class TestSetupConsole(unittest.TestCase):
    """Tests for routing verbose output in a windowless build."""

    def setUp(self):
        # setup_console() reassigns both streams; restore them so a mock
        # does not stay installed for the rest of the suite.
        self._streams = (sys.stdout, sys.stderr)

    def tearDown(self):
        sys.stdout, sys.stderr = self._streams

    @staticmethod
    def _set_std_handles(mock_ctypes: MagicMock, stdout: int | None, stderr: int | None, file_type: int = win32._FILE_TYPE_DISK) -> None:
        """Make GetStdHandle report *stdout* and *stderr* as redirected handles.

        ``None`` stands for a stream the caller did not redirect; it is
        reported as a console handle (``FILE_TYPE_CHAR``).
        """
        FILE_TYPE_CHAR = 0x0002
        console_handle = 900
        file_types = {console_handle: FILE_TYPE_CHAR}
        handles = {win32._STD_OUTPUT_HANDLE: stdout, win32._STD_ERROR_HANDLE: stderr}

        for handle in (stdout, stderr):
            if handle is not None:
                file_types[handle] = file_type

        mock_ctypes.windll.kernel32.GetStdHandle.side_effect = lambda std: handles[std] or console_handle
        mock_ctypes.windll.kernel32.GetFileType.side_effect = file_types.get

    @patch.object(win32, 'ctypes')
    @patch('builtins.open')
    def test_attaches_parent_console_first(self, _mock_open: MagicMock, mock_ctypes: MagicMock):
        """A console-launched process writes into the console it came from."""
        self._set_std_handles(mock_ctypes, None, None)
        mock_ctypes.windll.kernel32.AttachConsole.return_value = 1
        win32.setup_console()
        mock_ctypes.windll.kernel32.AttachConsole.assert_called_once_with(-1)
        mock_ctypes.windll.kernel32.AllocConsole.assert_not_called()

    @patch.object(win32, 'ctypes')
    @patch('builtins.open')
    def test_allocates_console_on_attach_failure(self, _mock_open: MagicMock, mock_ctypes: MagicMock):
        """A double-clicked process gets a console of its own."""
        self._set_std_handles(mock_ctypes, None, None)
        mock_ctypes.windll.kernel32.AttachConsole.return_value = 0
        win32.setup_console()
        mock_ctypes.windll.kernel32.AllocConsole.assert_called_once()

    @patch.object(win32, 'ctypes')
    @patch('builtins.open')
    def test_sets_pywebview_log(self, _mock_open: MagicMock, mock_ctypes: MagicMock):
        """pywebview's own logging is raised alongside our diagnostics."""
        self._set_std_handles(mock_ctypes, None, None)
        with patch.dict(win32.os.environ, {}, clear=True):
            win32.setup_console()
            self.assertEqual(win32.os.environ['PYWEBVIEW_LOG'], 'DEBUG')

    @patch.object(win32.msvcrt, 'open_osfhandle')
    @patch.object(win32, 'ctypes')
    @patch('builtins.open')
    def test_keeps_redirected_streams(self, mock_open: MagicMock, mock_ctypes: MagicMock, mock_osfhandle: MagicMock):
        """``app.exe --verbose > log.txt`` writes into the file, not the console."""
        self._set_std_handles(mock_ctypes, 100, 200)
        mock_osfhandle.side_effect = {100: 3, 200: 4}.get
        mock_open.side_effect = lambda target, *args, **kwargs: f'stream-{target}'

        win32.setup_console()

        self.assertEqual(sys.stdout, 'stream-3')
        self.assertEqual(sys.stderr, 'stream-4')
        mock_ctypes.windll.kernel32.AttachConsole.assert_not_called()
        mock_ctypes.windll.kernel32.AllocConsole.assert_not_called()

    @patch.object(win32.msvcrt, 'open_osfhandle')
    @patch.object(win32, 'ctypes')
    @patch('builtins.open')
    def test_console_covers_the_stream_without_a_redirect(self, mock_open: MagicMock, mock_ctypes: MagicMock, mock_osfhandle: MagicMock):
        """Redirecting only stdout leaves stderr on the console."""
        self._set_std_handles(mock_ctypes, 100, None)
        mock_osfhandle.return_value = 3
        mock_open.side_effect = lambda target, *args, **kwargs: f'stream-{target}'
        mock_ctypes.windll.kernel32.AttachConsole.return_value = 1

        win32.setup_console()

        self.assertEqual(sys.stdout, 'stream-3')
        self.assertEqual(sys.stderr, 'stream-CONOUT$')
        mock_ctypes.windll.kernel32.AttachConsole.assert_called_once_with(-1)

    @patch.object(win32.msvcrt, 'open_osfhandle')
    @patch.object(win32, 'ctypes')
    @patch('builtins.open')
    def test_shared_handle_is_wrapped_once(self, mock_open: MagicMock, mock_ctypes: MagicMock, mock_osfhandle: MagicMock):
        """One file passed for both streams must not be closed twice on exit."""
        self._set_std_handles(mock_ctypes, 100, 100)
        mock_osfhandle.return_value = 3
        mock_open.side_effect = lambda target, *args, **kwargs: f'stream-{target}'

        win32.setup_console()

        self.assertIs(sys.stdout, sys.stderr)
        mock_osfhandle.assert_called_once_with(100, win32.os.O_WRONLY)

    @patch.object(win32.msvcrt, 'open_osfhandle')
    @patch.object(win32, 'ctypes')
    @patch('builtins.open')
    def test_pipe_counts_as_a_redirect(self, mock_open: MagicMock, mock_ctypes: MagicMock, mock_osfhandle: MagicMock):
        """``Start-Process -RedirectStandardOutput`` hands over a pipe, not a file."""
        self._set_std_handles(mock_ctypes, 100, 200, file_type=win32._FILE_TYPE_PIPE)
        mock_osfhandle.side_effect = {100: 3, 200: 4}.get
        mock_open.side_effect = lambda target, *args, **kwargs: f'stream-{target}'

        win32.setup_console()

        self.assertEqual(sys.stdout, 'stream-3')
        self.assertEqual(sys.stderr, 'stream-4')
        mock_ctypes.windll.kernel32.AttachConsole.assert_not_called()

    @patch.object(win32, 'ctypes')
    @patch('builtins.open')
    def test_missing_handle_falls_back_to_console(self, mock_open: MagicMock, mock_ctypes: MagicMock):
        """A process started without console or redirection has no usable handle."""
        mock_ctypes.windll.kernel32.GetStdHandle.side_effect = [None, win32._INVALID_HANDLE]
        mock_open.side_effect = lambda target, *args, **kwargs: f'stream-{target}'
        mock_ctypes.windll.kernel32.AttachConsole.return_value = 0

        win32.setup_console()

        self.assertEqual(sys.stdout, 'stream-CONOUT$')
        self.assertEqual(sys.stderr, 'stream-CONOUT$')
        mock_ctypes.windll.kernel32.GetFileType.assert_not_called()
        mock_ctypes.windll.kernel32.AllocConsole.assert_called_once()

    @patch.object(win32.msvcrt, 'open_osfhandle')
    @patch.object(win32, 'ctypes')
    @patch('builtins.open')
    def test_unwrappable_handle_falls_back_to_console(self, mock_open: MagicMock, mock_ctypes: MagicMock, mock_osfhandle: MagicMock):
        """A redirected handle that cannot be turned into a descriptor is not fatal."""
        self._set_std_handles(mock_ctypes, 100, 200)
        mock_osfhandle.side_effect = OSError('bad handle')
        mock_open.side_effect = lambda target, *args, **kwargs: f'stream-{target}'
        mock_ctypes.windll.kernel32.AttachConsole.return_value = 1

        win32.setup_console()

        self.assertEqual(sys.stdout, 'stream-CONOUT$')
        self.assertEqual(sys.stderr, 'stream-CONOUT$')
        mock_ctypes.windll.kernel32.AttachConsole.assert_called_once_with(-1)


class TestInstallTrayClickHandler(unittest.TestCase):
    """Tests for the double-click dispatcher grafted onto pystray."""

    def setUp(self):
        self.on_single = MagicMock()
        self.on_double = MagicMock()
        self.notify = MagicMock(name='on_notify')
        self.other = MagicMock(name='other')
        self.icon = MagicMock()
        self.icon._on_notify = self.notify
        self.icon._message_handlers = {0x40B: self.notify, 0x0002: self.other}

    def _install(self, interval=0.5):
        with patch.object(win32, 'double_click_seconds', return_value=interval):
            installed = win32.install_tray_click_handler(self.icon, self.on_single, self.on_double)

        return installed, self.icon._message_handlers[0x40B]

    def test_replaces_notify_handler_only(self):
        """The WM_NOTIFY entry is swapped; every other entry stays untouched."""
        installed, handler = self._install()
        self.assertTrue(installed)
        self.assertIsNot(handler, self.notify)
        self.assertIs(self.icon._message_handlers[0x0002], self.other)

    def test_reports_failure_when_the_entry_is_gone(self):
        """A pystray release that renames its internals must be detectable."""
        self.icon._message_handlers = {0x0002: self.other}

        # Not via _install(): it reads back the very entry this test removes.
        with patch.object(win32, 'double_click_seconds', return_value=0.5):
            installed = win32.install_tray_click_handler(self.icon, self.on_single, self.on_double)

        self.assertFalse(installed)

    @patch.object(win32.threading, 'Timer')
    def test_single_release_schedules_deferred_action(self, mock_timer: MagicMock):
        """A left-button release defers the single-click action by the interval."""
        _installed, handler = self._install()
        handler(0, win32.WM_LBUTTONUP)

        self.assertEqual(mock_timer.call_args[0][0], 0.5)
        mock_timer.return_value.start.assert_called_once()

    @patch.object(win32.threading, 'Timer')
    def test_double_click_cancels_and_runs_command(self, mock_timer: MagicMock):
        """A double-click cancels the pending action and runs the command."""
        _installed, handler = self._install()
        handler(0, win32.WM_LBUTTONUP)
        handler(0, win32.WM_LBUTTONDBLCLK)

        mock_timer.return_value.cancel.assert_called_once()
        self.on_double.assert_called_once()
        self.on_single.assert_not_called()

    @patch.object(win32.threading, 'Timer')
    def test_trailing_release_after_double_click_swallowed(self, mock_timer: MagicMock):
        """The release that always follows a double-click schedules nothing."""
        _installed, handler = self._install()
        handler(0, win32.WM_LBUTTONUP)
        handler(0, win32.WM_LBUTTONDBLCLK)
        handler(0, win32.WM_LBUTTONUP)

        self.assertEqual(mock_timer.call_count, 1)

    @patch.object(win32.threading, 'Timer')
    def test_single_click_after_double_click_schedules_again(self, mock_timer: MagicMock):
        """A genuine single click after a completed double-click still defers."""
        _installed, handler = self._install()
        handler(0, win32.WM_LBUTTONUP)
        handler(0, win32.WM_LBUTTONDBLCLK)
        handler(0, win32.WM_LBUTTONUP)  # swallowed trailing release
        handler(0, win32.WM_LBUTTONUP)  # new single click

        self.assertEqual(mock_timer.call_count, 2)

    def test_other_message_falls_through_to_pystray(self):
        """Non-left-button messages delegate to pystray's original handler."""
        _installed, handler = self._install()
        wm_rbuttonup = 0x0205
        handler(7, wm_rbuttonup)

        self.notify.assert_called_once_with(7, wm_rbuttonup)

    def test_deferred_action_runs_after_the_interval(self):
        """Without a second click the single-click action really fires."""
        _installed, handler = self._install(interval=0.01)
        handler(0, win32.WM_LBUTTONUP)
        time.sleep(0.1)

        self.on_single.assert_called_once()

    def test_deferred_action_is_suppressed_by_a_double_click(self):
        """A double-click landing as the timer fires must not also open the popup."""
        _installed, handler = self._install(interval=0.05)
        handler(0, win32.WM_LBUTTONUP)
        handler(0, win32.WM_LBUTTONDBLCLK)
        time.sleep(0.15)

        self.on_single.assert_not_called()
        self.on_double.assert_called_once()


class TestSetDpiAwareness(unittest.TestCase):
    """Tests for opting into Per-Monitor-V2."""

    @patch.object(win32, 'ctypes')
    def test_requests_per_monitor_v2(self, mock_ctypes: MagicMock):
        win32.set_dpi_awareness()
        mock_ctypes.windll.user32.SetProcessDpiAwarenessContext.assert_called_once()

    @patch.object(win32, 'ctypes')
    def test_missing_export_does_not_kill_startup(self, mock_ctypes: MagicMock):
        """Windows before 10 1703 has no such export; pywebview's fallback applies."""
        mock_ctypes.windll.user32.SetProcessDpiAwarenessContext.side_effect = AttributeError
        win32.set_dpi_awareness()


class TestAutostartSupport(unittest.TestCase):
    """Tests for whether the menu may offer autostart."""

    def test_supported_when_frozen(self):
        with patch.object(win32.sys, 'frozen', True, create=True):
            self.assertTrue(win32.autostart_supported())

    def test_unsupported_from_source(self):
        """A Run value holding just the interpreter path would start Python, not the app."""
        with patch.object(win32.sys, 'frozen', False, create=True):
            self.assertFalse(win32.autostart_supported())


class TestPrepareGuiEnvironment(unittest.TestCase):

    def test_is_a_no_op(self):
        """Windows has one GUI host; there is no backend to select."""
        self.assertIsNone(win32.prepare_gui_environment())


if __name__ == '__main__':
    unittest.main()

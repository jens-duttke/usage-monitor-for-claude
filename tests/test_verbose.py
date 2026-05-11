"""
Verbose Diagnostics Tests
==========================

Unit tests for the --verbose diagnostic helpers.
"""
from __future__ import annotations

import io
import unittest
from unittest.mock import MagicMock, patch

from usage_monitor_for_claude.verbose import (
    _credentials_status,
    _dotnet_version,
    _dpi_info,
    _package_version,
    _redact_home,
    _row,
    _screen_info,
    _section,
    _webview2_version,
    print_runtime_diagnostics,
    print_startup_diagnostics,
    setup_console,
)


class TestRedactHome(unittest.TestCase):
    """Tests for _redact_home() path sanitization."""

    def test_replaces_home_prefix(self):
        """Paths under the home directory are redacted with ~."""
        home = str(__import__('pathlib').Path.home())
        self.assertEqual(_redact_home(f'{home}\\.claude\\.credentials.json'), '~\\.claude\\.credentials.json')

    def test_leaves_other_paths_unchanged(self):
        """Paths outside the home directory are not modified."""
        self.assertEqual(_redact_home('D:\\PythonDev\\app.exe'), 'D:\\PythonDev\\app.exe')

    def test_empty_string(self):
        """Empty string is returned unchanged."""
        self.assertEqual(_redact_home(''), '')


class TestSection(unittest.TestCase):
    """Tests for _section() header formatting."""

    def test_prints_title_and_underline(self):
        """Section prints title with matching-length underline."""
        buf = io.StringIO()
        with patch('sys.stdout', buf):
            _section('System')
        lines = buf.getvalue().split('\n')
        self.assertIn('System', lines[1])
        self.assertEqual(len('System'), len(lines[2].strip()))
        self.assertTrue(all(ch == '-' for ch in lines[2].strip()))


class TestRow(unittest.TestCase):
    """Tests for _row() key-value formatting."""

    def test_default_indent(self):
        """Row uses 4-space indent by default."""
        buf = io.StringIO()
        with patch('sys.stdout', buf):
            _row('OS', 'Windows 11')
        output = buf.getvalue()
        self.assertTrue(output.startswith('    '))
        self.assertIn('OS:', output)
        self.assertIn('Windows 11', output)

    def test_custom_indent(self):
        """Row respects custom indent parameter."""
        buf = io.StringIO()
        with patch('sys.stdout', buf):
            _row('Key', 'Value', indent=8)
        self.assertTrue(buf.getvalue().startswith('        '))

    def test_column_alignment(self):
        """Short and long labels produce aligned value columns."""
        buf1 = io.StringIO()
        buf2 = io.StringIO()
        with patch('sys.stdout', buf1):
            _row('OS', 'val1')
        with patch('sys.stdout', buf2):
            _row('Filesystem encoding', 'val2')
        # Values should start at the same column position
        pos1 = buf1.getvalue().index('val1')
        pos2 = buf2.getvalue().index('val2')
        self.assertEqual(pos1, pos2)


class TestPackageVersion(unittest.TestCase):
    """Tests for _package_version()."""

    def test_existing_package(self):
        """Known package returns its version string."""
        version = _package_version('pip')
        self.assertRegex(version, r'^\d+\.\d+')

    def test_missing_package(self):
        """Non-existent package returns 'not found'."""
        self.assertEqual(_package_version('nonexistent-pkg-12345'), 'not found')


class TestWebview2Version(unittest.TestCase):
    """Tests for _webview2_version() registry lookup."""

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
        with patch('usage_monitor_for_claude.verbose.winreg') as mock_winreg:
            mock_winreg.HKEY_CURRENT_USER = 0x80000001
            mock_winreg.HKEY_LOCAL_MACHINE = 0x80000002
            mock_winreg.OpenKey = self._mock_open_key([(runtime_guid, '130.0.2849.56')])
            mock_winreg.QueryValueEx = MagicMock(return_value=('130.0.2849.56', 1))
            result = _webview2_version()
        self.assertEqual(result, '130.0.2849.56')

    def test_beta_channel_labeled(self):
        """Non-Runtime channels include the channel name."""
        beta_guid = '{2CD8A007-E189-409D-A2C8-9AF4EF3C72AA}'
        with patch('usage_monitor_for_claude.verbose.winreg') as mock_winreg:
            mock_winreg.HKEY_CURRENT_USER = 0x80000001
            mock_winreg.HKEY_LOCAL_MACHINE = 0x80000002
            mock_winreg.OpenKey = self._mock_open_key([(beta_guid, '131.0.0.1')])
            mock_winreg.QueryValueEx = MagicMock(return_value=('131.0.0.1', 1))
            result = _webview2_version()
        self.assertIn('Beta', result)
        self.assertIn('131.0.0.1', result)

    def test_not_found(self):
        """Returns 'not found' when no registry keys exist."""
        with patch('usage_monitor_for_claude.verbose.winreg') as mock_winreg:
            mock_winreg.HKEY_CURRENT_USER = 0x80000001
            mock_winreg.HKEY_LOCAL_MACHINE = 0x80000002
            mock_winreg.OpenKey = MagicMock(side_effect=OSError)
            result = _webview2_version()
        self.assertEqual(result, 'not found')

    def test_zero_version_skipped(self):
        """Version '0.0.0.0' is treated as not installed."""
        runtime_guid = '{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}'
        with patch('usage_monitor_for_claude.verbose.winreg') as mock_winreg:
            mock_winreg.HKEY_CURRENT_USER = 0x80000001
            mock_winreg.HKEY_LOCAL_MACHINE = 0x80000002
            mock_winreg.OpenKey = self._mock_open_key([(runtime_guid, '0.0.0.0')])
            mock_winreg.QueryValueEx = MagicMock(return_value=('0.0.0.0', 1))
            result = _webview2_version()
        self.assertEqual(result, 'not found')


class TestDotnetVersion(unittest.TestCase):
    """Tests for _dotnet_version() registry lookup."""

    def test_dotnet_481(self):
        """Release >= 533320 reports 4.8.1."""
        with patch('usage_monitor_for_claude.verbose.winreg') as mock_winreg:
            mock_key = MagicMock()
            mock_winreg.OpenKey = MagicMock(return_value=mock_key)
            mock_key.__enter__ = MagicMock(return_value=mock_key)
            mock_key.__exit__ = MagicMock(return_value=False)
            mock_winreg.QueryValueEx = MagicMock(return_value=(533509, 4))
            result = _dotnet_version()
        self.assertIn('4.8.1', result)
        self.assertIn('533509', result)

    def test_dotnet_462(self):
        """Release >= 394802 reports 4.6.2."""
        with patch('usage_monitor_for_claude.verbose.winreg') as mock_winreg:
            mock_key = MagicMock()
            mock_winreg.OpenKey = MagicMock(return_value=mock_key)
            mock_key.__enter__ = MagicMock(return_value=mock_key)
            mock_key.__exit__ = MagicMock(return_value=False)
            mock_winreg.QueryValueEx = MagicMock(return_value=(394802, 4))
            result = _dotnet_version()
        self.assertIn('4.6.2', result)

    def test_dotnet_below_46(self):
        """Release below 393295 reports < 4.6."""
        with patch('usage_monitor_for_claude.verbose.winreg') as mock_winreg:
            mock_key = MagicMock()
            mock_winreg.OpenKey = MagicMock(return_value=mock_key)
            mock_key.__enter__ = MagicMock(return_value=mock_key)
            mock_key.__exit__ = MagicMock(return_value=False)
            mock_winreg.QueryValueEx = MagicMock(return_value=(300000, 4))
            result = _dotnet_version()
        self.assertIn('< 4.6', result)

    def test_dotnet_not_found(self):
        """Missing registry key returns 'not found'."""
        with patch('usage_monitor_for_claude.verbose.winreg') as mock_winreg:
            mock_winreg.OpenKey = MagicMock(side_effect=OSError)
            result = _dotnet_version()
        self.assertEqual(result, 'not found')


class TestDpiInfo(unittest.TestCase):
    """Tests for _dpi_info()."""

    def test_per_monitor_v2_150_percent(self):
        """Reports Per-Monitor V2 and 150% scaling."""
        with patch('usage_monitor_for_claude.verbose.ctypes') as mock_ctypes:
            user32 = mock_ctypes.windll.user32
            user32.GetThreadDpiAwarenessContext.return_value = -4
            user32.GetAwarenessFromDpiAwarenessContext.return_value = 2
            user32.GetDpiForSystem.return_value = 144
            awareness, dpi = _dpi_info()
        self.assertEqual(awareness, 'Per-Monitor V2')
        self.assertEqual(dpi, '144 (150%)')

    def test_system_aware_100_percent(self):
        """Reports System aware and 100% scaling."""
        with patch('usage_monitor_for_claude.verbose.ctypes') as mock_ctypes:
            user32 = mock_ctypes.windll.user32
            user32.GetThreadDpiAwarenessContext.return_value = -2
            user32.GetAwarenessFromDpiAwarenessContext.return_value = 1
            user32.GetDpiForSystem.return_value = 96
            awareness, dpi = _dpi_info()
        self.assertEqual(awareness, 'System')
        self.assertEqual(dpi, '96 (100%)')

    def test_unavailable_on_error(self):
        """Returns 'unavailable' when API calls fail."""
        with patch('usage_monitor_for_claude.verbose.ctypes') as mock_ctypes:
            user32 = mock_ctypes.windll.user32
            user32.GetThreadDpiAwarenessContext.side_effect = Exception('no API')
            user32.GetDpiForSystem.side_effect = Exception('no API')
            awareness, dpi = _dpi_info()
        self.assertEqual(awareness, 'unavailable')
        self.assertEqual(dpi, 'unavailable')


class TestScreenInfo(unittest.TestCase):
    """Tests for _screen_info()."""

    def test_normal_values(self):
        """Returns formatted monitor count, resolution, and work area."""
        with patch('usage_monitor_for_claude.verbose.ctypes') as mock_ctypes:
            user32 = mock_ctypes.windll.user32
            user32.GetSystemMetrics.side_effect = lambda x: {80: 2, 0: 2560, 1: 1440}[x]

            rect = MagicMock()
            rect.left, rect.top, rect.right, rect.bottom = 0, 0, 2560, 1392
            mock_ctypes.wintypes.RECT.return_value = rect
            user32.SystemParametersInfoW.return_value = 1

            monitors, primary, work_area = _screen_info()
        self.assertEqual(monitors, '2')
        self.assertEqual(primary, '2560 x 1440')
        self.assertIn('2560 x 1392', work_area)

    def test_unavailable_on_error(self):
        """Returns 'unavailable' when system calls fail."""
        with patch('usage_monitor_for_claude.verbose.ctypes') as mock_ctypes:
            user32 = mock_ctypes.windll.user32
            user32.GetSystemMetrics.side_effect = Exception('fail')
            mock_ctypes.wintypes.RECT.side_effect = Exception('fail')

            monitors, primary, work_area = _screen_info()
        self.assertEqual(monitors, 'unavailable')
        self.assertEqual(primary, 'unavailable')
        self.assertEqual(work_area, 'unavailable')


class TestCredentialsStatus(unittest.TestCase):
    """Tests for _credentials_status()."""

    def test_found(self):
        """Reports 'found' with path when credentials file exists."""
        with patch('usage_monitor_for_claude.verbose.Path') as mock_path, \
             patch.dict('os.environ', {}, clear=False):
            env = {k: v for k, v in __import__('os').environ.items() if k != 'CLAUDE_CONFIG_DIR'}
            with patch.dict('os.environ', env, clear=True):
                mock_home = MagicMock()
                mock_path.home.return_value = mock_home
                cred_path = mock_home / '.claude' / '.credentials.json'
                cred_path.exists.return_value = True
                result = _credentials_status()
        self.assertTrue(result.startswith('found'))

    def test_not_found(self):
        """Reports 'NOT FOUND' with path when credentials file is missing."""
        with patch('usage_monitor_for_claude.verbose.Path') as mock_path, \
             patch.dict('os.environ', {}, clear=False):
            env = {k: v for k, v in __import__('os').environ.items() if k != 'CLAUDE_CONFIG_DIR'}
            with patch.dict('os.environ', env, clear=True):
                mock_home = MagicMock()
                mock_path.home.return_value = mock_home
                cred_path = mock_home / '.claude' / '.credentials.json'
                cred_path.exists.return_value = False
                result = _credentials_status()
        self.assertTrue(result.startswith('NOT FOUND'))

    def test_custom_config_dir(self):
        """Respects CLAUDE_CONFIG_DIR environment variable."""
        with patch('usage_monitor_for_claude.verbose.Path') as mock_path, \
             patch.dict('os.environ', {'CLAUDE_CONFIG_DIR': 'D:\\custom'}):
            custom_path = MagicMock()
            mock_path.return_value = custom_path
            cred_path = custom_path / '.credentials.json'
            cred_path.exists.return_value = True
            result = _credentials_status()
        mock_path.assert_called_with('D:\\custom')
        self.assertTrue(result.startswith('found'))


class TestSetupConsole(unittest.TestCase):
    """Tests for setup_console()."""

    def test_attaches_parent_console_first(self):
        """Tries AttachConsole before AllocConsole."""
        with patch('usage_monitor_for_claude.verbose.ctypes') as mock_ctypes, \
             patch('builtins.open', MagicMock()), \
             patch('usage_monitor_for_claude.verbose.sys'), \
             patch('usage_monitor_for_claude.verbose.os'):
            mock_ctypes.windll.kernel32.AttachConsole.return_value = 1
            setup_console()
            mock_ctypes.windll.kernel32.AttachConsole.assert_called_once_with(-1)
            mock_ctypes.windll.kernel32.AllocConsole.assert_not_called()

    def test_allocates_console_on_attach_failure(self):
        """Falls back to AllocConsole when AttachConsole fails."""
        with patch('usage_monitor_for_claude.verbose.ctypes') as mock_ctypes, \
             patch('builtins.open', MagicMock()), \
             patch('usage_monitor_for_claude.verbose.sys'), \
             patch('usage_monitor_for_claude.verbose.os'):
            mock_ctypes.windll.kernel32.AttachConsole.return_value = 0
            setup_console()
            mock_ctypes.windll.kernel32.AllocConsole.assert_called_once()

    def test_sets_pywebview_log(self):
        """Sets PYWEBVIEW_LOG=DEBUG environment variable."""
        with patch('usage_monitor_for_claude.verbose.ctypes') as mock_ctypes, \
             patch('builtins.open', MagicMock()), \
             patch('usage_monitor_for_claude.verbose.sys'):
            mock_ctypes.windll.kernel32.AttachConsole.return_value = 1
            setup_console()
        self.assertEqual(__import__('os').environ.get('PYWEBVIEW_LOG'), 'DEBUG')


class TestPrintStartupDiagnostics(unittest.TestCase):
    """Tests for print_startup_diagnostics() output."""

    def test_contains_all_sections(self):
        """Output includes all expected section headers."""
        buf = io.StringIO()
        with patch('sys.stdout', buf), \
             patch('usage_monitor_for_claude.verbose.ctypes') as mock_ctypes, \
             patch('usage_monitor_for_claude.verbose.locale') as mock_locale, \
             patch('usage_monitor_for_claude.verbose.platform') as mock_platform, \
             patch('usage_monitor_for_claude.verbose.winreg') as mock_winreg:
            mock_platform.platform.return_value = 'Windows-11-10.0.26200-SP0'
            mock_platform.machine.return_value = 'AMD64'
            mock_ctypes.windll.shell32.IsUserAnAdmin.return_value = 0
            mock_ctypes.windll.user32.GetThreadDpiAwarenessContext.return_value = -4
            mock_ctypes.windll.user32.GetAwarenessFromDpiAwarenessContext.return_value = 2
            mock_ctypes.windll.user32.GetDpiForSystem.return_value = 96
            mock_ctypes.windll.user32.GetSystemMetrics.return_value = 1920
            mock_ctypes.windll.user32.SystemParametersInfoW.return_value = 1
            mock_ctypes.wintypes.RECT.return_value = MagicMock(left=0, top=0, right=1920, bottom=1040)
            mock_ctypes.byref = MagicMock()
            mock_locale.getlocale.return_value = ('en_US', 'cp1252')
            mock_winreg.OpenKey = MagicMock(side_effect=OSError)
            mock_winreg.HKEY_CURRENT_USER = 0x80000001
            mock_winreg.HKEY_LOCAL_MACHINE = 0x80000002

            print_startup_diagnostics()

        output = buf.getvalue()
        for section in ('System', 'Python', 'Locale', 'Display', 'Runtimes', 'Dependencies', 'Credentials'):
            with self.subTest(section=section):
                self.assertIn(section, output)

    def test_contains_version(self):
        """Output includes the app version."""
        buf = io.StringIO()
        with patch('sys.stdout', buf), \
             patch('usage_monitor_for_claude.verbose.ctypes') as mock_ctypes, \
             patch('usage_monitor_for_claude.verbose.locale') as mock_locale, \
             patch('usage_monitor_for_claude.verbose.platform') as mock_platform, \
             patch('usage_monitor_for_claude.verbose.winreg') as mock_winreg:
            mock_platform.platform.return_value = 'Windows-11'
            mock_platform.machine.return_value = 'AMD64'
            mock_ctypes.windll.shell32.IsUserAnAdmin.return_value = 0
            mock_ctypes.windll.user32.GetThreadDpiAwarenessContext.side_effect = Exception
            mock_ctypes.windll.user32.GetDpiForSystem.side_effect = Exception
            mock_ctypes.windll.user32.GetSystemMetrics.side_effect = Exception
            mock_ctypes.wintypes.RECT.side_effect = Exception
            mock_locale.getlocale.return_value = (None, None)
            mock_winreg.OpenKey = MagicMock(side_effect=OSError)
            mock_winreg.HKEY_CURRENT_USER = 0x80000001
            mock_winreg.HKEY_LOCAL_MACHINE = 0x80000002

            print_startup_diagnostics()

        from usage_monitor_for_claude import __version__
        self.assertIn(__version__, buf.getvalue())


class TestPrintRuntimeDiagnostics(unittest.TestCase):
    """Tests for print_runtime_diagnostics() output."""

    def test_contains_renderer_info(self):
        """Output includes webview renderer and GUI backend."""
        buf = io.StringIO()
        mock_webview = MagicMock()
        mock_webview.renderer = 'edgechromium'
        mock_webview.guilib.__name__ = 'webview.platforms.winforms'

        mock_pythonnet = MagicMock()
        mock_pythonnet.get_runtime_info.return_value = MagicMock(kind='.NET Framework', version='4.0', initialized=True)

        with patch('sys.stdout', buf), \
             patch.dict('sys.modules', {'webview': mock_webview, 'pythonnet': mock_pythonnet}):
            # System import needs to fail gracefully
            with patch.dict('sys.modules', {'System': None}):
                print_runtime_diagnostics()

        output = buf.getvalue()
        self.assertIn('edgechromium', output)
        self.assertIn('winforms', output)
        self.assertIn('.NET Framework', output)

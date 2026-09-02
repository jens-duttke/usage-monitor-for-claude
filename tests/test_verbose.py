"""
Verbose Diagnostics Tests
==========================

Unit tests for the --verbose diagnostic helpers.
"""
from __future__ import annotations

import io
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from usage_monitor_for_claude.verbose import (
    _credentials_status,
    _package_version,
    _redact_home,
    _row,
    _section,
    print_runtime_diagnostics,
    print_startup_diagnostics,
    setup_console,
)


class TestRedactHome(unittest.TestCase):
    """Tests for _redact_home() path sanitization."""

    def test_replaces_home_prefix(self):
        """Paths under the home directory are redacted with ~."""
        home = str(Path.home())
        self.assertEqual(
            _redact_home(f'{home}{os.sep}.claude{os.sep}.credentials.json'),
            f'~{os.sep}.claude{os.sep}.credentials.json',
        )

    def test_leaves_other_paths_unchanged(self):
        """Paths outside the home directory are not modified."""
        outside = os.sep.join(('', 'opt', 'PythonDev', 'app'))
        self.assertEqual(_redact_home(outside), outside)

    def test_empty_string(self):
        """Empty string is returned unchanged."""
        self.assertEqual(_redact_home(''), '')

    @unittest.skipUnless(sys.platform == 'win32', 'path casing only collapses on Windows')
    def test_case_insensitive_match(self):
        """Windows paths are case-insensitive - a differently-cased home prefix
        (e.g. CLAUDE_CONFIG_DIR set as c:\\users\\...) must still be redacted."""
        home = str(Path.home())
        self.assertEqual(_redact_home(f'{home.swapcase()}{os.sep}.claude{os.sep}file'), f'~{os.sep}.claude{os.sep}file')

    @unittest.skipIf(sys.platform == 'win32', 'POSIX paths are case-sensitive')
    def test_case_sensitive_on_posix(self):
        """A differently-cased prefix names a different directory on POSIX."""
        home = str(Path.home())
        path = f'{home.swapcase()}{os.sep}.claude'
        self.assertEqual(_redact_home(path), path)

    def test_prefix_boundary_not_partially_redacted(self):
        """A sibling profile whose name merely starts with the username
        (``/home/jens`` vs ``/home/jensen``) must not be partially redacted."""
        home = str(Path.home())
        sibling = f'{home}en{os.sep}file.txt'
        self.assertEqual(_redact_home(sibling), sibling)

    def test_exact_home_path_redacted(self):
        """The home directory itself is redacted to ~."""
        home = str(Path.home())
        self.assertEqual(_redact_home(home), '~')


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


class TestPrintStartupDiagnostics(unittest.TestCase):
    """Tests for print_startup_diagnostics() output.

    The platform probes are stubbed: this checks the report skeleton, which
    is what this module owns.  Each backend's rows are tested with it.
    """

    def _run(self) -> str:
        buf = io.StringIO()
        with patch('sys.stdout', buf), \
             patch('usage_monitor_for_claude.verbose.diagnostic_system_rows', return_value=[('OS', 'TestOS')]), \
             patch('usage_monitor_for_claude.verbose.diagnostic_display_rows', return_value=[('Monitors', '2')]), \
             patch('usage_monitor_for_claude.verbose.diagnostic_runtime_rows', return_value=[('Toolkit', '1.0')]), \
             patch('usage_monitor_for_claude.verbose.DIAGNOSTIC_PACKAGES', ('requests',)):
            print_startup_diagnostics()

        return buf.getvalue()

    def test_contains_all_sections(self):
        """Every section header is present."""
        output = self._run()
        for section in ('System', 'Python', 'Locale', 'Display', 'Runtimes', 'Dependencies', 'Credentials'):
            with self.subTest(section=section):
                self.assertIn(section, output)

    def test_contains_version(self):
        """Output includes the app version."""
        from usage_monitor_for_claude import __version__

        self.assertIn(__version__, self._run())

    def test_platform_rows_are_rendered(self):
        """Rows supplied by the platform backend reach the output."""
        output = self._run()
        self.assertIn('TestOS', output)
        self.assertIn('Toolkit', output)

    def test_home_directory_is_not_leaked(self):
        """The interpreter path is redacted so the username stays private."""
        home = str(Path.home())
        with patch.object(sys, 'executable', f'{home}{os.sep}venv{os.sep}python'):
            output = self._run()
        self.assertNotIn(home, output)
        self.assertIn(f'~{os.sep}venv', output)


class TestPrintRuntimeDiagnostics(unittest.TestCase):
    """Tests for print_runtime_diagnostics() output."""

    def test_contains_renderer_info(self):
        """Output includes webview renderer and GUI backend."""
        buf = io.StringIO()
        mock_webview = MagicMock()
        mock_webview.renderer = 'edgechromium'
        mock_webview.guilib.__name__ = 'webview.platforms.winforms'

        with patch('sys.stdout', buf), \
             patch.dict('sys.modules', {'webview': mock_webview}), \
             patch('usage_monitor_for_claude.verbose.diagnostic_post_init_rows', return_value=[]):
            print_runtime_diagnostics()

        output = buf.getvalue()
        self.assertIn('edgechromium', output)
        self.assertIn('winforms', output)

    def test_platform_post_init_rows_are_rendered(self):
        """Rows supplied by the platform backend reach the output."""
        buf = io.StringIO()
        mock_webview = MagicMock()
        mock_webview.guilib.__name__ = 'webview.platforms.gtk'

        with patch('sys.stdout', buf), \
             patch.dict('sys.modules', {'webview': mock_webview}), \
             patch('usage_monitor_for_claude.verbose.diagnostic_post_init_rows',
                   return_value=[('Toolkit runtime', '3.24.52')]):
            print_runtime_diagnostics()

        self.assertIn('3.24.52', buf.getvalue())

    def test_missing_backend_reported_as_unknown(self):
        """A webview without a resolved GUI backend still prints a row."""
        buf = io.StringIO()
        mock_webview = MagicMock()
        mock_webview.renderer = None
        mock_webview.guilib = None

        with patch('sys.stdout', buf), \
             patch.dict('sys.modules', {'webview': mock_webview}), \
             patch('usage_monitor_for_claude.verbose.diagnostic_post_init_rows', return_value=[]):
            print_runtime_diagnostics()

        self.assertIn('unknown', buf.getvalue())


if __name__ == '__main__':
    unittest.main()

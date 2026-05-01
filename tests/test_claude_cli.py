"""
Claude CLI Tests
==================

Unit tests for _discover_cli_path(), find_installations(), refresh_token(),
and cli_version().
"""
from __future__ import annotations

import os
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch

from usage_monitor_for_claude import claude_cli
from usage_monitor_for_claude.claude_cli import (
    ClaudeInstallation,
    RefreshResult,
    cli_version,
    find_installations,
    refresh_token,
)


# ---------------------------------------------------------------------------
# _discover_cli_path
# ---------------------------------------------------------------------------

class TestDiscoverCliPath(unittest.TestCase):
    """Tests for _discover_cli_path()."""

    @patch('usage_monitor_for_claude.claude_cli.shutil.which')
    def test_uses_which_when_found(self, mock_which):
        """shutil.which hit returns the discovered path directly."""
        with TemporaryDirectory() as tmp:
            fake = Path(tmp) / 'claude.cmd'
            fake.touch()
            mock_which.return_value = str(fake)
            self.assertEqual(claude_cli._discover_cli_path(), fake)

    @patch('usage_monitor_for_claude.claude_cli.shutil.which')
    def test_substitutes_cmd_for_ps1(self, mock_which):
        """When which returns a .ps1 shim, sibling .cmd is preferred."""
        with TemporaryDirectory() as tmp:
            ps1 = Path(tmp) / 'claude.ps1'
            cmd = Path(tmp) / 'claude.cmd'
            ps1.touch()
            cmd.touch()
            mock_which.return_value = str(ps1)
            self.assertEqual(claude_cli._discover_cli_path(), cmd)

    @patch('usage_monitor_for_claude.claude_cli.shutil.which')
    def test_substitutes_exe_for_ps1_when_no_cmd(self, mock_which):
        """When which returns .ps1 with no .cmd sibling, .exe is preferred."""
        with TemporaryDirectory() as tmp:
            ps1 = Path(tmp) / 'claude.ps1'
            exe = Path(tmp) / 'claude.exe'
            ps1.touch()
            exe.touch()
            mock_which.return_value = str(ps1)
            self.assertEqual(claude_cli._discover_cli_path(), exe)

    @patch('usage_monitor_for_claude.claude_cli.shutil.which')
    def test_returns_ps1_if_no_sibling_exists(self, mock_which):
        """Without a .cmd/.exe sibling, the .ps1 is returned (caller's is_file check handles it)."""
        with TemporaryDirectory() as tmp:
            ps1 = Path(tmp) / 'claude.ps1'
            ps1.touch()
            mock_which.return_value = str(ps1)
            self.assertEqual(claude_cli._discover_cli_path(), ps1)

    @patch('usage_monitor_for_claude.claude_cli.shutil.which', return_value=None)
    def test_falls_back_to_appdata_npm(self, _mock_which):
        """When which finds nothing, use the standard npm location."""
        with TemporaryDirectory() as tmp:
            npm_dir = Path(tmp) / 'npm'
            npm_dir.mkdir()
            cmd = npm_dir / 'claude.cmd'
            cmd.touch()
            with patch.dict(os.environ, {'APPDATA': str(tmp)}, clear=False):
                self.assertEqual(claude_cli._discover_cli_path(), cmd)

    @patch('usage_monitor_for_claude.claude_cli.shutil.which', return_value=None)
    def test_appdata_npm_prefers_cmd_over_exe(self, _mock_which):
        """When both .cmd and .exe exist in npm dir, .cmd is preferred (typical npm shim)."""
        with TemporaryDirectory() as tmp:
            npm_dir = Path(tmp) / 'npm'
            npm_dir.mkdir()
            cmd = npm_dir / 'claude.cmd'
            exe = npm_dir / 'claude.exe'
            cmd.touch()
            exe.touch()
            with patch.dict(os.environ, {'APPDATA': str(tmp)}, clear=False):
                self.assertEqual(claude_cli._discover_cli_path(), cmd)

    @patch('usage_monitor_for_claude.claude_cli.shutil.which', return_value=None)
    def test_last_resort_returns_default_when_nothing_found(self, _mock_which):
        """No CLI anywhere -> return the default path so callers fail gracefully."""
        with TemporaryDirectory() as tmp:
            home = Path(tmp)
            with patch('usage_monitor_for_claude.claude_cli.Path.home', return_value=home), \
                 patch.dict(os.environ, {'APPDATA': str(tmp)}, clear=False):
                result = claude_cli._discover_cli_path()
            self.assertEqual(result, home / '.local' / 'bin' / 'claude.exe')
            self.assertFalse(result.is_file())


# ---------------------------------------------------------------------------
# cli_version
# ---------------------------------------------------------------------------

class TestCliVersion(unittest.TestCase):
    """Tests for cli_version()."""

    def setUp(self):
        claude_cli._version_cache.clear()

    @patch('usage_monitor_for_claude.claude_cli.subprocess.run')
    @patch('pathlib.Path.stat', return_value=MagicMock(st_mtime=1000.0))
    def test_parses_version_string(self, _mock_stat, mock_run):
        """Extracts version from '2.1.69 (Claude Code)' output."""
        mock_run.return_value = MagicMock(stdout='2.1.69 (Claude Code)\n', returncode=0)
        self.assertEqual(cli_version(Path('/fake/claude.exe')), '2.1.69')

    @patch('usage_monitor_for_claude.claude_cli.subprocess.run')
    @patch('pathlib.Path.stat', return_value=MagicMock(st_mtime=1000.0))
    def test_version_only(self, _mock_stat, mock_run):
        """Handles bare version string without suffix."""
        mock_run.return_value = MagicMock(stdout='3.0.0\n', returncode=0)
        self.assertEqual(cli_version(Path('/fake/claude.exe')), '3.0.0')

    @patch('usage_monitor_for_claude.claude_cli.subprocess.run')
    @patch('pathlib.Path.stat', return_value=MagicMock(st_mtime=1000.0))
    def test_empty_output(self, _mock_stat, mock_run):
        """Returns empty string when output is empty."""
        mock_run.return_value = MagicMock(stdout='', returncode=0)
        self.assertEqual(cli_version(Path('/fake/claude.exe')), '')

    @patch('usage_monitor_for_claude.claude_cli.subprocess.run')
    @patch('pathlib.Path.stat', return_value=MagicMock(st_mtime=1000.0))
    def test_non_version_output(self, _mock_stat, mock_run):
        """Returns empty string for non-version output."""
        mock_run.return_value = MagicMock(stdout='error: something wrong', returncode=1)
        self.assertEqual(cli_version(Path('/fake/claude.exe')), '')

    @patch('usage_monitor_for_claude.claude_cli.subprocess.run')
    @patch('pathlib.Path.stat', return_value=MagicMock(st_mtime=1000.0))
    def test_timeout_returns_empty(self, _mock_stat, mock_run):
        """Returns empty string on timeout."""
        mock_run.side_effect = subprocess.TimeoutExpired(cmd='claude', timeout=10)
        self.assertEqual(cli_version(Path('/fake/claude.exe')), '')

    @patch('usage_monitor_for_claude.claude_cli.subprocess.run')
    @patch('pathlib.Path.stat', return_value=MagicMock(st_mtime=1000.0))
    def test_os_error_returns_empty(self, _mock_stat, mock_run):
        """Returns empty string on OSError (binary not found)."""
        mock_run.side_effect = OSError('not found')
        self.assertEqual(cli_version(Path('/fake/claude.exe')), '')

    @patch('usage_monitor_for_claude.claude_cli.subprocess.run')
    @patch('pathlib.Path.stat', return_value=MagicMock(st_mtime=1000.0))
    def test_passes_correct_args(self, _mock_stat, mock_run):
        """Calls subprocess with correct arguments."""
        mock_run.return_value = MagicMock(stdout='2.1.69\n', returncode=0)
        path = Path('/fake/claude.exe')
        cli_version(path)
        mock_run.assert_called_once_with(
            [str(path), '--version'],
            capture_output=True, text=True, timeout=10, creationflags=subprocess.CREATE_NO_WINDOW,
        )

    @patch('usage_monitor_for_claude.claude_cli.subprocess.run')
    @patch('pathlib.Path.stat', return_value=MagicMock(st_mtime=1000.0))
    def test_cache_hit_skips_subprocess(self, _mock_stat, mock_run):
        """Second call with same mtime returns cached version without subprocess."""
        mock_run.return_value = MagicMock(stdout='2.1.69\n', returncode=0)
        path = Path('/fake/claude.exe')
        self.assertEqual(cli_version(path), '2.1.69')
        self.assertEqual(cli_version(path), '2.1.69')
        mock_run.assert_called_once()

    @patch('usage_monitor_for_claude.claude_cli.subprocess.run')
    def test_cache_invalidated_on_mtime_change(self, mock_run):
        """Changed mtime triggers a new subprocess call."""
        mock_run.return_value = MagicMock(stdout='2.1.69\n', returncode=0)
        path = Path('/fake/claude.exe')
        with patch('pathlib.Path.stat', return_value=MagicMock(st_mtime=1000.0)):
            self.assertEqual(cli_version(path), '2.1.69')
        mock_run.return_value = MagicMock(stdout='3.0.0\n', returncode=0)
        with patch('pathlib.Path.stat', return_value=MagicMock(st_mtime=2000.0)):
            self.assertEqual(cli_version(path), '3.0.0')
        self.assertEqual(mock_run.call_count, 2)

    def test_stat_failure_returns_empty(self):
        """Returns empty string when stat() fails (file deleted)."""
        with patch('pathlib.Path.stat', side_effect=OSError('not found')):
            self.assertEqual(cli_version(Path('/fake/claude.exe')), '')


# ---------------------------------------------------------------------------
# find_installations
# ---------------------------------------------------------------------------

class TestFindInstallations(unittest.TestCase):
    """Tests for find_installations()."""

    @patch('usage_monitor_for_claude.claude_cli.cli_version', return_value='')
    @patch('usage_monitor_for_claude.claude_cli.CLAUDE_CLI_PATH')
    @patch('usage_monitor_for_claude.claude_cli._EXTENSION_DIRS', [])
    def test_no_installations_found(self, mock_cli_path, _mock_version):
        """Returns empty list when nothing is installed."""
        mock_cli_path.is_file.return_value = False
        result = find_installations()
        self.assertEqual(result, [])

    @patch('usage_monitor_for_claude.claude_cli.cli_version', return_value='2.1.69')
    @patch('usage_monitor_for_claude.claude_cli.CLAUDE_CLI_PATH')
    @patch('usage_monitor_for_claude.claude_cli._EXTENSION_DIRS', [])
    def test_cli_only(self, mock_cli_path, _mock_version):
        """Returns CLI installation when binary exists."""
        mock_cli_path.is_file.return_value = True
        result = find_installations()
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].name, 'CLI')
        self.assertEqual(result[0].version, '2.1.69')

    @patch('usage_monitor_for_claude.claude_cli.cli_version', return_value='')
    @patch('usage_monitor_for_claude.claude_cli.CLAUDE_CLI_PATH')
    @patch('usage_monitor_for_claude.claude_cli._EXTENSION_DIRS', [])
    def test_cli_exists_but_version_fails(self, mock_cli_path, _mock_version):
        """CLI binary exists but version command fails - not included."""
        mock_cli_path.is_file.return_value = True
        result = find_installations()
        self.assertEqual(result, [])

    @patch('usage_monitor_for_claude.claude_cli.cli_version', return_value='')
    @patch('usage_monitor_for_claude.claude_cli.CLAUDE_CLI_PATH')
    def test_vscode_extension(self, mock_cli_path, _mock_version):
        """Finds VS Code extension and extracts version from directory name."""
        mock_cli_path.is_file.return_value = False
        with TemporaryDirectory() as tmp:
            ext_dir = Path(tmp)
            (ext_dir / 'anthropic.claude-code-2.1.69-win32-x64').mkdir()
            with patch('usage_monitor_for_claude.claude_cli._EXTENSION_DIRS', [('VS Code', ext_dir)]):
                result = find_installations()
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].name, 'VS Code')
        self.assertEqual(result[0].version, '2.1.69')

    @patch('usage_monitor_for_claude.claude_cli.cli_version', return_value='')
    @patch('usage_monitor_for_claude.claude_cli.CLAUDE_CLI_PATH')
    def test_picks_highest_version(self, mock_cli_path, _mock_version):
        """When multiple extension versions exist, picks the highest."""
        mock_cli_path.is_file.return_value = False
        with TemporaryDirectory() as tmp:
            ext_dir = Path(tmp)
            (ext_dir / 'anthropic.claude-code-2.1.63-win32-x64').mkdir()
            (ext_dir / 'anthropic.claude-code-2.1.69-win32-x64').mkdir()
            (ext_dir / 'anthropic.claude-code-2.1.66-win32-x64').mkdir()
            with patch('usage_monitor_for_claude.claude_cli._EXTENSION_DIRS', [('VS Code', ext_dir)]):
                result = find_installations()
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].version, '2.1.69')

    @patch('usage_monitor_for_claude.claude_cli.cli_version', return_value='')
    @patch('usage_monitor_for_claude.claude_cli.CLAUDE_CLI_PATH')
    def test_ignores_non_claude_extensions(self, mock_cli_path, _mock_version):
        """Ignores directories that don't match the Claude extension prefix."""
        mock_cli_path.is_file.return_value = False
        with TemporaryDirectory() as tmp:
            ext_dir = Path(tmp)
            (ext_dir / 'some-other-extension-1.0.0').mkdir()
            (ext_dir / 'anthropic.claude-code-2.1.69-win32-x64').mkdir()
            with patch('usage_monitor_for_claude.claude_cli._EXTENSION_DIRS', [('VS Code', ext_dir)]):
                result = find_installations()
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].version, '2.1.69')

    @patch('usage_monitor_for_claude.claude_cli.cli_version', return_value='')
    @patch('usage_monitor_for_claude.claude_cli.CLAUDE_CLI_PATH')
    def test_nonexistent_extension_dir_skipped(self, mock_cli_path, _mock_version):
        """Extension directories that don't exist are silently skipped."""
        mock_cli_path.is_file.return_value = False
        with patch('usage_monitor_for_claude.claude_cli._EXTENSION_DIRS', [('VS Code', Path('/nonexistent'))]):
            result = find_installations()
        self.assertEqual(result, [])

    @patch('usage_monitor_for_claude.claude_cli.cli_version', return_value='2.1.69')
    @patch('usage_monitor_for_claude.claude_cli.CLAUDE_CLI_PATH')
    def test_cli_and_extensions_combined(self, mock_cli_path, _mock_version):
        """Returns both CLI and extension installations."""
        mock_cli_path.is_file.return_value = True
        with TemporaryDirectory() as tmp:
            ext_dir = Path(tmp)
            (ext_dir / 'anthropic.claude-code-2.1.68-win32-x64').mkdir()
            with patch('usage_monitor_for_claude.claude_cli._EXTENSION_DIRS', [('VS Code', ext_dir)]):
                result = find_installations()
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0].name, 'CLI')
        self.assertEqual(result[1].name, 'VS Code')


# ---------------------------------------------------------------------------
# refresh_token
# ---------------------------------------------------------------------------

class TestRefreshToken(unittest.TestCase):
    """Tests for refresh_token()."""

    @patch('usage_monitor_for_claude.claude_cli.CLAUDE_CLI_PATH')
    def test_cli_not_found(self, mock_path):
        """Returns error when CLI binary doesn't exist."""
        mock_path.is_file.return_value = False
        result = refresh_token()
        self.assertFalse(result.success)
        self.assertEqual(result.error, 'CLI not found')

    @patch('usage_monitor_for_claude.claude_cli.subprocess.run')
    @patch('usage_monitor_for_claude.claude_cli.CLAUDE_CLI_PATH')
    def test_up_to_date(self, mock_path, mock_run):
        """Parses 'up to date' output correctly."""
        mock_path.is_file.return_value = True
        mock_run.return_value = MagicMock(
            stdout='Current version: 2.1.69\nChecking for updates to latest version...\nClaude Code is up to date (2.1.69)\n',
            stderr='', returncode=0,
        )
        result = refresh_token()
        self.assertTrue(result.success)
        self.assertFalse(result.updated)
        self.assertEqual(result.old_version, '2.1.69')
        self.assertEqual(result.new_version, '2.1.69')

    @patch('usage_monitor_for_claude.claude_cli.subprocess.run')
    @patch('usage_monitor_for_claude.claude_cli.CLAUDE_CLI_PATH')
    def test_successfully_updated(self, mock_path, mock_run):
        """Parses 'Successfully updated' output correctly."""
        mock_path.is_file.return_value = True
        mock_run.return_value = MagicMock(
            stdout='Current version: 2.1.38\nChecking for updates to latest version...\nSuccessfully updated from 2.1.38 to version 2.1.69\n',
            stderr='', returncode=0,
        )
        result = refresh_token()
        self.assertTrue(result.success)
        self.assertTrue(result.updated)
        self.assertEqual(result.old_version, '2.1.38')
        self.assertEqual(result.new_version, '2.1.69')

    @patch('usage_monitor_for_claude.claude_cli.subprocess.run')
    @patch('usage_monitor_for_claude.claude_cli.CLAUDE_CLI_PATH')
    def test_timeout(self, mock_path, mock_run):
        """Returns error on timeout."""
        mock_path.is_file.return_value = True
        mock_run.side_effect = subprocess.TimeoutExpired(cmd='claude', timeout=60)
        result = refresh_token()
        self.assertFalse(result.success)
        self.assertEqual(result.error, 'Timeout')

    @patch('usage_monitor_for_claude.claude_cli.subprocess.run')
    @patch('usage_monitor_for_claude.claude_cli.CLAUDE_CLI_PATH')
    def test_os_error(self, mock_path, mock_run):
        """Returns error on OSError."""
        mock_path.is_file.return_value = True
        mock_run.side_effect = OSError('Permission denied')
        result = refresh_token()
        self.assertFalse(result.success)
        self.assertEqual(result.error, 'Permission denied')

    @patch('usage_monitor_for_claude.claude_cli.subprocess.run')
    @patch('usage_monitor_for_claude.claude_cli.CLAUDE_CLI_PATH')
    def test_unexpected_output_success(self, mock_path, mock_run):
        """Unexpected output with returncode 0 still counts as success."""
        mock_path.is_file.return_value = True
        mock_run.return_value = MagicMock(stdout='Something unexpected', stderr='', returncode=0)
        result = refresh_token()
        self.assertTrue(result.success)
        self.assertFalse(result.updated)

    @patch('usage_monitor_for_claude.claude_cli.subprocess.run')
    @patch('usage_monitor_for_claude.claude_cli.CLAUDE_CLI_PATH')
    def test_unexpected_output_failure(self, mock_path, mock_run):
        """Unexpected output with non-zero returncode is a failure."""
        mock_path.is_file.return_value = True
        mock_run.return_value = MagicMock(stdout='Error: something', stderr='', returncode=1)
        result = refresh_token()
        self.assertFalse(result.success)
        self.assertIn('Error: something', result.error)

    @patch('usage_monitor_for_claude.claude_cli.subprocess.run')
    @patch('usage_monitor_for_claude.claude_cli.CLAUDE_CLI_PATH')
    def test_error_message_truncated(self, mock_path, mock_run):
        """Long error output is truncated to 200 characters."""
        mock_path.is_file.return_value = True
        mock_run.return_value = MagicMock(stdout='X' * 300, stderr='', returncode=1)
        result = refresh_token()
        self.assertEqual(len(result.error), 200)

    @patch('usage_monitor_for_claude.claude_cli.subprocess.run')
    @patch('usage_monitor_for_claude.claude_cli.CLAUDE_CLI_PATH')
    def test_update_without_version_prefix(self, mock_path, mock_run):
        """Parses update output without 'version' prefix before new version."""
        mock_path.is_file.return_value = True
        mock_run.return_value = MagicMock(
            stdout='updated from 2.1.38 to 2.1.69', stderr='', returncode=0,
        )
        result = refresh_token()
        self.assertTrue(result.updated)
        self.assertEqual(result.old_version, '2.1.38')
        self.assertEqual(result.new_version, '2.1.69')

    @patch('usage_monitor_for_claude.claude_cli.subprocess.run')
    @patch('usage_monitor_for_claude.claude_cli.CLAUDE_CLI_PATH')
    def test_stderr_included_in_parsing(self, mock_path, mock_run):
        """Output from stderr is also considered when parsing."""
        mock_path.is_file.return_value = True
        mock_run.return_value = MagicMock(
            stdout='', stderr='Claude Code is up to date (2.1.69)', returncode=0,
        )
        result = refresh_token()
        self.assertTrue(result.success)
        self.assertEqual(result.new_version, '2.1.69')


if __name__ == '__main__':
    unittest.main()

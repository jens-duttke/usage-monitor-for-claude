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
            capture_output=True, text=True, encoding='utf-8', errors='replace',
            timeout=10, creationflags=subprocess.CREATE_NO_WINDOW,
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

    @patch('usage_monitor_for_claude.claude_cli.cli_version', return_value='')
    @patch('usage_monitor_for_claude.claude_cli.CLAUDE_CLI_PATH')
    def test_unreadable_extension_dir_skipped(self, mock_cli_path, _mock_version):
        """A directory that exists but cannot be enumerated (ACL denial, broken
        junction) is skipped instead of crashing the popup threads."""
        mock_cli_path.is_file.return_value = False

        denied_dir = MagicMock()
        denied_dir.is_dir.return_value = True
        denied_dir.iterdir.side_effect = PermissionError(13, 'Access is denied')

        with TemporaryDirectory() as tmp:
            ext_dir = Path(tmp)
            (ext_dir / 'anthropic.claude-code-2.1.69-win32-x64').mkdir()
            with patch('usage_monitor_for_claude.claude_cli._EXTENSION_DIRS', [('VS Code', denied_dir), ('Cursor', ext_dir)]):
                result = find_installations()

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].name, 'Cursor')

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


# ---------------------------------------------------------------------------
# cli_command (custom / WSL CLI)
# ---------------------------------------------------------------------------

class TestCommandVersion(unittest.TestCase):
    """Tests for _command_version()."""

    def setUp(self):
        claude_cli._command_version_cache.clear()

    @patch('usage_monitor_for_claude.claude_cli.subprocess.run')
    def test_parses_version(self, mock_run):
        """Extracts the version from a custom command's --version output."""
        mock_run.return_value = MagicMock(stdout='2.1.204 (Claude Code)\n', returncode=0)
        self.assertEqual(claude_cli._command_version(['wsl', 'claude']), '2.1.204')

    @patch('usage_monitor_for_claude.claude_cli.subprocess.run')
    def test_appends_version_flag(self, mock_run):
        """Runs the configured command with --version appended."""
        mock_run.return_value = MagicMock(stdout='2.1.204\n', returncode=0)
        claude_cli._command_version(['wsl', '/home/user/.local/bin/claude'])
        mock_run.assert_called_once_with(
            ['wsl', '/home/user/.local/bin/claude', '--version'],
            capture_output=True, text=True, encoding='utf-8', errors='replace',
            timeout=10, creationflags=subprocess.CREATE_NO_WINDOW,
        )

    @patch('usage_monitor_for_claude.claude_cli.subprocess.run')
    def test_cache_hit_skips_subprocess(self, mock_run):
        """A second call with the same command returns the cached version."""
        mock_run.return_value = MagicMock(stdout='2.1.204\n', returncode=0)
        self.assertEqual(claude_cli._command_version(['wsl', 'claude']), '2.1.204')
        self.assertEqual(claude_cli._command_version(['wsl', 'claude']), '2.1.204')
        mock_run.assert_called_once()

    @patch('usage_monitor_for_claude.claude_cli.subprocess.run')
    def test_exception_returns_empty_uncached(self, mock_run):
        """A failing command returns '' and is not cached, so the next call retries."""
        mock_run.side_effect = OSError('wsl not found')
        self.assertEqual(claude_cli._command_version(['wsl', 'claude']), '')
        self.assertNotIn(('wsl', 'claude'), claude_cli._command_version_cache)

    @patch('usage_monitor_for_claude.claude_cli.subprocess.run')
    def test_timeout_returns_empty_uncached(self, mock_run):
        """A timeout (e.g. a cold WSL boot) is not cached, so the next call retries."""
        mock_run.side_effect = subprocess.TimeoutExpired(cmd='wsl', timeout=10)
        self.assertEqual(claude_cli._command_version(['wsl', 'claude']), '')
        self.assertNotIn(('wsl', 'claude'), claude_cli._command_version_cache)

    @patch('usage_monitor_for_claude.claude_cli.subprocess.run')
    def test_unparsable_output_cached(self, mock_run):
        """A command that runs but reports no version caches '' - re-spawning it on
        every poll would keep paying the WSL start cost for a known-bad command."""
        mock_run.return_value = MagicMock(stdout='command not found', returncode=1)
        self.assertEqual(claude_cli._command_version(['wsl', 'claude']), '')
        self.assertEqual(claude_cli._command_version(['wsl', 'claude']), '')
        mock_run.assert_called_once()

    @patch('usage_monitor_for_claude.claude_cli.subprocess.run')
    def test_distinct_commands_cached_separately(self, mock_run):
        """Each command gets its own cache entry - one entry must not mask another."""
        mock_run.return_value = MagicMock(stdout='2.1.204\n', returncode=0)
        self.assertEqual(claude_cli._command_version(['wsl', 'claude']), '2.1.204')
        mock_run.return_value = MagicMock(stdout='2.1.99\n', returncode=0)
        self.assertEqual(claude_cli._command_version(['wsl', '-d', 'Ubuntu', 'claude']), '2.1.99')
        self.assertEqual(mock_run.call_count, 2)


class TestFindInstallationsCliCommand(unittest.TestCase):
    """Tests for find_installations() with a configured cli_command."""

    @patch('usage_monitor_for_claude.claude_cli._command_version', return_value='2.1.204')
    @patch('usage_monitor_for_claude.claude_cli.cli_version', return_value='')
    @patch('usage_monitor_for_claude.claude_cli.CLAUDE_CLI_PATH')
    @patch('usage_monitor_for_claude.claude_cli.CLI_COMMAND', {'WSL': ['wsl', '/home/user/.local/bin/claude']})
    @patch('usage_monitor_for_claude.claude_cli._EXTENSION_DIRS', [])
    def test_custom_command_listed(self, mock_cli_path, _mock_cli_version, _mock_cmd_version):
        """A configured cli_command appears as an installation under its name."""
        mock_cli_path.is_file.return_value = False
        result = find_installations()
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].name, 'WSL')
        self.assertEqual(result[0].version, '2.1.204')

    @patch('usage_monitor_for_claude.claude_cli._command_version', return_value='2.1.204')
    @patch('usage_monitor_for_claude.claude_cli.cli_version', return_value='2.1.177')
    @patch('usage_monitor_for_claude.claude_cli.CLAUDE_CLI_PATH')
    @patch('usage_monitor_for_claude.claude_cli.CLI_COMMAND', {'WSL': ['wsl', 'claude']})
    @patch('usage_monitor_for_claude.claude_cli._EXTENSION_DIRS', [])
    def test_listed_in_addition_to_native(self, mock_cli_path, _mock_cli_version, _mock_cmd_version):
        """A cli_command is listed in addition to the native CLI, which stays visible
        because it is the install the app authenticates and refreshes with."""
        mock_cli_path.is_file.return_value = True
        result = find_installations()
        self.assertEqual([(i.name, i.version) for i in result], [('CLI', '2.1.177'), ('WSL', '2.1.204')])

    @patch('usage_monitor_for_claude.claude_cli._command_version', return_value='')
    @patch('usage_monitor_for_claude.claude_cli.cli_version', return_value='2.1.177')
    @patch('usage_monitor_for_claude.claude_cli.CLAUDE_CLI_PATH')
    @patch('usage_monitor_for_claude.claude_cli.CLI_COMMAND', {'WSL': ['wsl', 'claude']})
    @patch('usage_monitor_for_claude.claude_cli._EXTENSION_DIRS', [])
    def test_custom_command_version_fails_native_kept(self, mock_cli_path, _mock_cli_version, _mock_cmd_version):
        """A cli_command whose version cannot be read is skipped without hiding the native CLI."""
        mock_cli_path.is_file.return_value = True
        result = find_installations()
        self.assertEqual([i.name for i in result], ['CLI'])

    @patch('usage_monitor_for_claude.claude_cli._command_version', return_value='2.1.204')
    @patch('usage_monitor_for_claude.claude_cli.cli_version', return_value='')
    @patch('usage_monitor_for_claude.claude_cli.CLAUDE_CLI_PATH')
    @patch('usage_monitor_for_claude.claude_cli.CLI_COMMAND', {'WSL': ['wsl', 'claude'], 'WSL Ubuntu': ['wsl', '-d', 'Ubuntu', 'claude']})
    @patch('usage_monitor_for_claude.claude_cli._EXTENSION_DIRS', [])
    def test_multiple_commands_all_listed(self, mock_cli_path, _mock_cli_version, _mock_cmd_version):
        """Every configured cli_command entry is listed under its own name."""
        mock_cli_path.is_file.return_value = False
        result = find_installations()
        self.assertEqual([i.name for i in result], ['WSL', 'WSL Ubuntu'])

    @patch('usage_monitor_for_claude.claude_cli._command_version', return_value='2.1.204')
    @patch('usage_monitor_for_claude.claude_cli.cli_version', return_value='2.1.177')
    @patch('usage_monitor_for_claude.claude_cli.CLAUDE_CLI_PATH')
    @patch('usage_monitor_for_claude.claude_cli.CLI_COMMAND', {'WSL': ['wsl', 'claude']})
    def test_native_command_and_extensions_all_listed(self, mock_cli_path, _mock_cli_version, _mock_cmd_version):
        """All three sources appear together, native CLI first, then the configured
        command, then IDE extensions."""
        mock_cli_path.is_file.return_value = True
        with TemporaryDirectory() as tmp:
            ext_dir = Path(tmp)
            (ext_dir / 'anthropic.claude-code-2.1.68-win32-x64').mkdir()
            with patch('usage_monitor_for_claude.claude_cli._EXTENSION_DIRS', [('VS Code', ext_dir)]):
                result = find_installations()
        self.assertEqual([i.name for i in result], ['CLI', 'WSL', 'VS Code'])


class TestRefreshTokenIgnoresCliCommand(unittest.TestCase):
    """Tests that refresh_token() never runs a configured cli_command.

    The refresh only works as a side effect: the CLI renews the expired token
    in the credentials file this app reads.  A CLI behind cli_command (e.g. a
    WSL install) keeps its own credentials inside WSL, so refreshing through it
    would leave that file untouched and could never renew the token.
    """

    def setUp(self):
        claude_cli._command_version_cache.clear()

    @patch('usage_monitor_for_claude.claude_cli.subprocess.run')
    @patch('usage_monitor_for_claude.claude_cli.CLAUDE_CLI_PATH')
    @patch('usage_monitor_for_claude.claude_cli.CLI_COMMAND', {'WSL': ['wsl', '/home/user/.local/bin/claude']})
    def test_uses_native_binary_despite_cli_command(self, mock_path, mock_run):
        """A configured cli_command must not divert the token refresh to WSL."""
        mock_path.is_file.return_value = True
        mock_path.__str__.return_value = r'C:\npm\claude.cmd'
        mock_run.return_value = MagicMock(stdout='Claude Code is up to date (2.1.177)', stderr='', returncode=0)
        result = refresh_token()
        self.assertTrue(result.success)
        self.assertEqual(mock_run.call_args[0][0], [r'C:\npm\claude.cmd', 'update'])

    @patch('usage_monitor_for_claude.claude_cli.subprocess.run')
    @patch('usage_monitor_for_claude.claude_cli.CLAUDE_CLI_PATH')
    @patch('usage_monitor_for_claude.claude_cli.CLI_COMMAND', {'WSL': ['wsl', 'claude']})
    def test_no_native_binary_reports_not_found(self, mock_path, mock_run):
        """Without a native binary the refresh reports 'CLI not found' rather than
        falling back to the cli_command, which owns different credentials."""
        mock_path.is_file.return_value = False
        result = refresh_token()
        self.assertFalse(result.success)
        self.assertEqual(result.error, 'CLI not found')
        mock_run.assert_not_called()

    @patch('usage_monitor_for_claude.claude_cli.subprocess.run')
    @patch('usage_monitor_for_claude.claude_cli.CLAUDE_CLI_PATH')
    @patch('usage_monitor_for_claude.claude_cli.CLI_COMMAND', {'WSL': ['wsl', 'claude']})
    def test_native_update_leaves_command_version_cache(self, mock_path, mock_run):
        """A native update must not touch the custom command's cached version - the
        two installs update independently."""
        mock_path.is_file.return_value = True
        claude_cli._command_version_cache[('wsl', 'claude')] = '2.1.204'
        mock_run.return_value = MagicMock(
            stdout='Successfully updated from 2.1.177 to version 2.1.178', stderr='', returncode=0,
        )
        refresh_token()
        self.assertEqual(claude_cli._command_version_cache[('wsl', 'claude')], '2.1.204')


# ---------------------------------------------------------------------------
# refresh_token() on non-UTF-8 locales (regression: #80)
# ---------------------------------------------------------------------------

class TestRefreshTokenNonUtf8Locale(unittest.TestCase):
    """Regression tests for the cp950/non-UTF-8 locale crash (issue #80).

    ``claude update`` emits UTF-8.  When ``subprocess.run`` decodes its
    pipes with the system locale codec (e.g. cp950), a non-ASCII glyph
    kills the pipe-reader thread; CPython then hands back ``None`` for that
    stream, and ``proc.stdout + proc.stderr`` raised ``TypeError``.
    """

    @patch('usage_monitor_for_claude.claude_cli.subprocess.run')
    @patch('usage_monitor_for_claude.claude_cli.CLAUDE_CLI_PATH')
    def test_decodes_output_as_utf8_regardless_of_locale(self, mock_path, mock_run):
        """The CLI output is decoded as UTF-8, not the ambient locale codec."""
        mock_path.is_file.return_value = True
        mock_run.return_value = MagicMock(stdout='', stderr='', returncode=0)
        refresh_token()
        _args, kwargs = mock_run.call_args
        self.assertEqual(kwargs.get('encoding'), 'utf-8')
        self.assertEqual(kwargs.get('errors'), 'replace')

    @patch('usage_monitor_for_claude.claude_cli.subprocess.run')
    @patch('usage_monitor_for_claude.claude_cli.CLAUDE_CLI_PATH')
    def test_survives_none_stderr_from_dead_pipe_thread(self, mock_path, mock_run):
        """A ``None`` stderr (dead decode thread) must not raise TypeError.

        This is the exact reported traceback: the update succeeded, stdout
        carried the success line, but the stderr pipe-reader thread had died
        and CPython returned ``None`` for it.
        """
        mock_path.is_file.return_value = True
        mock_run.return_value = MagicMock(
            stdout='Successfully updated from 2.1.38 to version 2.1.69',
            stderr=None, returncode=0,
        )
        result = refresh_token()  # must not raise "can only concatenate str ... NoneType"
        self.assertTrue(result.success)
        self.assertTrue(result.updated)
        self.assertEqual(result.old_version, '2.1.38')
        self.assertEqual(result.new_version, '2.1.69')

    @patch('usage_monitor_for_claude.claude_cli.subprocess.run')
    @patch('usage_monitor_for_claude.claude_cli.CLAUDE_CLI_PATH')
    def test_survives_both_streams_none(self, mock_path, mock_run):
        """Both streams ``None`` (returncode 0) still resolves without crashing."""
        mock_path.is_file.return_value = True
        mock_run.return_value = MagicMock(stdout=None, stderr=None, returncode=0)
        result = refresh_token()
        self.assertTrue(result.success)
        self.assertFalse(result.updated)


# ---------------------------------------------------------------------------
# cli_version() / _command_version() on non-UTF-8 locales (regression: #80)
# ---------------------------------------------------------------------------

class TestVersionProbesNonUtf8Locale(unittest.TestCase):
    """Same-root-cause regression as #80 for the two ``--version`` probes.

    ``claude --version`` emits UTF-8.  When ``subprocess.run`` decodes its
    pipes with the ambient locale codec (e.g. cp950), a non-ASCII glyph kills
    the pipe-reader thread and CPython hands back ``None`` for that stream.

    ``cli_version`` swallows the resulting ``AttributeError`` in its broad
    ``except`` (so it silently loses the version), but ``_command_version``
    parses ``proc.stdout`` *outside* its ``try`` and ``find_installations``
    calls it unguarded, so a ``None`` stdout used to raise
    ``AttributeError: 'NoneType' object has no attribute 'strip'`` straight
    into the popup's update path.
    """

    def setUp(self):
        claude_cli._version_cache.clear()
        claude_cli._command_version_cache.clear()

    @patch('usage_monitor_for_claude.claude_cli.subprocess.run')
    @patch('pathlib.Path.stat', return_value=MagicMock(st_mtime=1000.0))
    def test_cli_version_decodes_output_as_utf8(self, _mock_stat, mock_run):
        """cli_version decodes --version output as UTF-8, not the locale codec."""
        mock_run.return_value = MagicMock(stdout='2.1.69 (Claude Code)\n', returncode=0)
        cli_version(Path('/fake/claude.exe'))
        _args, kwargs = mock_run.call_args
        self.assertEqual(kwargs.get('encoding'), 'utf-8')
        self.assertEqual(kwargs.get('errors'), 'replace')

    @patch('usage_monitor_for_claude.claude_cli.subprocess.run')
    def test_command_version_decodes_output_as_utf8(self, mock_run):
        """_command_version decodes --version output as UTF-8, not the locale codec."""
        mock_run.return_value = MagicMock(stdout='2.1.204 (Claude Code)\n', returncode=0)
        claude_cli._command_version(['wsl', 'claude'])
        _args, kwargs = mock_run.call_args
        self.assertEqual(kwargs.get('encoding'), 'utf-8')
        self.assertEqual(kwargs.get('errors'), 'replace')

    @patch('usage_monitor_for_claude.claude_cli.subprocess.run')
    def test_command_version_survives_none_stdout(self, mock_run):
        """A None stdout (dead decode thread) returns '' instead of raising.

        This is the exact reported traceback for the custom-command path:
        ``_parse_version(proc.stdout)`` sits outside the ``try``, so a None
        stdout raised ``AttributeError`` before this fix.
        """
        mock_run.return_value = MagicMock(stdout=None, stderr=None, returncode=0)
        self.assertEqual(claude_cli._command_version(['wsl', 'claude']), '')

    @patch('usage_monitor_for_claude.claude_cli.subprocess.run')
    @patch('pathlib.Path.stat', return_value=MagicMock(st_mtime=1000.0))
    def test_cli_version_survives_none_stdout(self, _mock_stat, mock_run):
        """cli_version returns '' (never raises) on a None stdout."""
        mock_run.return_value = MagicMock(stdout=None, returncode=0)
        self.assertEqual(cli_version(Path('/fake/claude.exe')), '')

    @patch('usage_monitor_for_claude.claude_cli.subprocess.run')
    @patch('usage_monitor_for_claude.claude_cli.CLI_COMMAND', {'WSL': ['wsl', 'claude']})
    @patch('usage_monitor_for_claude.claude_cli._EXTENSION_DIRS', [])
    @patch('usage_monitor_for_claude.claude_cli.CLAUDE_CLI_PATH')
    def test_find_installations_survives_none_command_stdout(self, mock_path, mock_run):
        """A configured cli_command whose --version stdout is None must not
        crash find_installations() - the popup's update path (regression #80)."""
        mock_path.is_file.return_value = False
        mock_run.return_value = MagicMock(stdout=None, stderr=None, returncode=0)
        result = find_installations()  # must not raise AttributeError
        self.assertEqual(result, [])


if __name__ == '__main__':
    unittest.main()

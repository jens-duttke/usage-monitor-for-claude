"""
Claude CLI Tests
==================

Unit tests for _discover_cli_path(), find_installations(), refresh_token(),
cli_version(), and _run_cli().
"""
from __future__ import annotations

import os
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from usage_monitor_for_claude.platforms import no_window_kwargs
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
            timeout=10, **no_window_kwargs(),
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

    @patch('usage_monitor_for_claude.claude_cli.subprocess.run')
    @patch('pathlib.Path.stat', return_value=MagicMock(st_mtime=1000.0))
    def test_lost_output_returns_empty_uncached(self, _mock_stat, mock_run):
        """A lost stream returns '' and is not cached, so the next call retries."""
        mock_run.return_value = MagicMock(stdout=None, stderr=None, returncode=0)
        path = Path('/fake/claude.exe')
        self.assertEqual(cli_version(path), '')
        self.assertNotIn(path, claude_cli._version_cache)

    @patch('usage_monitor_for_claude.claude_cli.subprocess.run')
    @patch('pathlib.Path.stat', return_value=MagicMock(st_mtime=1000.0))
    def test_recovers_after_lost_output(self, _mock_stat, mock_run):
        """A lost stream must not pin an empty version until the binary changes."""
        path = Path('/fake/claude.exe')
        mock_run.return_value = MagicMock(stdout=None, stderr=None, returncode=0)
        self.assertEqual(cli_version(path), '')
        mock_run.return_value = MagicMock(stdout='2.1.69 (Claude Code)\n', stderr='', returncode=0)
        self.assertEqual(cli_version(path), '2.1.69')


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

    @patch('usage_monitor_for_claude.claude_cli.subprocess.run')
    @patch('usage_monitor_for_claude.claude_cli.CLAUDE_CLI_PATH')
    def test_allows_a_full_minute_for_the_update(self, mock_path, mock_run):
        """The update downloads from the npm registry, so it gets far longer than a
        version probe - dropping it to the probes' 10s would fail slow connections."""
        mock_path.is_file.return_value = True
        mock_run.return_value = MagicMock(
            stdout='Claude Code is up to date (2.1.69)', stderr='', returncode=0,
        )
        refresh_token()
        self.assertEqual(mock_run.call_args.kwargs['timeout'], 60)

    @patch('usage_monitor_for_claude.claude_cli.subprocess.run')
    @patch('usage_monitor_for_claude.claude_cli.CLAUDE_CLI_PATH')
    def test_lost_output_reports_error(self, mock_path, mock_run):
        """A lost stream is reported as an error instead of killing the poll thread.

        refresh_token() combines both streams to find the update message, so a
        stream reported as None turns into a TypeError in the poll thread that
        called it - the crash reported in issue #80.
        """
        mock_path.is_file.return_value = True
        mock_run.return_value = MagicMock(
            stdout='Successfully updated from 2.1.38 to version 2.1.69', stderr=None, returncode=0,
        )
        result = refresh_token()
        self.assertFalse(result.success)
        self.assertEqual(result.error, 'CLI output could not be captured')


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
            timeout=10, **no_window_kwargs(),
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
    def test_lost_output_returns_empty_uncached(self, mock_run):
        """A lost stream returns '' and is not cached, so the next call retries."""
        mock_run.return_value = MagicMock(stdout=None, stderr=None, returncode=0)
        self.assertEqual(claude_cli._command_version(['wsl', 'claude']), '')
        self.assertNotIn(('wsl', 'claude'), claude_cli._command_version_cache)

    @patch('usage_monitor_for_claude.claude_cli.subprocess.run')
    def test_recovers_after_lost_output(self, mock_run):
        """A lost stream must not pin an empty version for the process lifetime."""
        mock_run.return_value = MagicMock(stdout=None, stderr=None, returncode=0)
        self.assertEqual(claude_cli._command_version(['wsl', 'claude']), '')
        mock_run.return_value = MagicMock(stdout='2.1.204 (Claude Code)\n', stderr='', returncode=0)
        self.assertEqual(claude_cli._command_version(['wsl', 'claude']), '2.1.204')

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

    def setUp(self):
        claude_cli._command_version_cache.clear()

    @patch('usage_monitor_for_claude.claude_cli.subprocess.run')
    @patch('usage_monitor_for_claude.claude_cli.CLAUDE_CLI_PATH')
    @patch('usage_monitor_for_claude.claude_cli.CLI_COMMAND', {'WSL': ['wsl', 'claude']})
    @patch('usage_monitor_for_claude.claude_cli._EXTENSION_DIRS', [])
    def test_lost_command_output_does_not_reach_the_popup(self, mock_path, mock_run):
        """A lost stream from a configured command must not break the popup.

        _command_version() parses its output outside the try that guards the
        subprocess, and find_installations() calls it unguarded, so anything
        raised here lands in the popup's update path.
        """
        mock_path.is_file.return_value = False
        mock_run.return_value = MagicMock(stdout=None, stderr=None, returncode=0)
        self.assertEqual(find_installations(), [])

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
# _run_cli (output decoding)
# ---------------------------------------------------------------------------

# Child processes for the decoding tests.  Each writes bytes rather than text so
# the output is fixed by the test and not by the child's own stream encoding:
# the UTF-8 the Claude CLI emits for its progress and npm error glyphs, and a
# byte that is valid UTF-8 nowhere.
_UTF8_CHILD = (
    'import sys;'
    r"sys.stdout.buffer.write('✓ ok\n'.encode('utf-8'));"
    r"sys.stderr.buffer.write('• note\n'.encode('utf-8'))"
)

_INVALID_UTF8_CHILD = r"import sys; sys.stdout.buffer.write(b'\xff bad\n')"


class TestRunCli(unittest.TestCase):
    """Tests for _run_cli(), which owns the module's output decoding.

    The Claude CLI writes UTF-8 whatever the Windows code page is.  Letting
    subprocess decode it with the ambient locale codec instead is what caused
    the crash in issue #80, and that failure happens inside subprocess's pipe
    reader thread - so these tests run real child processes rather than mock
    the mechanism they are about.
    """

    def _run_with_locale_codec(self):
        """Run the UTF-8 child while decoding with a codec that cannot read it."""
        return subprocess.run(
            [sys.executable, '-c', _UTF8_CHILD],
            capture_output=True, text=True, encoding='cp950', timeout=30,
            **no_window_kwargs(),
        )

    def test_locale_codec_loses_the_captured_stream(self):
        """Reproduces #80: a locale codec that cannot decode UTF-8 drops the output.

        Naming cp950 explicitly reproduces the reporter's Traditional Chinese
        system on any machine.  Windows drains the pipes on reader threads, so
        the decode failure dies there: both streams come back as None even
        though capture_output was set, and that None is what the crash
        concatenated.
        """
        # The decode failure surfaces as an uncaught exception in subprocess's
        # reader thread; silence its traceback to keep the test output readable.
        with patch('threading.excepthook', lambda args: None):
            proc = self._run_with_locale_codec()
        self.assertEqual(proc.returncode, 0)
        self.assertIsNone(proc.stdout)
        self.assertIsNone(proc.stderr)


    def test_pinned_codec_keeps_the_same_output(self):
        """_run_cli decodes the very output the locale codec drops."""
        proc = claude_cli._run_cli([sys.executable, '-c', _UTF8_CHILD], timeout=30)
        self.assertEqual(proc.stdout, '✓ ok\n')
        self.assertEqual(proc.stderr, '• note\n')

    def test_undecodable_bytes_are_replaced(self):
        """Bytes that are not valid UTF-8 are replaced, so no reader thread dies."""
        proc = claude_cli._run_cli([sys.executable, '-c', _INVALID_UTF8_CHILD], timeout=30)
        self.assertEqual(proc.stdout, '\ufffd bad\n')

    @patch('usage_monitor_for_claude.claude_cli.subprocess.run')
    def test_lost_stdout_raises_oserror(self, mock_run):
        """A stream reported as None is an I/O failure, not an empty result."""
        mock_run.return_value = MagicMock(stdout=None, stderr='', returncode=0)
        with self.assertRaises(OSError):
            claude_cli._run_cli(['claude', '--version'], timeout=10)

    @patch('usage_monitor_for_claude.claude_cli.subprocess.run')
    def test_lost_stderr_raises_oserror(self, mock_run):
        """Only the stream carrying the glyph is dropped, so stderr alone can be lost."""
        mock_run.return_value = MagicMock(stdout='2.1.69 (Claude Code)\n', stderr=None, returncode=0)
        with self.assertRaises(OSError):
            claude_cli._run_cli(['claude', '--version'], timeout=10)


if __name__ == '__main__':
    unittest.main()

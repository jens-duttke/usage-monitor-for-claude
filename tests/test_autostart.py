"""
Autostart Tests
================

Unit tests for Windows autostart registry management.
"""
from __future__ import annotations

import sys
import unittest
from unittest.mock import MagicMock, patch

import usage_monitor_for_claude.autostart as autostart_mod


class _DefaultConfigDirTestCase(unittest.TestCase):
    """Base class pinning the default config dir (no per-instance suffix)."""

    def setUp(self):
        patcher_suffix = patch.object(autostart_mod, 'config_dir_suffix', return_value='')
        patcher_default = patch.object(autostart_mod, 'is_default_config_dir', return_value=True)
        patcher_suffix.start()
        patcher_default.start()
        self.addCleanup(patcher_suffix.stop)
        self.addCleanup(patcher_default.stop)


class TestIsAutostartEnabled(_DefaultConfigDirTestCase):
    """Tests for is_autostart_enabled()."""

    @patch.object(autostart_mod, 'winreg')
    def test_returns_true_when_value_exists(self, mock_winreg):
        """Registry entry found returns True."""
        mock_key = MagicMock()
        mock_winreg.OpenKey.return_value.__enter__ = MagicMock(return_value=mock_key)
        mock_winreg.OpenKey.return_value.__exit__ = MagicMock(return_value=False)

        self.assertTrue(autostart_mod.is_autostart_enabled())
        mock_winreg.QueryValueEx.assert_called_once_with(mock_key, autostart_mod.AUTOSTART_REG_BASE_NAME)

    @patch.object(autostart_mod, 'winreg')
    def test_returns_false_when_key_missing(self, mock_winreg):
        """FileNotFoundError on key open returns False."""
        mock_winreg.OpenKey.side_effect = FileNotFoundError

        self.assertFalse(autostart_mod.is_autostart_enabled())

    @patch.object(autostart_mod, 'winreg')
    def test_returns_false_when_value_missing(self, mock_winreg):
        """Key exists but value does not returns False."""
        mock_key = MagicMock()
        mock_winreg.OpenKey.return_value.__enter__ = MagicMock(return_value=mock_key)
        mock_winreg.OpenKey.return_value.__exit__ = MagicMock(return_value=False)
        mock_winreg.QueryValueEx.side_effect = FileNotFoundError

        self.assertFalse(autostart_mod.is_autostart_enabled())

    @patch.object(autostart_mod, 'winreg')
    def test_opens_correct_registry_path(self, mock_winreg):
        """Opens HKCU Run key with correct path."""
        mock_winreg.OpenKey.return_value.__enter__ = MagicMock()
        mock_winreg.OpenKey.return_value.__exit__ = MagicMock(return_value=False)

        autostart_mod.is_autostart_enabled()

        mock_winreg.OpenKey.assert_called_once_with(
            mock_winreg.HKEY_CURRENT_USER, autostart_mod.AUTOSTART_REG_KEY,
        )


class TestSetAutostart(_DefaultConfigDirTestCase):
    """Tests for set_autostart()."""

    @patch.object(autostart_mod, 'winreg')
    def test_enable_sets_quoted_executable_path(self, mock_winreg):
        """Enabling autostart writes the quoted sys.executable path."""
        mock_key = MagicMock()
        mock_winreg.OpenKey.return_value.__enter__ = MagicMock(return_value=mock_key)
        mock_winreg.OpenKey.return_value.__exit__ = MagicMock(return_value=False)

        autostart_mod.set_autostart(True)

        mock_winreg.SetValueEx.assert_called_once_with(
            mock_key, autostart_mod.AUTOSTART_REG_BASE_NAME, 0,
            mock_winreg.REG_SZ, f'"{sys.executable}"',
        )

    @patch.object(autostart_mod, 'winreg')
    def test_disable_deletes_registry_value(self, mock_winreg):
        """Disabling autostart removes the registry value."""
        mock_key = MagicMock()
        mock_winreg.OpenKey.return_value.__enter__ = MagicMock(return_value=mock_key)
        mock_winreg.OpenKey.return_value.__exit__ = MagicMock(return_value=False)

        autostart_mod.set_autostart(False)

        mock_winreg.DeleteValue.assert_called_once_with(mock_key, autostart_mod.AUTOSTART_REG_BASE_NAME)
        mock_winreg.SetValueEx.assert_not_called()

    @patch.object(autostart_mod, 'winreg')
    def test_disable_ignores_missing_value(self, mock_winreg):
        """Disabling when value already absent does not raise."""
        mock_key = MagicMock()
        mock_winreg.OpenKey.return_value.__enter__ = MagicMock(return_value=mock_key)
        mock_winreg.OpenKey.return_value.__exit__ = MagicMock(return_value=False)
        mock_winreg.DeleteValue.side_effect = FileNotFoundError

        autostart_mod.set_autostart(False)  # should not raise

    @patch.object(autostart_mod, 'winreg')
    def test_enable_opens_with_set_value_permission(self, mock_winreg):
        """Opening registry for write uses KEY_SET_VALUE."""
        mock_winreg.OpenKey.return_value.__enter__ = MagicMock()
        mock_winreg.OpenKey.return_value.__exit__ = MagicMock(return_value=False)

        autostart_mod.set_autostart(True)

        mock_winreg.OpenKey.assert_called_once_with(
            mock_winreg.HKEY_CURRENT_USER, autostart_mod.AUTOSTART_REG_KEY,
            0, mock_winreg.KEY_SET_VALUE,
        )

    @patch.object(autostart_mod, 'winreg')
    def test_enable_uses_current_executable(self, mock_winreg):
        """Uses sys.executable for the registry value."""
        mock_key = MagicMock()
        mock_winreg.OpenKey.return_value.__enter__ = MagicMock(return_value=mock_key)
        mock_winreg.OpenKey.return_value.__exit__ = MagicMock(return_value=False)

        with patch.object(sys, 'executable', r'C:\Program Files\MyApp\app.exe'):
            autostart_mod.set_autostart(True)

        mock_winreg.SetValueEx.assert_called_once_with(
            mock_key, autostart_mod.AUTOSTART_REG_BASE_NAME, 0,
            mock_winreg.REG_SZ, r'"C:\Program Files\MyApp\app.exe"',
        )


class TestSyncAutostartPath(_DefaultConfigDirTestCase):
    """Tests for sync_autostart_path()."""

    @patch.object(autostart_mod, 'winreg')
    def test_returns_early_when_no_registry_entry(self, mock_winreg):
        """No registry entry means no update attempted."""
        mock_winreg.OpenKey.side_effect = FileNotFoundError

        autostart_mod.sync_autostart_path()  # should not raise

        mock_winreg.SetValueEx.assert_not_called()

    @patch.object(autostart_mod, 'set_autostart')
    @patch.object(autostart_mod, 'winreg')
    def test_updates_when_path_differs(self, mock_winreg, mock_set):
        """Stored path differs from sys.executable triggers update."""
        mock_key = MagicMock()
        mock_winreg.OpenKey.return_value.__enter__ = MagicMock(return_value=mock_key)
        mock_winreg.OpenKey.return_value.__exit__ = MagicMock(return_value=False)
        mock_winreg.QueryValueEx.return_value = (r'"C:\old\path.exe"', 1)

        with patch.object(sys, 'executable', r'C:\new\path.exe'):
            autostart_mod.sync_autostart_path()

        mock_set.assert_called_once_with(True)

    @patch.object(autostart_mod, 'set_autostart')
    @patch.object(autostart_mod, 'winreg')
    def test_skips_update_when_path_matches(self, mock_winreg, mock_set):
        """Matching stored path skips the update."""
        mock_key = MagicMock()
        mock_winreg.OpenKey.return_value.__enter__ = MagicMock(return_value=mock_key)
        mock_winreg.OpenKey.return_value.__exit__ = MagicMock(return_value=False)

        expected = f'"{sys.executable}"'
        mock_winreg.QueryValueEx.return_value = (expected, 1)

        autostart_mod.sync_autostart_path()

        mock_set.assert_not_called()


class TestCustomConfigDirAutostart(unittest.TestCase):
    """Per-instance registry naming and command for a non-default config dir."""

    def setUp(self):
        patcher_suffix = patch.object(autostart_mod, 'config_dir_suffix', return_value='_abc123def456')
        patcher_default = patch.object(autostart_mod, 'is_default_config_dir', return_value=False)
        patcher_env = patch.dict('os.environ', {'CLAUDE_CONFIG_DIR': r'C:\Users\test\.claude-second'})
        patcher_suffix.start()
        patcher_default.start()
        patcher_env.start()
        self.addCleanup(patcher_suffix.stop)
        self.addCleanup(patcher_default.stop)
        self.addCleanup(patcher_env.stop)

    @patch.object(autostart_mod, 'winreg')
    def test_enable_uses_suffixed_value_name(self, mock_winreg):
        """Non-default config dir writes a suffixed registry value name."""
        mock_key = MagicMock()
        mock_winreg.OpenKey.return_value.__enter__ = MagicMock(return_value=mock_key)
        mock_winreg.OpenKey.return_value.__exit__ = MagicMock(return_value=False)

        autostart_mod.set_autostart(True)

        name = mock_winreg.SetValueEx.call_args[0][1]
        self.assertEqual(name, 'UsageMonitorForClaude_abc123def456')

    @patch.object(autostart_mod, 'winreg')
    def test_enable_command_includes_config_dir(self, mock_winreg):
        """Stored command carries --config-dir so autostart targets the right account."""
        mock_key = MagicMock()
        mock_winreg.OpenKey.return_value.__enter__ = MagicMock(return_value=mock_key)
        mock_winreg.OpenKey.return_value.__exit__ = MagicMock(return_value=False)

        autostart_mod.set_autostart(True)

        command = mock_winreg.SetValueEx.call_args[0][4]
        self.assertEqual(command, f'"{sys.executable}" --config-dir="C:\\Users\\test\\.claude-second"')

    @patch.object(autostart_mod, 'winreg')
    def test_disable_deletes_suffixed_value(self, mock_winreg):
        """Disabling removes the per-instance registry value."""
        mock_key = MagicMock()
        mock_winreg.OpenKey.return_value.__enter__ = MagicMock(return_value=mock_key)
        mock_winreg.OpenKey.return_value.__exit__ = MagicMock(return_value=False)

        autostart_mod.set_autostart(False)

        mock_winreg.DeleteValue.assert_called_once_with(mock_key, 'UsageMonitorForClaude_abc123def456')

    @patch.object(autostart_mod, 'set_autostart')
    @patch.object(autostart_mod, 'winreg')
    def test_sync_skips_when_command_matches(self, mock_winreg, mock_set):
        """sync_autostart_path() compares against the command including --config-dir."""
        mock_key = MagicMock()
        mock_winreg.OpenKey.return_value.__enter__ = MagicMock(return_value=mock_key)
        mock_winreg.OpenKey.return_value.__exit__ = MagicMock(return_value=False)
        stored = f'"{sys.executable}" --config-dir="C:\\Users\\test\\.claude-second"'
        mock_winreg.QueryValueEx.return_value = (stored, 1)

        autostart_mod.sync_autostart_path()

        mock_set.assert_not_called()

    @patch.object(autostart_mod, 'set_autostart')
    @patch.object(autostart_mod, 'winreg')
    def test_sync_updates_when_flag_missing(self, mock_winreg, mock_set):
        """A stored command without --config-dir is rewritten."""
        mock_key = MagicMock()
        mock_winreg.OpenKey.return_value.__enter__ = MagicMock(return_value=mock_key)
        mock_winreg.OpenKey.return_value.__exit__ = MagicMock(return_value=False)
        mock_winreg.QueryValueEx.return_value = (f'"{sys.executable}"', 1)

        autostart_mod.sync_autostart_path()

        mock_set.assert_called_once_with(True)


if __name__ == '__main__':
    unittest.main()

"""
Single-Instance Tests
======================

Unit tests for the single-instance guard: shared memory round-trip,
ensure_single_instance control flow, and release_instance_lock.
"""
from __future__ import annotations

import os
import unittest
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch

MODULE = 'usage_monitor_for_claude.single_instance'


def _reset_globals():
    """Reset module-level handles to None between tests."""
    import usage_monitor_for_claude.single_instance as si
    si._mutex_handle = None
    si._pid_mapping_handle = None


# ---------------------------------------------------------------------------
# Shared memory round-trip
# ---------------------------------------------------------------------------

class TestSharedMemoryRoundTrip(unittest.TestCase):
    """Verify _store_holder_info / _read_holder_info write and read correctly."""

    def setUp(self):
        _reset_globals()

    def tearDown(self):
        import usage_monitor_for_claude.single_instance as si
        if si._pid_mapping_handle:
            si._kernel32.CloseHandle(si._pid_mapping_handle)
            si._pid_mapping_handle = None

    @patch(f'{MODULE}.__version__', '2.5.3')
    def test_round_trip_returns_pid_and_version(self):
        from usage_monitor_for_claude.single_instance import _read_holder_info, _store_holder_info

        _store_holder_info()
        pid, version = _read_holder_info()

        self.assertEqual(pid, os.getpid())
        self.assertEqual(version, '2.5.3')

    @patch(f'{MODULE}.__version__', '0.0.1')
    def test_round_trip_short_version(self):
        from usage_monitor_for_claude.single_instance import _read_holder_info, _store_holder_info

        _store_holder_info()
        pid, version = _read_holder_info()

        self.assertEqual(pid, os.getpid())
        self.assertEqual(version, '0.0.1')

    @patch(f'{MODULE}.__version__', 'a' * 100)
    def test_long_version_is_truncated(self):
        """Version strings exceeding shared memory size are truncated, not crashed."""
        from usage_monitor_for_claude.single_instance import _read_holder_info, _store_holder_info

        _store_holder_info()
        pid, version = _read_holder_info()

        self.assertEqual(pid, os.getpid())
        self.assertIsNotNone(version)
        self.assertTrue(len(version) < 100)


# ---------------------------------------------------------------------------
# ensure_single_instance
# ---------------------------------------------------------------------------

class TestEnsureSingleInstance(unittest.TestCase):
    """Tests for ensure_single_instance() control flow."""

    def setUp(self):
        _reset_globals()

    def tearDown(self):
        _reset_globals()

    @patch(f'{MODULE}._store_holder_info')
    @patch(f'{MODULE}.ctypes')
    def test_first_instance_returns_true(self, mock_ctypes, mock_store):
        """First instance (no duplicate) creates mutex and returns True."""
        mock_ctypes.get_last_error.return_value = 0
        mock_ctypes.WinDLL = MagicMock
        mock_kernel32 = MagicMock()
        mock_kernel32.CreateMutexW.return_value = 42

        with patch(f'{MODULE}._kernel32', mock_kernel32):
            from usage_monitor_for_claude.single_instance import ensure_single_instance
            result = ensure_single_instance()

        self.assertTrue(result)
        mock_store.assert_called_once()

    @patch(f'{MODULE}._terminate_pid')
    @patch(f'{MODULE}._read_holder_info', return_value=(99999, '1.9.0'))
    @patch(f'{MODULE}._store_holder_info')
    @patch(f'{MODULE}.ctypes')
    def test_duplicate_user_accepts_returns_true(self, mock_ctypes, mock_store, mock_read, mock_terminate):
        """Duplicate detected, user clicks Yes - terminates old instance and returns True."""
        mock_ctypes.get_last_error.return_value = 0xB7
        mock_kernel32 = MagicMock()
        mock_kernel32.CreateMutexW.return_value = 42

        mock_user32 = MagicMock()
        mock_user32.MessageBoxW.return_value = 6  # IDYES

        with patch(f'{MODULE}._kernel32', mock_kernel32), \
             patch(f'{MODULE}.ctypes.windll.user32', mock_user32):
            from usage_monitor_for_claude.single_instance import ensure_single_instance
            result = ensure_single_instance()

        self.assertTrue(result)
        mock_terminate.assert_called_once_with(99999)
        self.assertEqual(mock_store.call_count, 1)

    @patch(f'{MODULE}._read_holder_info', return_value=(99999, '1.9.0'))
    @patch(f'{MODULE}.ctypes')
    def test_duplicate_user_declines_returns_false(self, mock_ctypes, mock_read):
        """Duplicate detected, user clicks No - returns False without terminating."""
        mock_ctypes.get_last_error.return_value = 0xB7
        mock_kernel32 = MagicMock()
        mock_kernel32.CreateMutexW.return_value = 42

        mock_user32 = MagicMock()
        mock_user32.MessageBoxW.return_value = 7  # IDNO

        with patch(f'{MODULE}._kernel32', mock_kernel32), \
             patch(f'{MODULE}.ctypes.windll.user32', mock_user32):
            from usage_monitor_for_claude.single_instance import ensure_single_instance
            result = ensure_single_instance()

        self.assertFalse(result)

    @patch(f'{MODULE}._read_holder_info', return_value=(99999, '1.9.0'))
    @patch(f'{MODULE}.ctypes')
    def test_duplicate_dialog_title_includes_version(self, mock_ctypes, mock_read):
        """Dialog title includes the running version."""
        mock_ctypes.get_last_error.return_value = 0xB7
        mock_kernel32 = MagicMock()
        mock_kernel32.CreateMutexW.return_value = 42

        mock_user32 = MagicMock()
        mock_user32.MessageBoxW.return_value = 7  # IDNO

        with patch(f'{MODULE}._kernel32', mock_kernel32), \
             patch(f'{MODULE}.ctypes.windll.user32', mock_user32):
            from usage_monitor_for_claude.single_instance import ensure_single_instance
            ensure_single_instance()

        title = mock_user32.MessageBoxW.call_args[0][2]
        self.assertIn('v1.9.0', title)

    @patch(f'{MODULE}._read_holder_info', return_value=(99999, None))
    @patch(f'{MODULE}.ctypes')
    def test_duplicate_unknown_version_shows_question_mark(self, mock_ctypes, mock_read):
        """When version is unknown, message shows '?' as placeholder."""
        mock_ctypes.get_last_error.return_value = 0xB7
        mock_kernel32 = MagicMock()
        mock_kernel32.CreateMutexW.return_value = 42

        mock_user32 = MagicMock()
        mock_user32.MessageBoxW.return_value = 7  # IDNO

        with patch(f'{MODULE}._kernel32', mock_kernel32), \
             patch(f'{MODULE}.ctypes.windll.user32', mock_user32):
            from usage_monitor_for_claude.single_instance import ensure_single_instance
            ensure_single_instance()

        message = mock_user32.MessageBoxW.call_args[0][1]
        self.assertIn('?', message)


# ---------------------------------------------------------------------------
# Per-instance object names
# ---------------------------------------------------------------------------

class TestObjectNames(unittest.TestCase):
    """Tests for _object_names() per-config-dir suffixing."""

    def test_default_config_dir_uses_legacy_names(self):
        """Default config dir keeps the unsuffixed names for cross-version detection."""
        import usage_monitor_for_claude.single_instance as si

        with patch(f'{MODULE}.config_dir_suffix', return_value=''):
            mutex_name, mapping_name = si._object_names()

        self.assertEqual(mutex_name, 'UsageMonitorForClaude_SingleInstance')
        self.assertEqual(mapping_name, 'UsageMonitorForClaude_HolderPID')

    def test_custom_config_dir_appends_suffix(self):
        """A custom config dir yields suffixed, per-instance names."""
        import usage_monitor_for_claude.single_instance as si

        with patch(f'{MODULE}.config_dir_suffix', return_value='_abc123def456'):
            mutex_name, mapping_name = si._object_names()

        self.assertEqual(mutex_name, 'UsageMonitorForClaude_SingleInstance_abc123def456')
        self.assertEqual(mapping_name, 'UsageMonitorForClaude_HolderPID_abc123def456')

    def test_two_config_dirs_get_distinct_names(self):
        """Two different config dirs never collide on kernel object names."""
        import usage_monitor_for_claude.single_instance as si

        with TemporaryDirectory() as dir_a, TemporaryDirectory() as dir_b:
            with patch.dict('os.environ', {'CLAUDE_CONFIG_DIR': dir_a}):
                names_a = si._object_names()
            with patch.dict('os.environ', {'CLAUDE_CONFIG_DIR': dir_b}):
                names_b = si._object_names()

        self.assertNotEqual(names_a[0], names_b[0])
        self.assertNotEqual(names_a[1], names_b[1])


# ---------------------------------------------------------------------------
# release_instance_lock
# ---------------------------------------------------------------------------

class TestReleaseInstanceLock(unittest.TestCase):
    """Tests for release_instance_lock()."""

    def setUp(self):
        _reset_globals()

    def tearDown(self):
        _reset_globals()

    def test_release_closes_both_handles(self):
        """Both mutex and mapping handles are closed and set to None."""
        import usage_monitor_for_claude.single_instance as si
        mock_kernel32 = MagicMock()

        si._mutex_handle = 100
        si._pid_mapping_handle = 200

        with patch(f'{MODULE}._kernel32', mock_kernel32):
            si.release_instance_lock()

        self.assertIsNone(si._mutex_handle)
        self.assertIsNone(si._pid_mapping_handle)
        self.assertEqual(mock_kernel32.CloseHandle.call_count, 2)

    def test_release_with_no_handles_is_safe(self):
        """Calling release when no handles are held does not crash."""
        import usage_monitor_for_claude.single_instance as si

        si._mutex_handle = None
        si._pid_mapping_handle = None
        si.release_instance_lock()

        self.assertIsNone(si._mutex_handle)
        self.assertIsNone(si._pid_mapping_handle)


if __name__ == '__main__':
    unittest.main()

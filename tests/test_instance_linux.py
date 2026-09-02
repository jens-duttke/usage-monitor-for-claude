"""
Linux Single-Instance Tests
============================

Unit tests for the flock-based single-instance guard.
"""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch

if sys.platform == 'win32':
    raise unittest.SkipTest('Linux single-instance guard is not used on Windows')

import usage_monitor_for_claude.platforms.instance_linux as si  # noqa: E402


class _LockTestCase(unittest.TestCase):
    """Redirect the lock file into a temporary directory."""

    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.directory = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        self.addCleanup(si.release_instance_lock)

        patcher = patch.object(si, '_lock_directory', return_value=self.directory)
        patcher.start()
        self.addCleanup(patcher.stop)

        suffix = patch.object(si, 'config_dir_suffix', return_value='')
        suffix.start()
        self.addCleanup(suffix.stop)

        si._lock_fd = None


class TestLockPath(unittest.TestCase):
    """Tests for lock file placement."""

    def test_prefers_runtime_dir(self):
        """XDG_RUNTIME_DIR is the correct home for runtime state."""
        with patch.dict(os.environ, {'XDG_RUNTIME_DIR': '/run/user/42'}):
            self.assertEqual(si._lock_directory(), Path('/run/user/42'))

    def test_falls_back_to_cache_home(self):
        """A session without a runtime dir still gets a per-user location."""
        with patch.dict(os.environ, {'XDG_CACHE_HOME': '/home/u/.cache'}, clear=True):
            self.assertEqual(si._lock_directory(), Path('/home/u/.cache'))

    def test_falls_back_to_home_cache(self):
        """Without either variable the default cache directory is used."""
        with patch.dict(os.environ, {}, clear=True), \
             patch.object(Path, 'home', return_value=Path('/home/u')):
            self.assertEqual(si._lock_directory(), Path('/home/u/.cache'))

    def test_config_dir_gets_own_lock(self):
        """A second monitored account is a singleton for itself only."""
        with patch.object(si, '_lock_directory', return_value=Path('/run')), \
             patch.object(si, 'config_dir_suffix', return_value='-abc'):
            self.assertEqual(si._lock_path(), Path('/run/usage-monitor-for-claude-abc.lock'))


class TestEnsureSingleInstance(_LockTestCase):
    """Tests for acquiring, refusing and replacing the lock."""

    def test_first_instance_acquires(self):
        """A free lock is taken and the holder record is written."""
        self.assertTrue(si.ensure_single_instance())
        pid, version = si._read_holder_info()
        self.assertEqual(pid, os.getpid())
        self.assertTrue(version)

    def test_second_instance_declined_by_user(self):
        """Declining the replace dialog exits without touching the holder."""
        self.assertTrue(si.ensure_single_instance())
        held_fd = si._lock_fd

        with patch.object(si, '_acquire', return_value=None), \
             patch.object(si, 'ask_yes_no', return_value=False) as mock_ask:
            self.assertFalse(si.ensure_single_instance())

        mock_ask.assert_called_once()
        self.assertIs(si._lock_fd, held_fd)

    def test_replace_terminates_holder_and_retakes(self):
        """Accepting the dialog terminates the holder and retakes the lock."""
        self.directory.joinpath('usage-monitor-for-claude.lock').write_text('4242\n9.9.9\n', encoding='utf-8')

        with patch.object(si, '_acquire', side_effect=[None, 7]), \
             patch.object(si, 'ask_yes_no', return_value=True), \
             patch.object(si, '_terminate_pid') as mock_terminate, \
             patch.object(si, '_store_holder_info') as mock_store:
            self.assertTrue(si.ensure_single_instance())

        mock_terminate.assert_called_once_with(4242)
        mock_store.assert_called_once_with(7)
        si._lock_fd = None

    def test_replace_fails_when_holder_survives(self):
        """A holder that will not die leaves the new instance refusing to start."""
        self.directory.joinpath('usage-monitor-for-claude.lock').write_text('4242\n9.9.9\n', encoding='utf-8')

        with patch.object(si, '_acquire', return_value=None), \
             patch.object(si, 'ask_yes_no', return_value=True), \
             patch.object(si, '_terminate_pid'), \
             patch.object(si, 'show_topmost_error') as mock_error:
            self.assertFalse(si.ensure_single_instance())

        mock_error.assert_called_once()

    def test_recycled_pid_is_not_terminated(self):
        """A holder that exited during the dialog must not have its PID killed.

        The kernel recycles PIDs, so the snapshot could name an unrelated
        process by the time the user answers.
        """
        self.directory.joinpath('usage-monitor-for-claude.lock').write_text('4242\n9.9.9\n', encoding='utf-8')

        def rewrite_holder():
            self.directory.joinpath('usage-monitor-for-claude.lock').write_text('5555\n9.9.9\n', encoding='utf-8')
            return True

        with patch.object(si, '_acquire', side_effect=[None, 7]), \
             patch.object(si, 'ask_yes_no', side_effect=lambda *a: rewrite_holder()), \
             patch.object(si, '_terminate_pid') as mock_terminate, \
             patch.object(si, '_store_holder_info'):
            self.assertTrue(si.ensure_single_instance())

        mock_terminate.assert_not_called()
        si._lock_fd = None

    def test_dialog_title_carries_holder_version(self):
        """The dialog names the version that is already running."""
        self.directory.joinpath('usage-monitor-for-claude.lock').write_text('4242\n1.2.3\n', encoding='utf-8')

        with patch.object(si, '_acquire', return_value=None), \
             patch.object(si, 'ask_yes_no', return_value=False) as mock_ask:
            si.ensure_single_instance()

        self.assertIn('1.2.3', mock_ask.call_args[0][1])

    def test_unknown_version_shows_question_mark(self):
        """A holder record without a version still produces a usable message."""
        self.directory.joinpath('usage-monitor-for-claude.lock').write_text('4242\n', encoding='utf-8')

        with patch.object(si, '_acquire', return_value=None), \
             patch.object(si, 'ask_yes_no', return_value=False) as mock_ask:
            si.ensure_single_instance()

        self.assertIn('?', mock_ask.call_args[0][0])


class TestHolderRecord(_LockTestCase):
    """Tests for reading and writing the holder record."""

    def test_round_trip(self):
        """PID and version survive a write/read cycle."""
        si.ensure_single_instance()
        pid, version = si._read_holder_info()
        self.assertEqual(pid, os.getpid())
        self.assertEqual(version, si.__version__)

    def test_missing_file(self):
        """No lock file means no holder."""
        self.assertEqual(si._read_holder_info(), (None, None))

    def test_empty_file(self):
        """An empty lock file is not a holder."""
        self.directory.joinpath('usage-monitor-for-claude.lock').write_text('', encoding='utf-8')
        self.assertEqual(si._read_holder_info(), (None, None))

    def test_malformed_pid(self):
        """A non-numeric first line is ignored rather than raising."""
        self.directory.joinpath('usage-monitor-for-claude.lock').write_text('not-a-pid\n1.0\n', encoding='utf-8')
        self.assertEqual(si._read_holder_info(), (None, None))

    def test_zero_pid_is_no_holder_but_keeps_version(self):
        """PID 0 names no process, yet the version still labels the dialog.

        Matches the Windows guard, which also reports the version from a
        record whose PID is unusable.
        """
        self.directory.joinpath('usage-monitor-for-claude.lock').write_text('0\n1.0\n', encoding='utf-8')
        self.assertEqual(si._read_holder_info(), (None, '1.0'))

    def test_write_truncates_previous_record(self):
        """A shorter record must not leave bytes of the previous one behind."""
        path = self.directory / 'usage-monitor-for-claude.lock'
        path.write_text('999999999\n99.99.99-longer\n', encoding='utf-8')
        si.ensure_single_instance()
        self.assertEqual(path.read_text(encoding='utf-8'), f'{os.getpid()}\n{si.__version__}\n')


class TestTerminatePid(unittest.TestCase):
    """Tests for the escalating terminate sequence."""

    def test_sigterm_then_gone(self):
        """A holder that exits on SIGTERM is never escalated to SIGKILL."""
        with patch.object(si.os, 'kill') as mock_kill, \
             patch.object(si, '_process_is_alive', return_value=False), \
             patch.object(si.time, 'sleep'):
            si._terminate_pid(4242)

        self.assertEqual(mock_kill.call_count, 1)
        self.assertEqual(mock_kill.call_args[0][1], si.signal.SIGTERM)

    def test_escalates_to_sigkill(self):
        """A holder that ignores SIGTERM is killed once the timeout expires."""
        clock = iter([0.0] + [i * 1.0 for i in range(1, 40)])
        with patch.object(si.os, 'kill') as mock_kill, \
             patch.object(si, '_process_is_alive', return_value=True), \
             patch.object(si.time, 'sleep'), \
             patch.object(si.time, 'monotonic', side_effect=lambda: next(clock)):
            si._terminate_pid(4242)

        signals = [call[0][1] for call in mock_kill.call_args_list]
        self.assertIn(si.signal.SIGKILL, signals)

    def test_missing_process_returns_quietly(self):
        """Signalling a process that already exited is not an error."""
        with patch.object(si.os, 'kill', side_effect=ProcessLookupError):
            si._terminate_pid(4242)


class TestProcessIsAlive(unittest.TestCase):

    def test_own_process_is_alive(self):
        """The running interpreter is by definition alive."""
        self.assertTrue(si._process_is_alive(os.getpid()))

    def test_missing_process(self):
        """A vanished process reports not alive."""
        with patch.object(si.os, 'kill', side_effect=ProcessLookupError):
            self.assertFalse(si._process_is_alive(4242))

    def test_foreign_owner_counts_as_alive(self):
        """A process owned by another user exists even if we cannot signal it."""
        with patch.object(si.os, 'kill', side_effect=PermissionError):
            self.assertTrue(si._process_is_alive(4242))


class TestReleaseInstanceLock(_LockTestCase):
    """Tests for releasing the lock."""

    def test_release_allows_reacquisition(self):
        """After releasing, a fresh acquisition succeeds."""
        self.assertTrue(si.ensure_single_instance())
        si.release_instance_lock()
        self.assertIsNone(si._lock_fd)
        self.assertTrue(si.ensure_single_instance())

    def test_release_without_lock_is_safe(self):
        """Releasing when nothing is held must not raise."""
        si._lock_fd = None
        si.release_instance_lock()

    def test_release_survives_a_closed_descriptor(self):
        """A descriptor already closed elsewhere must not raise."""
        si.ensure_single_instance()
        os.close(si._lock_fd)
        si.release_instance_lock()
        self.assertIsNone(si._lock_fd)


class TestRealLocking(_LockTestCase):
    """Tests that exercise the actual flock, not a mock."""

    def test_second_acquire_in_same_process_is_refused(self):
        """A second descriptor cannot take a lock this process already holds."""
        self.assertTrue(si.ensure_single_instance())
        # flock is per open-file-description, so a fresh open must be refused
        # even from the same process.
        self.assertIsNone(si._acquire(si._lock_path()))

    def test_acquire_creates_the_directory(self):
        """A missing runtime directory is created rather than failing."""
        nested = self.directory / 'nested' / 'deeper'
        with patch.object(si, '_lock_directory', return_value=nested):
            fd = si._acquire(si._lock_path())
        self.assertIsNotNone(fd)
        os.close(fd)
        self.assertTrue(nested.is_dir())

    def test_lock_file_is_user_only(self):
        """The lock file must not be readable by other users."""
        si.ensure_single_instance()
        mode = si._lock_path().stat().st_mode & 0o777
        self.assertEqual(mode, 0o600)


if __name__ == '__main__':
    unittest.main()

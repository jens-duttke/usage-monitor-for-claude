"""
Linux Local Channel Tests
==========================

Tests for the Unix domain socket against a real socket in a temporary
directory that stands in for ``$XDG_RUNTIME_DIR``.
"""
from __future__ import annotations

import os
import socket
import stat
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

if sys.platform == 'win32':
    raise unittest.SkipTest('Unix domain sockets')

from usage_monitor_for_claude.platforms import channel_linux  # noqa: E402
from usage_monitor_for_claude.platforms.channel_linux import LocalChannelListener, open_local_channel  # noqa: E402


class _RuntimeDirTestCase(unittest.TestCase):
    """Points ``XDG_RUNTIME_DIR`` at a fresh temporary directory."""

    def setUp(self):
        runtime_dir = tempfile.TemporaryDirectory()
        self.addCleanup(runtime_dir.cleanup)
        self.runtime_dir = Path(runtime_dir.name)
        patcher = patch.dict(os.environ, {'XDG_RUNTIME_DIR': runtime_dir.name})
        patcher.start()
        self.addCleanup(patcher.stop)

    def _listen(self, name: str = 'channel') -> LocalChannelListener:
        listener = LocalChannelListener(name)
        self.addCleanup(listener._listener.close)
        return listener


class TestSocketChannel(_RuntimeDirTestCase):
    """Tests for listening on and connecting to a real socket."""

    def test_round_trip(self):
        listener = self._listen()

        def serve() -> None:
            connection = listener.accept()
            connection.sendall(b'echo:' + connection.recv(100))
            connection.close()
        threading.Thread(target=serve, daemon=True).start()

        client = open_local_channel('channel')
        client.sendall(b'hello\n')

        self.assertEqual(client.recv(100), b'echo:hello\n')
        client.close()

    def test_socket_lives_in_the_runtime_directory(self):
        self._listen('channel')

        self.assertTrue(stat.S_ISSOCK((self.runtime_dir / 'channel.sock').lstat().st_mode))

    def test_only_the_owner_may_connect(self):
        """Connecting needs write access to the socket file, which 0600 keeps from every other user."""
        self._listen('channel')

        self.assertEqual(stat.S_IMODE((self.runtime_dir / 'channel.sock').stat().st_mode), 0o600)

    def test_nobody_listening(self):
        with self.assertRaises(OSError):
            open_local_channel('nobody')

    def test_fallback_without_runtime_directory(self):
        """Some minimal sessions have no XDG_RUNTIME_DIR; the per-user cache directory takes its place."""
        cache_home = tempfile.TemporaryDirectory()
        self.addCleanup(cache_home.cleanup)
        with patch.dict(os.environ, {'XDG_CACHE_HOME': cache_home.name}):
            del os.environ['XDG_RUNTIME_DIR']
            path = channel_linux._socket_path('channel')

        self.assertEqual(path, Path(cache_home.name) / 'channel.sock')

    def test_socket_lives_next_to_the_instance_lock(self):
        """Both come from the one runtime_directory() helper, so they cannot drift apart."""
        from usage_monitor_for_claude.platforms import instance_linux

        self.assertEqual(channel_linux._socket_path('channel').parent, instance_linux._lock_path().parent)

    def test_listener_creates_a_missing_directory(self):
        nested = self.runtime_dir / 'nested' / 'deeper'
        with patch.dict(os.environ, {'XDG_RUNTIME_DIR': str(nested)}):
            self._listen('channel')

        self.assertTrue(stat.S_ISSOCK((nested / 'channel.sock').lstat().st_mode))

    def test_connecting_creates_no_directory(self):
        """The bridge only connects; it must not create the fallback cache directory as a side effect."""
        missing = self.runtime_dir / 'missing-cache'
        with patch.dict(os.environ, {'XDG_CACHE_HOME': str(missing)}):
            del os.environ['XDG_RUNTIME_DIR']
            with self.assertRaises(OSError):
                open_local_channel('channel')

        self.assertFalse(missing.exists())


class TestLeftoverSocket(_RuntimeDirTestCase):
    """Tests for what a previous run left behind."""

    def test_stale_socket_is_replaced(self):
        """A crash leaves the socket file behind with nobody serving it."""
        stale = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        stale.bind(str(self.runtime_dir / 'channel.sock'))
        stale.close()

        self._listen('channel')

        client = open_local_channel('channel')
        client.close()

    def test_socket_in_use_is_refused(self):
        """A second process must not take over the channel of a running one."""
        self._listen('channel')

        with self.assertRaises(OSError):
            LocalChannelListener('channel')

    def test_file_that_is_no_socket_is_left_alone(self):
        path = self.runtime_dir / 'channel.sock'
        path.write_text('not mine', encoding='utf-8')

        with self.assertRaises(FileExistsError):
            LocalChannelListener('channel')

        self.assertEqual(path.read_text(encoding='utf-8'), 'not mine')


class TestRestart(_RuntimeDirTestCase):
    """Tests for the hand-over from an exiting process to the next one."""

    def test_nothing_runs_at_exit(self):
        """An exit handler that deletes the socket could delete the one a Restart's new process has just created."""
        with patch('atexit.register') as mock_register:
            self._listen('channel')

        mock_register.assert_not_called()

    def test_next_process_takes_over_once_the_old_one_is_gone(self):
        old = self._listen('channel')
        with self.assertRaises(OSError):
            LocalChannelListener('channel')

        old._listener.close()
        self.assertTrue((self.runtime_dir / 'channel.sock').exists())
        new = self._listen('channel')

        def serve() -> None:
            connection = new.accept()
            connection.sendall(b'new\n')
            connection.close()
        threading.Thread(target=serve, daemon=True).start()
        client = open_local_channel('channel')
        self.addCleanup(client.close)

        self.assertEqual(client.recv(100), b'new\n')

    def test_leftover_socket_reads_as_not_running(self):
        """The bridge treats it like a missing socket and reports that the application is not reachable."""
        self._listen('channel')._listener.close()

        with self.assertRaises(ConnectionRefusedError):
            open_local_channel('channel')


if __name__ == '__main__':
    unittest.main()

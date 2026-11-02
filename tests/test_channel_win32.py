"""
Windows Local Channel Tests
============================

Tests for the named pipe against the real Windows pipe file system - a mock
would only prove the mock.  The access rules are read back from the pipe
the listener created.
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes
import sys
import threading
import unittest
import uuid
from unittest.mock import patch

if sys.platform != 'win32':
    raise unittest.SkipTest('Windows named pipes')

from usage_monitor_for_claude.platforms import channel_win32  # noqa: E402
from usage_monitor_for_claude.platforms.channel_win32 import LocalChannelListener, open_local_channel  # noqa: E402

_READ_CONTROL = 0x00020000
_SE_KERNEL_OBJECT = 6
_DACL_SECURITY_INFORMATION = 0x4


def _unique_name() -> str:
    return f'usage-monitor-for-claude-test-{uuid.uuid4().hex[:12]}'


def _echo_server(listener: LocalChannelListener, connections: int = 1) -> threading.Thread:
    """Answer each client with everything it sent up to its first newline, prefixed with ``echo:``."""
    def serve() -> None:
        for _ in range(connections):
            connection = listener.accept()
            received = b''
            try:
                while not received.endswith(b'\n'):
                    chunk = connection.recv(65536)
                    if not chunk:
                        break
                    received += chunk
                connection.sendall(b'echo:' + received)
            except OSError:
                pass  # a client that refused the pipe has already closed it
            finally:
                connection.close()

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    return thread


def _read_all(connection: object) -> bytes:
    received = b''
    while True:
        chunk = connection.recv(65536)  # type: ignore[attr-defined]  # duck-typed channel connection
        if not chunk:
            return received
        received += chunk


def _pipe_dacl(name: str) -> str:
    """Return the DACL of the pipe *name* as SDDL, read from the pipe itself."""
    kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
    advapi32 = ctypes.WinDLL('advapi32', use_last_error=True)
    kernel32.CreateFileW.restype = ctypes.wintypes.HANDLE
    kernel32.CreateFileW.argtypes = [ctypes.wintypes.LPCWSTR, ctypes.wintypes.DWORD, ctypes.wintypes.DWORD, ctypes.c_void_p,
                                     ctypes.wintypes.DWORD, ctypes.wintypes.DWORD, ctypes.wintypes.HANDLE]
    advapi32.GetSecurityInfo.argtypes = [ctypes.wintypes.HANDLE, ctypes.c_int, ctypes.wintypes.DWORD, ctypes.c_void_p, ctypes.c_void_p,
                                         ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
    advapi32.ConvertSecurityDescriptorToStringSecurityDescriptorW.argtypes = [
        ctypes.c_void_p, ctypes.wintypes.DWORD, ctypes.wintypes.DWORD, ctypes.POINTER(ctypes.c_wchar_p), ctypes.c_void_p,
    ]

    handle = kernel32.CreateFileW(channel_win32._pipe_path(name), _READ_CONTROL, 0, None, 3, 0, None)
    descriptor = ctypes.c_void_p()
    sddl = ctypes.c_wchar_p()
    try:
        assert advapi32.GetSecurityInfo(handle, _SE_KERNEL_OBJECT, _DACL_SECURITY_INFORMATION, None, None, None, None, ctypes.byref(descriptor)) == 0
        assert advapi32.ConvertSecurityDescriptorToStringSecurityDescriptorW(descriptor, 1, _DACL_SECURITY_INFORMATION, ctypes.byref(sddl), None)
        return str(sddl.value)
    finally:
        kernel32.CloseHandle(handle)


class TestPipeChannel(unittest.TestCase):
    """Tests for listening on and connecting to a real named pipe."""

    def test_round_trip(self):
        name = _unique_name()
        _echo_server(LocalChannelListener(name))

        client = open_local_channel(name)
        client.sendall(b'hello\n')

        self.assertEqual(_read_all(client), b'echo:hello\n')
        client.close()

    def test_message_larger_than_the_pipe_buffer(self):
        """A write larger than the 64 KiB pipe buffer must arrive whole."""
        name = _unique_name()
        _echo_server(LocalChannelListener(name))
        payload = b'x' * 300_000 + b'\n'

        client = open_local_channel(name)
        client.sendall(payload)

        self.assertEqual(_read_all(client), b'echo:' + payload)
        client.close()

    def test_several_clients_one_after_another(self):
        """accept() keeps the next instance ready, so a second client is not refused."""
        name = _unique_name()
        _echo_server(LocalChannelListener(name), connections=3)

        for index in range(3):
            with self.subTest(index=index):
                client = open_local_channel(name)
                client.sendall(f'{index}\n'.encode())
                self.assertEqual(_read_all(client), f'echo:{index}\n'.encode())
                client.close()

    def test_second_listener_on_the_same_name_is_refused(self):
        """Without FILE_FLAG_FIRST_PIPE_INSTANCE a second process would silently share the name."""
        name = _unique_name()
        LocalChannelListener(name)

        with self.assertRaises(OSError):
            LocalChannelListener(name)

    def test_nobody_listening(self):
        with self.assertRaises(OSError):
            open_local_channel(_unique_name())

    def test_closed_client_reads_as_end_of_stream_on_the_server(self):
        name = _unique_name()
        listener = LocalChannelListener(name)
        received: list[bytes] = []

        def serve() -> None:
            connection = listener.accept()
            received.append(connection.recv(100))
            connection.close()
        thread = threading.Thread(target=serve, daemon=True)
        thread.start()

        open_local_channel(name).close()
        thread.join(timeout=5)

        self.assertEqual(received, [b''])


class TestPipeAccess(unittest.TestCase):
    """Tests for who may use the pipe."""

    def test_only_the_current_user_has_access(self):
        """Windows grants Everyone read access to a named pipe by default; the explicit DACL must replace that."""
        name = _unique_name()
        LocalChannelListener(name)

        self.assertEqual(_pipe_dacl(name), f'D:P(A;;FA;;;{channel_win32._current_user_sid()})')

    def test_name_carries_the_user_sid(self):
        """Pipe names are machine-wide, so another user's instance must not collide with this one."""
        sid = channel_win32._current_user_sid()

        self.assertTrue(sid.startswith('S-1-5-'))
        self.assertEqual(channel_win32._pipe_path('example'), f'\\\\.\\pipe\\example-{sid}')

    def test_pipe_served_by_another_user_is_refused(self):
        """Another user could create the name first; the client must not take its answers for the app's."""
        name = _unique_name()
        _echo_server(LocalChannelListener(name))
        real_sid = channel_win32._current_user_sid()

        def other_user_owns_server(process: int) -> str:
            # The client's own SID still resolves; the server's process is reported as someone else's
            return real_sid if process == channel_win32._kernel32.GetCurrentProcess() else 'S-1-5-21-1-2-3-9999'

        with patch.object(channel_win32, '_process_user_sid', side_effect=other_user_owns_server), \
             patch.object(channel_win32, '_current_user_sid', return_value=real_sid):
            with self.assertRaises(PermissionError):
                open_local_channel(name)

    def test_pipe_served_by_the_current_user_is_accepted(self):
        name = _unique_name()
        _echo_server(LocalChannelListener(name))

        client = open_local_channel(name)
        handle = client._handle
        assert handle is not None

        self.assertTrue(channel_win32._served_by_current_user(handle))
        client.close()


if __name__ == '__main__':
    unittest.main()

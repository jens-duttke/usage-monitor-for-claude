"""
Windows Local Channel
======================

A named pipe that connects the ``--mcp`` bridge process to the running tray
application.

Access is limited to the current user on both ends: the pipe carries an
explicit DACL naming only the user's SID, refuses remote clients, and is
named after that SID so other users' instances never collide with it.  The
client opens it at identification level, so the server cannot impersonate
the client, and accepts a pipe only when the process serving it runs as the
same user - another user could otherwise create the name first.
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes

__all__ = ['LocalChannelListener', 'open_local_channel']

_PIPE_PREFIX = '\\\\.\\pipe\\'
_BUFFER_SIZE = 64 * 1024
_CONNECT_WAIT_MS = 2000

_PIPE_ACCESS_DUPLEX = 0x00000003
_FILE_FLAG_FIRST_PIPE_INSTANCE = 0x00080000
_PIPE_TYPE_BYTE = 0x00000000
_PIPE_READMODE_BYTE = 0x00000000
_PIPE_WAIT = 0x00000000
_PIPE_REJECT_REMOTE_CLIENTS = 0x00000008
_PIPE_UNLIMITED_INSTANCES = 255
_GENERIC_READ = 0x80000000
_GENERIC_WRITE = 0x40000000
_OPEN_EXISTING = 3
# SECURITY_SQOS_PRESENT | SECURITY_IDENTIFICATION: the server learns who the client is, but cannot act as it
_CLIENT_SECURITY_FLAGS = 0x00100000 | 0x00010000
_TOKEN_QUERY = 0x0008
_TOKEN_USER = 1
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_SDDL_REVISION_1 = 1

_ERROR_BROKEN_PIPE = 109
_ERROR_PIPE_BUSY = 231
_ERROR_NO_DATA = 232
_ERROR_PIPE_NOT_CONNECTED = 233
_ERROR_PIPE_CONNECTED = 535
_ERROR_INSUFFICIENT_BUFFER = 122
_CLOSED_PIPE_ERRORS = frozenset({_ERROR_BROKEN_PIPE, _ERROR_NO_DATA, _ERROR_PIPE_NOT_CONNECTED})
_INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value


class _SecurityAttributes(ctypes.Structure):
    _fields_ = [('nLength', ctypes.wintypes.DWORD), ('lpSecurityDescriptor', ctypes.c_void_p), ('bInheritHandle', ctypes.wintypes.BOOL)]


class _SidAndAttributes(ctypes.Structure):
    _fields_ = [('Sid', ctypes.c_void_p), ('Attributes', ctypes.wintypes.DWORD)]


# use_last_error=True captures GetLastError() right after each call, before Python can reset it.
_kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
_advapi32 = ctypes.WinDLL('advapi32', use_last_error=True)

_HANDLE = ctypes.wintypes.HANDLE
_DWORD = ctypes.wintypes.DWORD
_BOOL = ctypes.wintypes.BOOL

_kernel32.CreateNamedPipeW.argtypes = [ctypes.wintypes.LPCWSTR, _DWORD, _DWORD, _DWORD, _DWORD, _DWORD, _DWORD, ctypes.c_void_p]
_kernel32.CreateNamedPipeW.restype = _HANDLE
_kernel32.ConnectNamedPipe.argtypes = [_HANDLE, ctypes.c_void_p]
_kernel32.ConnectNamedPipe.restype = _BOOL
_kernel32.DisconnectNamedPipe.argtypes = [_HANDLE]
_kernel32.DisconnectNamedPipe.restype = _BOOL
_kernel32.FlushFileBuffers.argtypes = [_HANDLE]
_kernel32.FlushFileBuffers.restype = _BOOL
_kernel32.CreateFileW.argtypes = [ctypes.wintypes.LPCWSTR, _DWORD, _DWORD, ctypes.c_void_p, _DWORD, _DWORD, _HANDLE]
_kernel32.CreateFileW.restype = _HANDLE
_kernel32.WaitNamedPipeW.argtypes = [ctypes.wintypes.LPCWSTR, _DWORD]
_kernel32.WaitNamedPipeW.restype = _BOOL
_kernel32.ReadFile.argtypes = [_HANDLE, ctypes.c_void_p, _DWORD, ctypes.POINTER(_DWORD), ctypes.c_void_p]
_kernel32.ReadFile.restype = _BOOL
_kernel32.WriteFile.argtypes = [_HANDLE, ctypes.c_void_p, _DWORD, ctypes.POINTER(_DWORD), ctypes.c_void_p]
_kernel32.WriteFile.restype = _BOOL
_kernel32.CloseHandle.argtypes = [_HANDLE]
_kernel32.CloseHandle.restype = _BOOL
_kernel32.GetCurrentProcess.argtypes = []
_kernel32.GetCurrentProcess.restype = _HANDLE
_kernel32.OpenProcess.argtypes = [_DWORD, _BOOL, _DWORD]
_kernel32.OpenProcess.restype = _HANDLE
_kernel32.GetNamedPipeServerProcessId.argtypes = [_HANDLE, ctypes.POINTER(ctypes.wintypes.ULONG)]
_kernel32.GetNamedPipeServerProcessId.restype = _BOOL
_kernel32.LocalFree.argtypes = [ctypes.c_void_p]
_kernel32.LocalFree.restype = ctypes.c_void_p

_advapi32.OpenProcessToken.argtypes = [_HANDLE, _DWORD, ctypes.POINTER(_HANDLE)]
_advapi32.OpenProcessToken.restype = _BOOL
_advapi32.GetTokenInformation.argtypes = [_HANDLE, ctypes.c_int, ctypes.c_void_p, _DWORD, ctypes.POINTER(_DWORD)]
_advapi32.GetTokenInformation.restype = _BOOL
_advapi32.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_wchar_p)]
_advapi32.ConvertSidToStringSidW.restype = _BOOL
_advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [
    ctypes.wintypes.LPCWSTR, _DWORD, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.wintypes.ULONG),
]
_advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW.restype = _BOOL


class _PipeConnection:
    """One end of a connected pipe instance, with the ``recv``/``sendall``/``close`` subset of a socket."""

    def __init__(self, handle: int, server_side: bool) -> None:
        self._handle = handle
        self._server_side = server_side

    def recv(self, size: int) -> bytes:
        """Return up to *size* bytes, or ``b''`` once the other end has closed the pipe."""
        buffer = ctypes.create_string_buffer(size)
        read = _DWORD(0)
        if not _kernel32.ReadFile(self._handle, buffer, size, ctypes.byref(read), None):
            error = ctypes.get_last_error()
            if error in _CLOSED_PIPE_ERRORS:
                return b''
            raise ctypes.WinError(error)

        return buffer.raw[:read.value]

    def sendall(self, data: bytes) -> None:
        sent = 0
        while sent < len(data):
            chunk = data[sent:]
            written = _DWORD(0)
            if not _kernel32.WriteFile(self._handle, chunk, len(chunk), ctypes.byref(written), None):
                raise ctypes.WinError(ctypes.get_last_error())
            sent += written.value

    def close(self) -> None:
        if self._handle is None:
            return
        if self._server_side:
            # Let the client read what was written before the instance is torn down
            _kernel32.FlushFileBuffers(self._handle)
            _kernel32.DisconnectNamedPipe(self._handle)
        _kernel32.CloseHandle(self._handle)
        self._handle = None


class LocalChannelListener:
    """Server end of the channel *name*; each ``accept()`` returns one connected client.

    Raises ``OSError`` when the pipe cannot be created - in particular when
    another process already serves the name.
    """

    def __init__(self, name: str) -> None:
        self._path = _pipe_path(name)
        self._security_descriptor = _user_only_security_descriptor()
        self._security = _SecurityAttributes(ctypes.sizeof(_SecurityAttributes), self._security_descriptor, False)
        self._pending = self._create_instance(first=True)

    def accept(self) -> _PipeConnection:
        """Block until a client connects, and keep the next instance ready for the one after it."""
        handle = self._pending
        connected = _kernel32.ConnectNamedPipe(handle, None)
        # A client that connected between CreateNamedPipeW and ConnectNamedPipe is reported as an error, but is connected
        error = 0 if connected else ctypes.get_last_error()
        self._pending = self._create_instance(first=False)
        if error not in (0, _ERROR_PIPE_CONNECTED):
            _kernel32.CloseHandle(handle)
            raise ctypes.WinError(error)

        return _PipeConnection(handle, server_side=True)

    def _create_instance(self, first: bool) -> int:
        open_mode = _PIPE_ACCESS_DUPLEX | (_FILE_FLAG_FIRST_PIPE_INSTANCE if first else 0)
        pipe_mode = _PIPE_TYPE_BYTE | _PIPE_READMODE_BYTE | _PIPE_WAIT | _PIPE_REJECT_REMOTE_CLIENTS
        handle = _kernel32.CreateNamedPipeW(
            self._path, open_mode, pipe_mode, _PIPE_UNLIMITED_INSTANCES, _BUFFER_SIZE, _BUFFER_SIZE, 0, ctypes.byref(self._security),
        )
        if handle == _INVALID_HANDLE_VALUE:
            raise ctypes.WinError(ctypes.get_last_error())

        return handle


def open_local_channel(name: str) -> _PipeConnection:
    """Connect to the channel *name*.

    Raises ``OSError`` when nothing serves it, or when the process serving it
    belongs to another user.
    """
    path = _pipe_path(name)
    handle = _kernel32.CreateFileW(path, _GENERIC_READ | _GENERIC_WRITE, 0, None, _OPEN_EXISTING, _CLIENT_SECURITY_FLAGS, None)
    if handle == _INVALID_HANDLE_VALUE and ctypes.get_last_error() == _ERROR_PIPE_BUSY:
        _kernel32.WaitNamedPipeW(path, _CONNECT_WAIT_MS)
        handle = _kernel32.CreateFileW(path, _GENERIC_READ | _GENERIC_WRITE, 0, None, _OPEN_EXISTING, _CLIENT_SECURITY_FLAGS, None)
    if handle == _INVALID_HANDLE_VALUE:
        raise ctypes.WinError(ctypes.get_last_error())

    connection = _PipeConnection(handle, server_side=False)
    if not _served_by_current_user(handle):
        connection.close()
        raise PermissionError(f'{path} is served by a process of another user')

    return connection


def _pipe_path(name: str) -> str:
    return f'{_PIPE_PREFIX}{name}-{_current_user_sid()}'


def _user_only_security_descriptor() -> int:
    """Return a security descriptor granting full access to the current user and to nobody else.

    ``P`` protects the DACL from inheritance, so no default entry - Windows
    grants Everyone read access to a named pipe by default - sneaks back in.
    The descriptor is never freed: the listener needs it for every instance
    it creates, for the rest of the process lifetime.
    """
    descriptor = ctypes.c_void_p()
    sddl = f'D:P(A;;GA;;;{_current_user_sid()})'
    if not _advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW(sddl, _SDDL_REVISION_1, ctypes.byref(descriptor), None):
        raise ctypes.WinError(ctypes.get_last_error())

    return descriptor.value


def _current_user_sid() -> str:
    return _process_user_sid(_kernel32.GetCurrentProcess())


def _served_by_current_user(pipe_handle: int) -> bool:
    """Return True when the process serving *pipe_handle* runs as the current user."""
    server_pid = ctypes.wintypes.ULONG(0)
    if not _kernel32.GetNamedPipeServerProcessId(pipe_handle, ctypes.byref(server_pid)):
        return False

    process = _kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, server_pid.value)
    if not process:
        return False
    try:
        return _process_user_sid(process) == _current_user_sid()
    except OSError:
        return False
    finally:
        _kernel32.CloseHandle(process)


def _process_user_sid(process: int) -> str:
    """Return the SID of the user a process runs as, in its ``S-1-5-...`` string form."""
    token = _HANDLE()
    if not _advapi32.OpenProcessToken(process, _TOKEN_QUERY, ctypes.byref(token)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        needed = _DWORD(0)
        _advapi32.GetTokenInformation(token, _TOKEN_USER, None, 0, ctypes.byref(needed))
        if ctypes.get_last_error() != _ERROR_INSUFFICIENT_BUFFER:
            raise ctypes.WinError(ctypes.get_last_error())
        buffer = ctypes.create_string_buffer(needed.value)
        if not _advapi32.GetTokenInformation(token, _TOKEN_USER, buffer, needed, ctypes.byref(needed)):
            raise ctypes.WinError(ctypes.get_last_error())

        user = _SidAndAttributes.from_buffer(buffer)
        sid_string = ctypes.c_wchar_p()
        if not _advapi32.ConvertSidToStringSidW(user.Sid, ctypes.byref(sid_string)):
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            return str(sid_string.value)
        finally:
            _kernel32.LocalFree(ctypes.cast(sid_string, ctypes.c_void_p))
    finally:
        _kernel32.CloseHandle(token)

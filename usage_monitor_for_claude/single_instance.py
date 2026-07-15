"""
Single-Instance Guard
======================

Prevents multiple instances from running simultaneously using a named
Win32 mutex.  The holder's PID and version are stored in page-file-backed
shared memory so that a new instance can identify and terminate it
regardless of executable name.
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes
import os
import struct

from . import __version__
from .i18n import T
from .instance_id import config_dir_suffix

__all__ = ['ensure_single_instance', 'release_instance_lock']

_MUTEX_BASE_NAME = 'UsageMonitorForClaude_SingleInstance'
_PID_MAPPING_BASE_NAME = 'UsageMonitorForClaude_HolderPID'
_ERROR_ALREADY_EXISTS = 0xB7
_INVALID_HANDLE = ctypes.c_void_p(-1).value
_PAGE_READWRITE = 0x04
_FILE_MAP_READ = 0x0004
_FILE_MAP_WRITE = 0x0002

# Shared memory layout: 4-byte PID + null-terminated UTF-8 version string.
# 64 bytes is plenty for a PID and a version like "1.10.0".
_SHARED_MEM_SIZE = 64

# use_last_error=True captures GetLastError() immediately after each
# FFI call into a ctypes-private thread-local, before Python can run
# any intervening code that might reset it.
_kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)

_kernel32.CreateMutexW.argtypes = [ctypes.wintypes.LPCVOID, ctypes.wintypes.BOOL, ctypes.wintypes.LPCWSTR]
_kernel32.CreateMutexW.restype = ctypes.wintypes.HANDLE

_kernel32.CloseHandle.argtypes = [ctypes.wintypes.HANDLE]
_kernel32.CloseHandle.restype = ctypes.wintypes.BOOL

_kernel32.CreateFileMappingW.argtypes = [
    ctypes.wintypes.HANDLE, ctypes.wintypes.LPCVOID, ctypes.wintypes.DWORD,
    ctypes.wintypes.DWORD, ctypes.wintypes.DWORD, ctypes.wintypes.LPCWSTR,
]
_kernel32.CreateFileMappingW.restype = ctypes.wintypes.HANDLE

_kernel32.OpenFileMappingW.argtypes = [ctypes.wintypes.DWORD, ctypes.wintypes.BOOL, ctypes.wintypes.LPCWSTR]
_kernel32.OpenFileMappingW.restype = ctypes.wintypes.HANDLE

_kernel32.MapViewOfFile.argtypes = [
    ctypes.wintypes.HANDLE, ctypes.wintypes.DWORD, ctypes.wintypes.DWORD, ctypes.wintypes.DWORD, ctypes.c_size_t,
]
_kernel32.MapViewOfFile.restype = ctypes.c_void_p

_kernel32.UnmapViewOfFile.argtypes = [ctypes.c_void_p]
_kernel32.UnmapViewOfFile.restype = ctypes.wintypes.BOOL

_kernel32.OpenProcess.argtypes = [ctypes.wintypes.DWORD, ctypes.wintypes.BOOL, ctypes.wintypes.DWORD]
_kernel32.OpenProcess.restype = ctypes.wintypes.HANDLE

_kernel32.TerminateProcess.argtypes = [ctypes.wintypes.HANDLE, ctypes.wintypes.UINT]
_kernel32.TerminateProcess.restype = ctypes.wintypes.BOOL

_kernel32.WaitForSingleObject.argtypes = [ctypes.wintypes.HANDLE, ctypes.wintypes.DWORD]
_kernel32.WaitForSingleObject.restype = ctypes.wintypes.DWORD

# Handles kept alive for the process lifetime; released on exit or
# explicitly via release_instance_lock().
_mutex_handle: int | None = None
_pid_mapping_handle: int | None = None


def _object_names() -> tuple[str, str]:
    """Return the per-instance ``(mutex_name, pid_mapping_name)`` pair.

    The names carry a config-dir suffix so one monitor instance per
    Claude account can run concurrently, each a singleton for its own
    config directory.
    """
    suffix = config_dir_suffix()
    return _MUTEX_BASE_NAME + suffix, _PID_MAPPING_BASE_NAME + suffix


def _store_holder_info() -> None:
    """Store our PID and version in named shared memory.

    The shared memory is backed by the page file (no disk I/O) and is
    automatically released when this process terminates.
    """
    global _pid_mapping_handle
    _pid_mapping_handle = _kernel32.CreateFileMappingW(
        _INVALID_HANDLE, None, _PAGE_READWRITE, 0, _SHARED_MEM_SIZE, _object_names()[1],
    )
    if not _pid_mapping_handle:
        return

    view = _kernel32.MapViewOfFile(_pid_mapping_handle, _FILE_MAP_WRITE, 0, 0, _SHARED_MEM_SIZE)
    if not view:
        return

    version_bytes = __version__.encode('utf-8')[:_SHARED_MEM_SIZE - 5]
    payload = struct.pack(f'<I{len(version_bytes) + 1}s', os.getpid(), version_bytes + b'\x00')
    ctypes.memmove(view, payload, len(payload))
    _kernel32.UnmapViewOfFile(view)


def _read_holder_info() -> tuple[int | None, str | None]:
    """Read PID and version of the mutex-holding instance from shared memory.

    Returns
    -------
    tuple[int | None, str | None]
        ``(pid, version)`` of the holder, or ``(None, None)`` if the
        shared memory does not exist.
    """
    mapping = _kernel32.OpenFileMappingW(_FILE_MAP_READ, False, _object_names()[1])
    if not mapping:
        return None, None

    view = _kernel32.MapViewOfFile(mapping, _FILE_MAP_READ, 0, 0, _SHARED_MEM_SIZE)
    if not view:
        _kernel32.CloseHandle(mapping)
        return None, None

    raw = ctypes.string_at(view, _SHARED_MEM_SIZE)
    _kernel32.UnmapViewOfFile(view)
    _kernel32.CloseHandle(mapping)

    if len(raw) < 5:
        return None, None

    pid = struct.unpack('<I', raw[:4])[0]
    version = raw[4:].split(b'\x00', 1)[0].decode('utf-8', errors='replace') or None
    return pid if pid else None, version


def _terminate_pid(pid: int) -> None:
    """Terminate a process by PID and wait until it is fully dead.

    Uses OpenProcess + TerminateProcess + WaitForSingleObject so the
    process has released all kernel objects (mutexes, handles) before
    this function returns.
    """
    PROCESS_TERMINATE = 0x0001
    PROCESS_SYNCHRONIZE = 0x00100000

    handle = _kernel32.OpenProcess(PROCESS_TERMINATE | PROCESS_SYNCHRONIZE, False, pid)
    if not handle:
        return

    if not _kernel32.TerminateProcess(handle, 1):
        _kernel32.CloseHandle(handle)
        return

    _kernel32.WaitForSingleObject(handle, 5000)
    _kernel32.CloseHandle(handle)


def ensure_single_instance() -> bool:
    """Ensure only one instance of the application is running.

    If another instance holds the mutex, shows a dialog asking the user
    whether to replace it.  The dialog title includes the running
    instance's version when available.

    Returns
    -------
    bool
        True if this instance may proceed, False if it should exit.
    """
    global _mutex_handle
    mutex_name = _object_names()[0]
    _mutex_handle = _kernel32.CreateMutexW(None, False, mutex_name)
    if ctypes.get_last_error() != _ERROR_ALREADY_EXISTS:
        _store_holder_info()
        return True

    # Another instance is running - ask the user.
    MB_YESNO = 0x04
    MB_ICONQUESTION = 0x20
    MB_TOPMOST = 0x40000
    IDYES = 6

    holder_pid, running_version = _read_holder_info()

    title = T['popup_title']
    if running_version:
        title += f' v{running_version}'

    message = T['already_running'].format(
        running_version=running_version or '?',
    )

    answer = ctypes.windll.user32.MessageBoxW(
        None, message, title,
        MB_YESNO | MB_ICONQUESTION | MB_TOPMOST,
    )
    if answer != IDYES:
        _kernel32.CloseHandle(_mutex_handle)
        _mutex_handle = None
        return False

    if holder_pid:
        _terminate_pid(holder_pid)
    _kernel32.CloseHandle(_mutex_handle)

    _mutex_handle = _kernel32.CreateMutexW(None, False, mutex_name)
    _store_holder_info()
    return True


def release_instance_lock() -> None:
    """Release the mutex and shared memory so a new instance can start."""
    global _mutex_handle, _pid_mapping_handle

    if _mutex_handle:
        _kernel32.CloseHandle(_mutex_handle)
        _mutex_handle = None

    if _pid_mapping_handle:
        _kernel32.CloseHandle(_pid_mapping_handle)
        _pid_mapping_handle = None

"""
Linux Single-Instance Guard
============================

Prevents multiple instances from running simultaneously using an advisory
``flock`` on a lock file in the session's runtime directory.  The holder's
PID and version are stored in that file so a new instance can identify and
replace it regardless of executable name.

The lock is held by an open file descriptor for the process lifetime; the
kernel releases it when the process exits, so a crashed instance never
leaves a lock behind that a restart cannot take.
"""
from __future__ import annotations

import errno
import fcntl
import os
import signal
import time
from pathlib import Path

from .. import __version__
from ..i18n import T
from ..instance_id import config_dir_suffix
from .linux import ask_yes_no, show_topmost_error

__all__ = ['ensure_single_instance', 'release_instance_lock']

_LOCK_BASE_NAME = 'usage-monitor-for-claude'

# Seconds to wait for a terminated holder to actually exit before giving up.
_TERMINATE_TIMEOUT = 5.0
_TERMINATE_POLL = 0.1

# File descriptor kept open for the process lifetime; releasing it drops the
# lock.  Released on exit or explicitly via release_instance_lock().
_lock_fd: int | None = None


def _lock_directory() -> Path:
    """Return the directory the lock file lives in.

    ``XDG_RUNTIME_DIR`` is the correct home for runtime state - it is
    per-user, on tmpfs, and cleared at logout.  Sessions without it (some
    minimal or containerised setups) fall back to the user's cache
    directory, which is still per-user and writable.
    """
    runtime_dir = os.environ.get('XDG_RUNTIME_DIR')
    if runtime_dir:
        return Path(runtime_dir)

    return Path(os.environ.get('XDG_CACHE_HOME') or Path.home() / '.cache')


def _lock_path() -> Path:
    """Return the per-instance lock file path.

    The name carries a config-dir suffix so one monitor instance per Claude
    account can run concurrently, each a singleton for its own config
    directory.
    """
    return _lock_directory() / f'{_LOCK_BASE_NAME}{config_dir_suffix()}.lock'


def _read_holder_info() -> tuple[int | None, str | None]:
    """Read PID and version of the lock-holding instance.

    Returns
    -------
    tuple[int | None, str | None]
        ``(pid, version)`` of the holder, or ``(None, None)`` when the file
        is missing or malformed.  ``flock`` is advisory, so the file stays
        readable while another process holds the lock.
    """
    try:
        raw = _lock_path().read_text(encoding='utf-8', errors='replace')
    except OSError:
        return None, None

    lines = raw.splitlines()
    if not lines:
        return None, None

    try:
        pid = int(lines[0].strip())
    except ValueError:
        return None, None

    version = lines[1].strip() if len(lines) > 1 else ''

    return pid or None, version or None


def _store_holder_info(fd: int) -> None:
    """Write our PID and version into the locked file."""
    payload = f'{os.getpid()}\n{__version__}\n'.encode('utf-8')
    os.ftruncate(fd, 0)
    os.lseek(fd, 0, os.SEEK_SET)
    os.write(fd, payload)
    os.fsync(fd)


def _acquire(path: Path) -> int | None:
    """Open *path* and take the exclusive lock, or None if held elsewhere.

    The descriptor is returned still open and locked; the caller owns it.
    """
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    except OSError:
        return None

    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as error:
        os.close(fd)
        if error.errno in (errno.EACCES, errno.EAGAIN):
            return None
        raise

    return fd


def _process_is_alive(pid: int) -> bool:
    """Return True if a process with *pid* exists and is reachable."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # Owned by another user: it exists, we just may not signal it.
        return True

    return True


def _terminate_pid(pid: int) -> None:
    """Ask a process to exit and wait until it is gone.

    Sends ``SIGTERM`` so the holder can release its tray icon and windows,
    then escalates to ``SIGKILL`` if it is still alive when the timeout
    expires.  Returning does not guarantee the process died - the caller
    proves that by retaking the lock.
    """
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        return

    deadline = time.monotonic() + _TERMINATE_TIMEOUT
    while time.monotonic() < deadline:
        if not _process_is_alive(pid):
            return
        time.sleep(_TERMINATE_POLL)

    try:
        os.kill(pid, signal.SIGKILL)
    except OSError:
        return

    deadline = time.monotonic() + _TERMINATE_TIMEOUT
    while time.monotonic() < deadline and _process_is_alive(pid):
        time.sleep(_TERMINATE_POLL)


def ensure_single_instance() -> bool:
    """Ensure only one instance of the application is running.

    If another instance holds the lock, shows a dialog asking the user
    whether to replace it.  The dialog title includes the running
    instance's version when available.

    Returns
    -------
    bool
        True if this instance may proceed, False if it should exit.
    """
    global _lock_fd

    path = _lock_path()
    fd = _acquire(path)
    if fd is not None:
        _lock_fd = fd
        _store_holder_info(fd)
        return True

    holder_pid, running_version = _read_holder_info()

    title = T['popup_title']
    if running_version:
        title += f' v{running_version}'

    if not ask_yes_no(T['already_running'].format(running_version=running_version or '?'), title):
        return False

    # Re-read the holder after the dialog: it can stay open for a long time,
    # the old instance may have exited meanwhile, and the kernel recycles
    # PIDs - terminating the snapshotted PID could kill an unrelated process.
    current_holder_pid, _ = _read_holder_info()
    if holder_pid and current_holder_pid == holder_pid:
        _terminate_pid(holder_pid)

    # Retaking the lock is the ground truth for whether the old instance is
    # really gone: the kernel only frees it when that process exits.
    fd = _acquire(path)
    if fd is None:
        show_topmost_error(T['replace_failed'], title)
        return False

    _lock_fd = fd
    _store_holder_info(fd)

    return True


def release_instance_lock() -> None:
    """Release the lock so a new instance can start.

    The file itself is deliberately left behind.  Unlinking it would let a
    second process create a fresh file and lock that, while a third still
    holds a lock on the unlinked inode - two instances, each convinced it is
    the only one.  A stale file costs nothing: the lock lives in the open
    descriptor, so the next start takes it without resistance.
    """
    global _lock_fd

    if _lock_fd is None:
        return

    try:
        fcntl.flock(_lock_fd, fcntl.LOCK_UN)
    except OSError:
        pass

    try:
        os.close(_lock_fd)
    except OSError:
        pass

    _lock_fd = None

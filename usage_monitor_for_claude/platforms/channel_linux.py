"""
Linux Local Channel
====================

A Unix domain socket that connects the ``--mcp`` bridge process to the
running tray application.

The socket lives in ``$XDG_RUNTIME_DIR``, a directory only the user can
enter, and is itself ``0600``.  It stays in place when the application exits
and is replaced on the next start once nobody answers on it, while a file that
is not a socket is never touched.  It is not removed at exit because the old
process of a Restart exits after the new one has created its socket, and
nothing about the file tells the two apart: tmpfs and overlayfs reuse inode
numbers at once.
"""
from __future__ import annotations

import errno
import os
import socket
import stat
from pathlib import Path

from .linux import runtime_directory

__all__ = ['LocalChannelListener', 'open_local_channel']

_BACKLOG = 8


class LocalChannelListener:
    """Server end of the channel *name*; each ``accept()`` returns one connected client.

    Raises ``OSError`` when the socket cannot be created - in particular when
    another process already serves the name.
    """

    def __init__(self, name: str) -> None:
        path = _socket_path(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        _remove_stale_socket(path)

        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            listener.bind(str(path))
            # A new socket file takes 0777 minus the umask; connecting needs write access, so 0600 keeps every other user out
            os.chmod(path, 0o600)
            listener.listen(_BACKLOG)
        except OSError:
            listener.close()
            raise

        self._listener = listener

    def accept(self) -> socket.socket:
        connection, _ = self._listener.accept()
        return connection


def open_local_channel(name: str) -> socket.socket:
    """Connect to the channel *name*; raises ``OSError`` when nothing serves it."""
    connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        connection.connect(str(_socket_path(name)))
    except OSError:
        connection.close()
        raise

    return connection


def _socket_path(name: str) -> Path:
    return runtime_directory() / f'{name}.sock'


def _remove_stale_socket(path: Path) -> None:
    """Remove a socket nobody serves any more; raise when it is in use or is not a socket."""
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError:
        return

    if not stat.S_ISSOCK(mode):
        raise FileExistsError(errno.EEXIST, 'not a socket, left untouched', str(path))

    probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        probe.connect(str(path))
    except ConnectionRefusedError:
        path.unlink()
        return
    finally:
        probe.close()

    raise OSError(errno.EADDRINUSE, 'already served by another process', str(path))

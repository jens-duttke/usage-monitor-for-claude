"""
MCP Channel Framing
====================

What both ends of the local MCP channel share: its name, the line framing
and the rule which messages get an answer.

The ``--mcp`` bridge imports this module and the server side alike.  It
imports nothing but the standard library and ``instance_id``: the bridge is
started by Claude without a window, and ``settings`` shows a dialog for an
invalid settings file at import time, which would block that process before
it reads its first message.
"""
from __future__ import annotations

import json
from typing import Protocol

from .instance_id import config_dir_suffix

__all__ = ['MAX_MESSAGE_BYTES', 'Connection', 'LineReader', 'expects_answer', 'mcp_channel_name']

MAX_MESSAGE_BYTES = 64 * 1024

_CHANNEL_BASE_NAME = 'usage-monitor-for-claude-mcp'
_RECEIVE_CHUNK_BYTES = 4096


class Connection(Protocol):
    """The part of a socket a channel connection offers."""

    def recv(self, size: int) -> bytes: ...

    def sendall(self, data: bytes) -> None: ...

    def close(self) -> None: ...


def mcp_channel_name() -> str:
    """Return the channel name of this instance; each ``--config-dir`` account gets its own."""
    return _CHANNEL_BASE_NAME + config_dir_suffix()


def expects_answer(line: bytes) -> bool:
    """Return whether the server answers *line*: everything except a well-formed notification.

    The bridge relies on this to know whether to wait for a reply, so the
    server decides with the very same function.
    """
    try:
        message = json.loads(line)
    except (ValueError, RecursionError):
        return True

    return not (isinstance(message, dict) and 'id' not in message and isinstance(message.get('method'), str))


class LineReader:
    """Splits what arrives on a connection into messages, one per line."""

    def __init__(self, connection: Connection) -> None:
        self._connection = connection
        self._buffer = b''

    def readline(self) -> bytes | None:
        """Return the next message without its line ending, or None once the other end has closed.

        Raises ``ValueError`` for a line longer than ``MAX_MESSAGE_BYTES``,
        which would otherwise grow the buffer without bound.
        """
        while b'\n' not in self._buffer:
            if len(self._buffer) > MAX_MESSAGE_BYTES:
                raise ValueError('message too long')
            chunk = self._connection.recv(_RECEIVE_CHUNK_BYTES)
            if not chunk:
                return None
            self._buffer += chunk

        line, self._buffer = self._buffer.split(b'\n', 1)
        if len(line) > MAX_MESSAGE_BYTES:
            raise ValueError('message too long')

        return line.rstrip(b'\r')

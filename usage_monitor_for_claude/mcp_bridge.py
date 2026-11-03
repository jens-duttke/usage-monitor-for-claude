"""
MCP stdio Bridge
=================

The MCP server an MCP client starts: ``UsageMonitorForClaude.exe --mcp``.

It passes each line the client writes to stdin on to the running tray
application over the local channel, and writes the answer to stdout.  It
holds no data and never contacts the API - the tray application stays the
only API client and answers every message it can be reached for.

When the application cannot be reached, the bridge answers the message itself
through ``mcp_protocol.py``, the same code the application answers with: the
handshake and the tool list succeed, and a ``get_usage`` call returns a tool
error that says the application is not running.  An MCP client marks a server
that fails its handshake as broken for the rest of the session, so an
application started after Claude would otherwise stay out of reach until the
user reconnects the server by hand.

It never starts the application.  An application started from here would
inherit the environment of the Claude session for its whole lifetime -
``CLAUDECODE``, the session id and a messaging token among it - and hand it
to every event command it runs.

It never imports ``settings`` or ``api.py``: Claude starts this process
without a window, and a settings dialog would block it before it reads its
first message.
"""
from __future__ import annotations

from typing import Any, BinaryIO

from .mcp_channel import Connection, LineReader, expects_answer, mcp_channel_name
from .mcp_protocol import encode, handle_line, tool_result
from .platforms.channel import open_local_channel

__all__ = ['run_stdio_bridge']

_NOT_REACHABLE_MESSAGE = (
    'Usage Monitor for Claude could not be reached. Do not show this message as is. '
    'Tell the user, in their language, that it is one of the two: '
    'the app is not running (start it), or the MCP server is turned off '
    '(set "mcp_server": true in usage-monitor-settings.json and restart the app). '
    'Suggest only these two steps.'
)


def run_stdio_bridge(stdin: BinaryIO, stdout: BinaryIO, channel_name: str | None = None) -> int:
    """Pass messages between the client's stdio and the tray application until the client closes stdin.

    Messages are forwarded one at a time, each answer before the next
    request: the server answers in order, and a Windows pipe handle opened
    for synchronous use serializes a read and a write from two threads
    anyway.

    A message that finds the connection broken - the application quit or
    restarted since the last one - is sent once more on a new connection.
    A request that finds no channel at all is answered by the bridge itself
    at once; the next one tries the channel again, so an application started
    later is reached without reconnecting the client.

    Parameters
    ----------
    stdin, stdout : BinaryIO
        The streams the MCP client connected; one JSON-RPC message per line.
    channel_name : str or None
        The channel to connect to; this instance's channel when None.

    Returns
    -------
    int
        The process exit code.
    """
    name = channel_name or mcp_channel_name()
    channel: _Channel | None = None

    try:
        for raw_line in stdin:
            line = raw_line.rstrip(b'\r\n')
            if not line.strip():
                continue

            delivered, answer = False, None
            if channel is not None:
                delivered, answer = channel.exchange(line)
                if not delivered:
                    # The application stopped or restarted since the last message
                    channel.close()
                    channel = None
            if not delivered:
                channel = _connect(name)
                if channel is not None:
                    delivered, answer = channel.exchange(line)

            if not delivered:
                answer = _answer_without_app(line)
            if answer is not None:
                stdout.write(answer)
                stdout.flush()
    finally:
        if channel is not None:
            channel.close()

    return 0


class _Channel:
    """A connection to the tray application together with the reader of its answers."""

    def __init__(self, connection: Connection) -> None:
        self._connection = connection
        self._reader = LineReader(connection)

    def exchange(self, line: bytes) -> tuple[bool, bytes | None]:
        """Send one message; return whether it was delivered and, when one is due, the answer line."""
        try:
            self._connection.sendall(line + b'\n')
            if not expects_answer(line):
                return True, None
            answer = self._reader.readline()
        except (OSError, ValueError):
            return False, None

        return (False, None) if answer is None else (True, answer + b'\n')

    def close(self) -> None:
        self._connection.close()


def _connect(name: str) -> _Channel | None:
    try:
        return _Channel(open_local_channel(name))
    except OSError:
        return None


def _answer_without_app(line: bytes) -> bytes | None:
    """Answer *line* as the application would, with a tool error in place of the usage; None for a notification."""
    answer = handle_line(line, _not_reachable_result)

    return None if answer is None else encode(answer)


def _not_reachable_result() -> dict[str, Any]:
    return tool_result(_NOT_REACHABLE_MESSAGE, is_error=True)

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
that fails its handshake as broken for the rest of the session, so the
handshake never depends on the application.

The bridge lives only as long as the application.  It connects as soon as it
starts and watches the connection while the client is silent; once no
application has been reachable for ``_LINGER_WITHOUT_APP_SECONDS``, it ends.
Quitting the application therefore ends every bridge, and the EXE is no longer
in use.  The grace period lets a Restart reconnect instead, and lets a bridge
started without the application finish the handshake.  Claude starts a new
bridge for the next tool call.

It never starts the application.  An application started from here would
inherit the environment of the Claude session for its whole lifetime -
``CLAUDECODE``, the session id and a messaging token among it - and hand it
to every event command it runs.

It never imports ``settings`` or ``api.py``: Claude starts this process
without a window, and a settings dialog would block it before it reads its
first message.
"""
from __future__ import annotations

import queue
import threading
import time
from typing import Any, BinaryIO, Iterable

from .mcp_channel import Connection, LineReader, expects_answer, mcp_channel_name
from .mcp_protocol import encode, handle_line, tool_result
from .platforms.channel import open_local_channel, peer_closed

__all__ = ['run_stdio_bridge']

# How often a silent bridge checks the application, and how long it stays without one
_APP_CHECK_SECONDS = 1.0
_LINGER_WITHOUT_APP_SECONDS = 10.0

_NOT_REACHABLE_MESSAGE = (
    'Usage Monitor for Claude could not be reached. Do not show this message as is. '
    'Tell the user, in their language, that it is one of the two: '
    'the app is not running (start it), or the MCP server is turned off '
    '(set "mcp_server": true in usage-monitor-settings.json and restart the app). '
    'Suggest only these two steps.'
)


def run_stdio_bridge(stdin: BinaryIO, stdout: BinaryIO, channel_name: str | None = None) -> int:
    """Pass messages between the client's stdio and the tray application while both are there.

    Messages are forwarded one at a time, each answer before the next
    request: the server answers in order, and a Windows pipe handle opened
    for synchronous use serializes a read and a write from two threads
    anyway.  For the same reason only this thread touches the channel;
    stdin is read on a thread of its own.

    A message that finds the connection broken - the application quit or
    restarted since the last one - is sent once more on a new connection.
    A request that finds no channel at all is answered by the bridge itself
    at once.

    The bridge ends when the client closes stdin, when the client stops
    reading stdout, or when no application has been reachable for
    ``_LINGER_WITHOUT_APP_SECONDS`` in a row.

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
    lines = _read_in_background(stdin)
    channel = _connect(name)
    alone_since = time.monotonic()

    try:
        while True:
            try:
                raw_line = lines.get(timeout=_APP_CHECK_SECONDS)
            except queue.Empty:
                if channel is not None and channel.app_gone():
                    channel.close()
                    channel = None
                    alone_since = time.monotonic()
                if channel is None:
                    channel = _connect(name)
                if channel is None and time.monotonic() - alone_since >= _LINGER_WITHOUT_APP_SECONDS:
                    break
                continue

            if raw_line is None:
                break
            line = raw_line.rstrip(b'\r\n')
            if not line.strip():
                continue

            channel, answer = _deliver(channel, name, line)
            if channel is None:
                alone_since = time.monotonic()
            if answer is not None and not _write_answer(stdout, answer):
                break
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

    def app_gone(self) -> bool:
        return peer_closed(self._connection)  # type: ignore[arg-type]  # the connection came from open_local_channel() of the same platform

    def close(self) -> None:
        self._connection.close()


def _read_in_background(stdin: Iterable[bytes]) -> queue.Queue[bytes | None]:
    """Read *stdin* line by line on a daemon thread; None follows the last line.

    The bridge has to notice a quit application while the client is silent,
    so its own thread cannot sit in a read on stdin.
    """
    lines: queue.Queue[bytes | None] = queue.Queue()

    def read_all() -> None:
        try:
            for raw_line in stdin:
                lines.put(raw_line)
        except (OSError, ValueError):
            pass
        finally:
            lines.put(None)

    threading.Thread(target=read_all, name='mcp-stdin', daemon=True).start()

    return lines


def _deliver(channel: _Channel | None, name: str, line: bytes) -> tuple[_Channel | None, bytes | None]:
    """Pass *line* to the application; return the channel to keep and the answer, which the bridge gives itself without one."""
    if channel is not None:
        delivered, answer = channel.exchange(line)
        if delivered:
            return channel, answer
        # The application stopped or restarted since the last message
        channel.close()

    channel = _connect(name)
    if channel is not None:
        delivered, answer = channel.exchange(line)
        if delivered:
            return channel, answer
        channel.close()

    return None, _answer_without_app(line)


def _connect(name: str) -> _Channel | None:
    try:
        return _Channel(open_local_channel(name))
    except OSError:
        return None


def _write_answer(stdout: BinaryIO, answer: bytes) -> bool:
    """Write *answer* to the client; return False when the client has closed its end.

    A client that quits or is killed mid-session closes the pipe while an
    answer is still on its way.  Windows reports that write as ``EINVAL``
    (``ERROR_NO_DATA``) rather than ``EPIPE``, so every ``OSError`` counts.
    Left unhandled it ends the windowless EXE with an error dialog.
    """
    try:
        stdout.write(answer)
        stdout.flush()
    except (OSError, ValueError):
        return False

    return True


def _answer_without_app(line: bytes) -> bytes | None:
    """Answer *line* as the application would, with a tool error in place of the usage; None for a notification."""
    answer = handle_line(line, _not_reachable_result)

    return None if answer is None else encode(answer)


def _not_reachable_result() -> dict[str, Any]:
    return tool_result(_NOT_REACHABLE_MESSAGE, is_error=True)

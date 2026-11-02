"""
MCP stdio Bridge
=================

The MCP server an MCP client starts: ``UsageMonitorForClaude.exe --mcp``.

It passes each line the client writes to stdin on to the running tray
application over the local channel, and writes the answer to stdout.  It
holds no data, decides no protocol rule and never contacts the API - the
tray application stays the only API client and answers every message itself.
When the application is not running, the bridge starts it.
"""
from __future__ import annotations

import json
import time
from typing import Any, BinaryIO, Callable

from .launch import app_command
from .mcp_server import Connection, LineReader, expects_answer, mcp_channel_name
from .platforms import start_detached
from .platforms.channel import open_local_channel

__all__ = ['run_stdio_bridge', 'start_app_in_background']

_INTERNAL_ERROR = -32603

# The longest a request waits for the application to open its channel: a
# frozen build unpacks itself and starts its GUI first.  The wait ends as soon
# as the channel answers.
_APP_WAIT_SECONDS = 30.0
_APP_POLL_SECONDS = 0.25


def run_stdio_bridge(stdin: BinaryIO, stdout: BinaryIO, channel_name: str | None = None, launch_app: Callable[[], None] | None = None) -> int:
    """Pass messages between the client's stdio and the tray application until the client closes stdin.

    Messages are forwarded one at a time, each answer before the next
    request: the server answers in order, and a Windows pipe handle opened
    for synchronous use serializes a read and a write from two threads
    anyway.

    A request that finds no application starts it through ``launch_app`` and
    waits up to ``_APP_WAIT_SECONDS`` for its channel; an instance started
    during a Restart exits by itself.  After a wait that ended without the
    application, later requests still start it but do not wait, so an
    application that cannot start costs each call an error, not the whole
    wait, until a request reaches it again.

    Parameters
    ----------
    stdin, stdout : BinaryIO
        The streams the MCP client connected; one JSON-RPC message per line.
    channel_name : str or None
        The channel to connect to; this instance's channel when None.
    launch_app : callable or None
        Starts the application in the background; None never starts it.

    Returns
    -------
    int
        The process exit code.
    """
    name = channel_name or mcp_channel_name()
    channel: _Channel | None = None
    waits_for_app = True

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
                if channel is None and expects_answer(line):
                    if launch_app is not None:
                        launch_app()
                    if waits_for_app:
                        channel = _wait_for_channel(name)
                    waits_for_app = channel is not None
                if channel is not None:
                    waits_for_app = True
                    delivered, answer = channel.exchange(line)

            if not delivered and expects_answer(line):
                answer = _encode(_not_running_error(line))
            if answer is not None:
                stdout.write(answer)
                stdout.flush()
    finally:
        if channel is not None:
            channel.close()

    return 0


def start_app_in_background() -> None:
    """Start the tray application for the account this bridge serves, detached from the bridge.

    The new process inherits the bridge's environment, ``CLAUDE_CONFIG_DIR``
    included, so it opens the very channel the bridge connects to.  With
    ``--exit-if-running`` it ends without a dialog when another instance is
    starting at the same time, e.g. one started by a second Claude session.
    """
    command, env = app_command(['--exit-if-running'])
    try:
        start_detached(command, env)
    except OSError:
        # The request then waits for nothing and reports that the application is not running
        pass


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


def _wait_for_channel(name: str) -> _Channel | None:
    deadline = time.monotonic() + _APP_WAIT_SECONDS
    while time.monotonic() < deadline:
        channel = _connect(name)
        if channel is not None:
            return channel
        time.sleep(_APP_POLL_SECONDS)

    return None


def _not_running_error(line: bytes) -> dict[str, Any]:
    try:
        message = json.loads(line)
    except (ValueError, RecursionError):
        message = None
    request_id = message.get('id') if isinstance(message, dict) else None

    return {
        'jsonrpc': '2.0', 'id': request_id,
        'error': {'code': _INTERNAL_ERROR, 'message': 'Usage Monitor for Claude is not running. Start it, then try again.'},
    }


def _encode(message: dict[str, Any]) -> bytes:
    return json.dumps(message, separators=(',', ':')).encode('utf-8') + b'\n'

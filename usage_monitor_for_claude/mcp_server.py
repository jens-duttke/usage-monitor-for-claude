"""
MCP Server
===========

Read-only Model Context Protocol server that tells a local Claude session
how much of its quota is left.

The one tool answers from the application's usage cache, so it never causes
an API request and never touches credentials.  Claude starts the ``--mcp``
bridge (``mcp_bridge.py``), which reaches this server over a local channel
that only the current user can open - a named pipe on Windows, a Unix domain
socket on Linux.

The wire format is that of MCP over stdio: one JSON-RPC message per line.  A
request that carries its protocol version in ``params._meta`` is served as
revision 2026-07-28, without a handshake; ``initialize`` and every request
without that version are served as one of the earlier handshake revisions.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from collections.abc import Callable
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Protocol

from . import __version__
from .formatting import elapsed_pct, expand_popup_fields, field_period
from .instance_id import config_dir_suffix
from .platforms.channel import LocalChannelListener
from .settings import POPUP_FIELDS

if TYPE_CHECKING:
    # Annotations only: the stdio bridge imports this module and must not load api.py, which handles the credentials
    from .cache import CacheSnapshot

__all__ = [
    'MAX_MESSAGE_BYTES', 'Connection', 'LineReader', 'expects_answer', 'handle_line', 'mcp_channel_name', 'start_mcp_server', 'usage_report',
]

log = logging.getLogger(__name__)

MAX_MESSAGE_BYTES = 64 * 1024

_CHANNEL_BASE_NAME = 'usage-monitor-for-claude-mcp'
_SERVER_NAME = 'usage-monitor-for-claude'
_SERVER_INFO = {'name': _SERVER_NAME, 'version': __version__}
_TOOL_NAME = 'get_usage'
_SUPPORTED_PROTOCOL_VERSIONS = ('2026-07-28',)
# Revisions that open with an initialize handshake, newest first; Claude Desktop speaks one of these
_HANDSHAKE_PROTOCOL_VERSIONS = ('2025-11-25', '2025-06-18', '2025-03-26', '2024-11-05')
_INSTRUCTIONS = f'Call {_TOOL_NAME} before starting expensive work, to see how much of the Claude usage quota is left.'

_META_PROTOCOL_VERSION = 'io.modelcontextprotocol/protocolVersion'
_META_CLIENT_CAPABILITIES = 'io.modelcontextprotocol/clientCapabilities'
_META_SERVER_INFO = 'io.modelcontextprotocol/serverInfo'

# The tool list and the discovery result never change while the process runs
_CACHE_TTL_MS = 60 * 60 * 1000
_RECEIVE_CHUNK_BYTES = 4096

# A tool call that arrives before the first fetch has returned waits for it
_FIRST_FETCH_WAIT_SECONDS = 15.0
_FIRST_FETCH_POLL_SECONDS = 0.25

# A restart starts the new process while the old one still serves the channel
_LISTEN_ATTEMPTS = 60
_LISTEN_RETRY_SECONDS = 2.0

_PARSE_ERROR = -32700
_INVALID_REQUEST = -32600
_METHOD_NOT_FOUND = -32601
_INVALID_PARAMS = -32602
_INTERNAL_ERROR = -32603
_UNSUPPORTED_PROTOCOL_VERSION = -32022

_TOOL_DEFINITION = {
    'name': _TOOL_NAME,
    'title': 'Get Claude usage',
    'description': (
        'Returns the Claude usage quotas of the account that the user has chosen to display. For each quota: used_percent (100 means exhausted) and '
        'remaining_percent (100 minus used_percent, never below 0), '
        'resets_at (ISO 8601 in the user\'s local time zone) and resets_in_minutes, both null when no period is running; '
        'resets_in_minutes is negative once the reset time has passed but the new period has not been fetched yet, so used_percent '
        'still shows the period that just ended, '
        'expected_percent (how much of the period has elapsed, counting only working hours where the user has set them, null when '
        'unknown) and vs_expected (used_percent minus expected_percent: above 0 means usage is ahead of the clock, null when unknown). '
        'data_age_seconds tells how old the numbers are. error is set when the last refresh failed, in which case the numbers may be stale.'
    ),
    'inputSchema': {'type': 'object', 'properties': {}, 'additionalProperties': False},
    'annotations': {'readOnlyHint': True, 'idempotentHint': True, 'openWorldHint': False},
}


class Connection(Protocol):
    """The part of a socket a channel connection offers."""

    def recv(self, size: int) -> bytes: ...

    def sendall(self, data: bytes) -> None: ...

    def close(self) -> None: ...


def start_mcp_server(read_snapshot: Callable[[], CacheSnapshot]) -> None:
    """Serve the local channel on a daemon thread for the rest of the process lifetime.

    Never raises: the server is an optional extra, and a channel that cannot
    be opened leaves the rest of the application untouched.

    Parameters
    ----------
    read_snapshot : Callable
        Returns the current cache state; called once per tool call.
    """
    threading.Thread(target=_serve, args=(read_snapshot,), name='mcp-server', daemon=True).start()


def mcp_channel_name() -> str:
    """Return the channel name of this instance; each ``--config-dir`` account gets its own."""
    return _CHANNEL_BASE_NAME + config_dir_suffix()


def usage_report(snapshot: CacheSnapshot) -> dict[str, Any]:
    """Build the ``get_usage`` result from a cache snapshot.

    Parameters
    ----------
    snapshot : CacheSnapshot
        Current cache state.

    Returns
    -------
    dict
        ``quotas`` (one entry per bar the popup shows, in the same order),
        ``data_age_seconds`` (None before the first successful fetch) and
        ``error`` (None unless the last refresh failed).
    """
    quotas = []
    for field in expand_popup_fields(POPUP_FIELDS, snapshot.usage):
        quota = _quota_report(field, snapshot.usage[field])
        if quota is not None:
            quotas.append(quota)

    last_success_time = snapshot.last_success_time
    data_age_seconds = None if last_success_time is None else round(time.time() - last_success_time)

    return {'quotas': quotas, 'data_age_seconds': data_age_seconds, 'error': snapshot.last_error}


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


def handle_line(line: bytes, read_snapshot: Callable[[], CacheSnapshot]) -> dict[str, Any] | None:
    """Answer one JSON-RPC message, or return None for a notification.

    Parameters
    ----------
    line : bytes
        One message, without its newline.
    read_snapshot : Callable
        Returns the current cache state.
    """
    if not expects_answer(line):
        return None

    try:
        message = json.loads(line)
    except (ValueError, RecursionError):
        return _rpc_error(None, _PARSE_ERROR, 'Parse error')
    if not isinstance(message, dict):
        return _rpc_error(None, _INVALID_REQUEST, 'Expected a single JSON-RPC message')

    request_id = message.get('id')
    method = message.get('method')
    # A client must not send JSON-RPC responses, and a request id is a string or an integer, never null
    if not isinstance(method, str) or isinstance(request_id, bool) or not isinstance(request_id, (str, int)):
        return _rpc_error(None, _INVALID_REQUEST, 'Expected a JSON-RPC request with a string or integer id')

    try:
        return _answer_request(request_id, method, message.get('params', {}), read_snapshot)
    except Exception:
        log.exception('MCP request failed')
        return _rpc_error(request_id, _INTERNAL_ERROR, 'Internal error')


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


def _serve(read_snapshot: Callable[[], CacheSnapshot]) -> None:
    """Open the channel, retrying while another process still serves it, and accept clients until the process exits."""
    name = mcp_channel_name()
    for attempt in range(_LISTEN_ATTEMPTS):
        try:
            listener = LocalChannelListener(name)
            break
        except OSError:
            if attempt < _LISTEN_ATTEMPTS - 1:
                time.sleep(_LISTEN_RETRY_SECONDS)
    else:
        log.warning('not started: the channel %s stays in use by another process', name)
        return

    log.info('listening on the local channel %s', name)
    while True:
        try:
            connection = listener.accept()
        except OSError:
            log.debug('MCP client failed to connect', exc_info=True)
            continue
        threading.Thread(target=_serve_connection, args=(connection, read_snapshot), name='mcp-connection', daemon=True).start()


def _serve_connection(connection: Connection, read_snapshot: Callable[[], CacheSnapshot]) -> None:
    """Answer the messages of one client, in order, until it disconnects."""
    reader = LineReader(connection)
    try:
        while True:
            try:
                line = reader.readline()
            except ValueError:
                connection.sendall(_encode(_rpc_error(None, _PARSE_ERROR, f'Message longer than {MAX_MESSAGE_BYTES} bytes')))
                return
            if line is None:
                return
            if not line.strip():
                continue
            answer = handle_line(line, read_snapshot)
            if answer is not None:
                connection.sendall(_encode(answer))
    except OSError:
        log.debug('MCP connection failed', exc_info=True)
    finally:
        connection.close()


def _answer_request(request_id: str | int, method: str, params: Any, read_snapshot: Callable[[], CacheSnapshot]) -> dict[str, Any]:
    meta = params.get('_meta') if isinstance(params, dict) else None
    if method == 'initialize' or not (isinstance(meta, dict) and _META_PROTOCOL_VERSION in meta):
        return _answer_handshake_request(request_id, method, params, read_snapshot)

    protocol_version = meta.get(_META_PROTOCOL_VERSION)
    if not isinstance(protocol_version, str) or not isinstance(meta.get(_META_CLIENT_CAPABILITIES), dict):
        return _rpc_error(request_id, _INVALID_PARAMS, f'params._meta needs {_META_PROTOCOL_VERSION} and {_META_CLIENT_CAPABILITIES}')
    if protocol_version not in _SUPPORTED_PROTOCOL_VERSIONS:
        return _rpc_error(
            request_id, _UNSUPPORTED_PROTOCOL_VERSION, 'Unsupported protocol version',
            data={'supported': list(_SUPPORTED_PROTOCOL_VERSIONS), 'requested': protocol_version},
        )

    if method == 'server/discover':
        return _rpc_result(request_id, {
            'supportedVersions': list(_SUPPORTED_PROTOCOL_VERSIONS), 'capabilities': {'tools': {}}, 'instructions': _INSTRUCTIONS,
            'ttlMs': _CACHE_TTL_MS, 'cacheScope': 'public',
        })
    if method == 'tools/list':
        return _rpc_result(request_id, {'tools': [_TOOL_DEFINITION], 'ttlMs': _CACHE_TTL_MS, 'cacheScope': 'public'})
    if method == 'tools/call':
        invalid = _invalid_tool_call(params)
        if invalid is not None:
            return _rpc_error(request_id, _INVALID_PARAMS, invalid)
        return _rpc_result(request_id, _tool_result(read_snapshot))

    return _rpc_error(request_id, _METHOD_NOT_FOUND, f'Method not found: {method}')


def _answer_handshake_request(request_id: str | int, method: str, params: Any, read_snapshot: Callable[[], CacheSnapshot]) -> dict[str, Any]:
    """Answer a client of the revisions before 2026-07-28, which opens with ``initialize`` and sends no version per request.

    Claude Desktop is such a client.  Nothing is remembered between
    requests: the bridge reconnects when the application restarts, and the
    client does not send ``initialize`` again on the new connection.
    """
    if not isinstance(params, dict):
        return _rpc_error(request_id, _INVALID_PARAMS, 'params must be an object')

    if method == 'initialize':
        requested = params.get('protocolVersion')
        version = requested if requested in _HANDSHAKE_PROTOCOL_VERSIONS else _HANDSHAKE_PROTOCOL_VERSIONS[0]
        return _handshake_result(request_id, {
            'protocolVersion': version, 'capabilities': {'tools': {}}, 'serverInfo': _SERVER_INFO, 'instructions': _INSTRUCTIONS,
        })
    if method == 'ping':
        return _handshake_result(request_id, {})
    if method == 'tools/list':
        return _handshake_result(request_id, {'tools': [_TOOL_DEFINITION]})
    if method == 'tools/call':
        invalid = _invalid_tool_call(params)
        if invalid is not None:
            return _rpc_error(request_id, _INVALID_PARAMS, invalid)
        return _handshake_result(request_id, _tool_result(read_snapshot))

    return _rpc_error(request_id, _METHOD_NOT_FOUND, f'Method not found: {method}')


def _invalid_tool_call(params: dict[str, Any]) -> str | None:
    """Return why a ``tools/call`` cannot be served, or None when it can."""
    if params.get('name') != _TOOL_NAME:
        return f'Unknown tool: {params.get("name")}'
    if params.get('arguments') not in (None, {}):
        return f'{_TOOL_NAME} takes no arguments'

    return None


def _tool_result(read_snapshot: Callable[[], CacheSnapshot]) -> dict[str, Any]:
    return {'content': [{'type': 'text', 'text': json.dumps(usage_report(_settled_snapshot(read_snapshot)))}], 'isError': False}


def _settled_snapshot(read_snapshot: Callable[[], CacheSnapshot]) -> CacheSnapshot:
    """Return the cache state, waiting up to ``_FIRST_FETCH_WAIT_SECONDS`` while the first fetch is still outstanding.

    The bridge starts the application on demand, and the application opens
    its channel before its first fetch has returned: answering then would
    report no quotas at all.  Once one fetch has succeeded or failed, the
    state is returned as it is.
    """
    deadline = time.monotonic() + _FIRST_FETCH_WAIT_SECONDS
    snapshot = read_snapshot()
    while snapshot.last_success_time is None and snapshot.last_error is None and time.monotonic() < deadline:
        time.sleep(_FIRST_FETCH_POLL_SECONDS)
        snapshot = read_snapshot()

    return snapshot


def _quota_report(field: str, entry: dict[str, Any]) -> dict[str, Any] | None:
    """Return the report of one quota, or None when its utilization is not a number."""
    used_percent = entry.get('utilization')
    if isinstance(used_percent, bool) or not isinstance(used_percent, (int, float)):
        return None

    resets_at = entry.get('resets_at') or None
    reset = _parse_time(resets_at)
    period = field_period(field)
    elapsed = elapsed_pct(resets_at, period, field=field) if resets_at and reset and period else None

    # One decimal throughout; the derived values come from the rounded ones, so the numbers
    # the model reads add up (used + remaining = 100, used - expected = vs_expected)
    used = round(used_percent, 1)
    expected_percent = None if elapsed is None else round(elapsed, 1)

    return {
        'field': field,
        'used_percent': used,
        # Both directions, because users phrase limits either way ("stop at 90%", "until 10% is left")
        'remaining_percent': round(100 - used, 1),
        'resets_at': None if reset is None else reset.astimezone().replace(microsecond=0).isoformat(),
        # The model knows today's date but not the time of day, so it cannot work this out from resets_at
        'resets_in_minutes': None if reset is None else round((reset - datetime.now(timezone.utc)).total_seconds() / 60),
        'expected_percent': expected_percent,
        'vs_expected': None if expected_percent is None else round(used - expected_percent, 1),
    }


def _parse_time(iso_str: str | None) -> datetime | None:
    """Parse an API timestamp, or return None when it is missing, malformed or lacks a time zone."""
    if not iso_str:
        return None

    try:
        parsed = datetime.fromisoformat(iso_str)
    except (TypeError, ValueError):
        return None

    return parsed if parsed.tzinfo is not None else None


def _encode(message: dict[str, Any]) -> bytes:
    """Serialize a message onto one line, as the framing requires."""
    return json.dumps(message, separators=(',', ':')).encode('utf-8') + b'\n'


def _rpc_result(request_id: str | int, result: dict[str, Any]) -> dict[str, Any]:
    """Wrap a result, adding the fields every result of this revision carries."""
    return {'jsonrpc': '2.0', 'id': request_id, 'result': {'resultType': 'complete', **result, '_meta': {_META_SERVER_INFO: _SERVER_INFO}}}


def _handshake_result(request_id: str | int, result: dict[str, Any]) -> dict[str, Any]:
    """Wrap a result of the handshake revisions, which carry no result type and no per-result server identity."""
    return {'jsonrpc': '2.0', 'id': request_id, 'result': result}


def _rpc_error(request_id: str | int | None, code: int, message: str, data: dict[str, Any] | None = None) -> dict[str, Any]:
    error: dict[str, Any] = {'code': code, 'message': message}
    if data is not None:
        error['data'] = data

    return {'jsonrpc': '2.0', 'id': request_id, 'error': error}

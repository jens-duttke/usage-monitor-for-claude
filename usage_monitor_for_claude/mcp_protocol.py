"""
MCP Protocol
=============

The MCP messages of the ``get_usage`` server: the handshake, the tool list
and the shape of every answer.  Only the result of a tool call depends on
who answers - the tray application reports the usage, the ``--mcp`` bridge
reports that the application cannot be reached.

The bridge answers with this module whenever the application is not
reachable, so a client always gets a working server with its tool listed;
an MCP client marks a server that fails its handshake as broken and does not
retry it for the rest of the session.  Like ``mcp_channel.py`` it imports
nothing that reads the settings, because the bridge must never open a
dialog.

The wire format is that of MCP over stdio: one JSON-RPC message per line.  A
request that carries its protocol version in ``params._meta`` is served as
revision 2026-07-28, without a handshake; ``initialize`` and every request
without that version are served as one of the earlier handshake revisions.
"""
from __future__ import annotations

import json
import logging
from collections.abc import Callable
from typing import Any

from . import __version__
from .mcp_channel import MAX_MESSAGE_BYTES, expects_answer

__all__ = ['encode', 'handle_line', 'message_too_long_error', 'tool_result']

log = logging.getLogger(__name__)

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


def handle_line(line: bytes, call_tool: Callable[[], dict[str, Any]]) -> dict[str, Any] | None:
    """Answer one JSON-RPC message, or return None for a notification.

    Parameters
    ----------
    line : bytes
        One message, without its newline.
    call_tool : Callable
        Runs ``get_usage`` and returns its result, built with ``tool_result()``;
        called only for a valid ``tools/call``.
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
        return _answer_request(request_id, method, message.get('params', {}), call_tool)
    except Exception:
        log.exception('MCP request failed')
        return _rpc_error(request_id, _INTERNAL_ERROR, 'Internal error')


def tool_result(text: str, is_error: bool = False) -> dict[str, Any]:
    """Return a ``tools/call`` result carrying *text*.

    A failure of the tool itself is a result with ``is_error`` set, not a
    JSON-RPC error: the model reads it and can tell the user what to do.
    """
    return {'content': [{'type': 'text', 'text': text}], 'isError': is_error}


def message_too_long_error() -> dict[str, Any]:
    """Return the error for a line longer than ``MAX_MESSAGE_BYTES``."""
    return _rpc_error(None, _PARSE_ERROR, f'Message longer than {MAX_MESSAGE_BYTES} bytes')


def encode(message: dict[str, Any]) -> bytes:
    """Serialize a message onto one line, as the framing requires."""
    return json.dumps(message, separators=(',', ':')).encode('utf-8') + b'\n'


def _answer_request(request_id: str | int, method: str, params: Any, call_tool: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    meta = params.get('_meta') if isinstance(params, dict) else None
    if method == 'initialize' or not (isinstance(meta, dict) and _META_PROTOCOL_VERSION in meta):
        return _answer_handshake_request(request_id, method, params, call_tool)

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
        return _rpc_result(request_id, call_tool())

    return _rpc_error(request_id, _METHOD_NOT_FOUND, f'Method not found: {method}')


def _answer_handshake_request(request_id: str | int, method: str, params: Any, call_tool: Callable[[], dict[str, Any]]) -> dict[str, Any]:
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
        return _handshake_result(request_id, call_tool())

    return _rpc_error(request_id, _METHOD_NOT_FOUND, f'Method not found: {method}')


def _invalid_tool_call(params: dict[str, Any]) -> str | None:
    """Return why a ``tools/call`` cannot be served, or None when it can."""
    if params.get('name') != _TOOL_NAME:
        return f'Unknown tool: {params.get("name")}'
    if params.get('arguments') not in (None, {}):
        return f'{_TOOL_NAME} takes no arguments'

    return None


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

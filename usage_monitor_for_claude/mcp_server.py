"""
MCP Server
===========

Read-only Model Context Protocol server that tells a local Claude session
how much of its quota is left.

The one tool answers from the application's usage cache, so it never causes
an API request and never touches credentials.  Claude starts the ``--mcp``
bridge (``mcp_bridge.py``), which reaches this server over a local channel
that only the current user can open - a named pipe on Windows, a Unix domain
socket on Linux.  The messages themselves are answered by ``mcp_protocol.py``;
this module supplies the usage report and serves the channel.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from collections.abc import Callable
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from . import mcp_protocol
from .formatting import elapsed_pct, expand_popup_fields, field_period
from .mcp_channel import Connection, LineReader, mcp_channel_name
from .platforms.channel import LocalChannelListener
from .settings import POPUP_FIELDS

if TYPE_CHECKING:
    # Annotations only: the cache module imports api.py, which handles the credentials
    from .cache import CacheSnapshot

__all__ = ['handle_line', 'start_mcp_server', 'usage_report']

log = logging.getLogger(__name__)

# A tool call that arrives before the first fetch has returned waits for it
_FIRST_FETCH_WAIT_SECONDS = 15.0
_FIRST_FETCH_POLL_SECONDS = 0.25

# A restart starts the new process while the old one still serves the channel
_LISTEN_ATTEMPTS = 60
_LISTEN_RETRY_SECONDS = 2.0


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


def handle_line(line: bytes, read_snapshot: Callable[[], CacheSnapshot]) -> dict[str, Any] | None:
    """Answer one JSON-RPC message with the usage of this application, or return None for a notification.

    Parameters
    ----------
    line : bytes
        One message, without its newline.
    read_snapshot : Callable
        Returns the current cache state.
    """
    return mcp_protocol.handle_line(line, lambda: _usage_tool_result(read_snapshot))


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
                connection.sendall(mcp_protocol.encode(mcp_protocol.message_too_long_error()))
                return
            if line is None:
                return
            if not line.strip():
                continue
            answer = handle_line(line, read_snapshot)
            if answer is not None:
                connection.sendall(mcp_protocol.encode(answer))
    except OSError:
        log.debug('MCP connection failed', exc_info=True)
    finally:
        connection.close()


def _usage_tool_result(read_snapshot: Callable[[], CacheSnapshot]) -> dict[str, Any]:
    return mcp_protocol.tool_result(json.dumps(usage_report(_settled_snapshot(read_snapshot))))


def _settled_snapshot(read_snapshot: Callable[[], CacheSnapshot]) -> CacheSnapshot:
    """Return the cache state, waiting up to ``_FIRST_FETCH_WAIT_SECONDS`` while the first fetch is still outstanding.

    The application opens its channel before its first fetch has returned,
    so a Claude session started together with the application would
    otherwise be answered with no quotas at all.  Once one fetch has
    succeeded or failed, the state is returned as it is.
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
    # the model reads add up (used + remaining = 100 up to exhaustion, used - expected = vs_expected)
    used = round(used_percent, 1)
    expected_percent = None if elapsed is None else round(elapsed, 1)

    return {
        'field': field,
        'used_percent': used,
        # Both directions, because users phrase limits either way ("stop at 90%", "until 10% is left");
        # utilization can exceed 100, and nothing is left beyond that
        'remaining_percent': max(0.0, round(100 - used, 1)),
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

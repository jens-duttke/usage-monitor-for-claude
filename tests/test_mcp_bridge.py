"""
MCP stdio Bridge Tests
=======================

Unit tests for the stdio bridge against an in-memory channel that answers
through the real ``handle_line()``, plus end-to-end runs over the real
channel and through the ``--mcp`` entry point.
"""
from __future__ import annotations

import io
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import unittest
import uuid
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator
from unittest.mock import patch

from usage_monitor_for_claude import mcp_bridge
from usage_monitor_for_claude.cache import CacheSnapshot
from usage_monitor_for_claude.mcp_bridge import run_stdio_bridge
from usage_monitor_for_claude.mcp_channel import mcp_channel_name
from usage_monitor_for_claude.mcp_server import _serve_connection, handle_line

_REPO_ROOT = Path(__file__).resolve().parent.parent
_REAL_CHANNEL_AVAILABLE = sys.platform == 'win32' or hasattr(socket, 'AF_UNIX')
_META = {'io.modelcontextprotocol/protocolVersion': '2026-07-28', 'io.modelcontextprotocol/clientCapabilities': {}}
# A cache that has been fetched once, so a tool call is answered at once
_SNAPSHOT = CacheSnapshot({}, None, None, 1_700_000_000.0, False, None, 1)


def _request(method: str, params: dict[str, Any] | None = None, request_id: Any = 1) -> dict[str, Any]:
    return {'jsonrpc': '2.0', 'id': request_id, 'method': method, 'params': {'_meta': _META, **(params or {})}}


def _stdin(*messages: dict[str, Any] | bytes) -> io.BytesIO:
    encoded = [message if isinstance(message, bytes) else json.dumps(message).encode() for message in messages]
    return io.BytesIO(b'\n'.join(encoded) + b'\n')


def _run(stdin: Iterable[bytes], channel_name: str = 'test-channel') -> list[dict[str, Any]]:
    stdout = io.BytesIO()
    exit_code = run_stdio_bridge(stdin, stdout, channel_name=channel_name)  # type: ignore[arg-type]  # any iterable of lines will do
    assert exit_code == 0
    output = stdout.getvalue()
    assert output == b'' or output.endswith(b'\n'), output

    return [json.loads(line) for line in output.splitlines()]


class _InMemoryApp:
    """Stands in for the tray application: a connection that answers through the real handle_line()."""

    def __init__(self, breaks_after: int | None = None) -> None:
        self._pending = b''
        self.received: list[bytes] = []
        self.closed = False
        self._breaks_after = breaks_after

    def sendall(self, data: bytes) -> None:
        if self._breaks_after is not None and len(self.received) >= self._breaks_after:
            raise BrokenPipeError
        for line in data.splitlines():
            self.received.append(line)
            answer = handle_line(line, lambda: _SNAPSHOT)
            if answer is not None:
                self._pending += json.dumps(answer).encode() + b'\n'

    def recv(self, size: int) -> bytes:
        chunk, self._pending = self._pending[:size], self._pending[size:]
        return chunk

    def close(self) -> None:
        self.closed = True


class TestBridge(unittest.TestCase):
    """Tests for passing messages between stdio and the tray application."""

    def setUp(self):
        self.app = _InMemoryApp()
        patcher = patch('usage_monitor_for_claude.mcp_bridge.open_local_channel', return_value=self.app)
        self.mock_open = patcher.start()
        self.addCleanup(patcher.stop)

    def test_discover_round_trip(self):
        answers = _run(_stdin(_request('server/discover')))

        self.assertEqual(answers[0]['result']['supportedVersions'], ['2026-07-28'])
        self.mock_open.assert_called_once_with('test-channel')

    def test_tool_call(self):
        answers = _run(_stdin(_request('tools/call', {'name': 'get_usage', 'arguments': {}})))

        self.assertEqual(json.loads(answers[0]['result']['content'][0]['text'])['quotas'], [])

    def test_answers_keep_the_order_of_the_requests(self):
        answers = _run(_stdin(_request('server/discover', request_id=1), _request('tools/list', request_id=2), _request('ping', request_id=3)))

        self.assertEqual([answer['id'] for answer in answers], [1, 2, 3])

    def test_lines_reach_the_app_unchanged(self):
        """The bridge decides nothing: the application sees exactly what the client wrote."""
        request = json.dumps(_request('server/discover')).encode()

        _run(io.BytesIO(request + b'\r\n'))

        self.assertEqual(self.app.received, [request])

    def test_notification_produces_no_output(self):
        self.assertEqual(_run(_stdin({'jsonrpc': '2.0', 'method': 'notifications/cancelled', 'params': {'requestId': 1}})), [])

    def test_server_verdicts_are_relayed(self):
        answers = _run(_stdin(b'{not json', _request('resources/list', request_id=4)))

        self.assertEqual([answer['error']['code'] for answer in answers], [-32700, -32601])

    def test_blank_lines_are_skipped(self):
        answers = _run(io.BytesIO(b'\n  \n' + json.dumps(_request('server/discover')).encode() + b'\n'))

        self.assertEqual(len(answers), 1)
        self.assertEqual(len(self.app.received), 1)

    def test_channel_is_closed_at_the_end(self):
        _run(_stdin(_request('server/discover')))

        self.assertTrue(self.app.closed)

    def test_default_channel_is_this_instances_one(self):
        run_stdio_bridge(_stdin(_request('server/discover')), io.BytesIO())

        self.mock_open.assert_called_once_with(mcp_channel_name())


class _AppAvailability:
    """Stands in for ``open_local_channel``: no application until ``start()``, then the given connections in turn."""

    def __init__(self, *connections: _InMemoryApp) -> None:
        self._connections = list(connections)
        self.running = False

    def start(self) -> None:
        self.running = True

    def stop(self) -> None:
        self.running = False

    def __call__(self, name: str) -> _InMemoryApp:
        if not self.running or not self._connections:
            raise FileNotFoundError(name)

        return self._connections.pop(0)


def _lines(*steps: dict[str, Any] | bytes | Callable[[], None]) -> Iterator[bytes]:
    """Yield the messages as stdin lines, running each callable in between as the bridge reaches it."""
    for step in steps:
        if callable(step):
            step()
        else:
            yield (step if isinstance(step, bytes) else json.dumps(step).encode()) + b'\n'


def _tool_call(request_id: int) -> dict[str, Any]:
    return _request('tools/call', {'name': 'get_usage', 'arguments': {}}, request_id=request_id)


def _tool_error_text(answer: dict[str, Any]) -> str | None:
    """Return the text of a tool result that reports an error, or None for a tool result with data."""
    result = answer['result']
    return result['content'][0]['text'] if result['isError'] else None


class TestAppNotReachable(unittest.TestCase):
    """Tests for an application that is not running, is quit, or restarts; the bridge never starts it."""

    def _channel(self, availability: Any) -> Any:
        return patch.object(mcp_bridge, 'open_local_channel', side_effect=availability)

    def test_tool_call_reports_why_as_a_tool_error(self):
        """The model reads a tool error and can relay it; the bridge cannot tell a stopped application from one with the server off, so it names both."""
        with self._channel(FileNotFoundError):
            answers = _run(_stdin(_tool_call(7)))

        self.assertEqual(answers[0]['id'], 7)
        self.assertNotIn('error', answers[0])
        message = _tool_error_text(answers[0])
        self.assertIn('not running', message)
        self.assertIn('"mcp_server": true', message)

    def test_handshake_and_tool_list_succeed_without_the_app(self):
        """A client marks a server whose handshake fails as broken for the whole session, so these must never depend on the app."""
        handshake = {'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {'protocolVersion': '2025-06-18', 'capabilities': {}}}
        requests = [handshake, {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list', 'params': {}}, _request('server/discover', request_id=3)]
        with self._channel(FileNotFoundError):
            answers = _run(_stdin(*requests))

        from_the_app = [handle_line(json.dumps(request).encode(), lambda: _SNAPSHOT) for request in requests]
        self.assertEqual(answers, from_the_app)
        self.assertEqual(answers[1]['result']['tools'][0]['name'], 'get_usage')

    def test_protocol_errors_match_the_apps_without_the_app(self):
        """An unknown tool or method is a protocol error, the same one the application would send."""
        requests = [_request('tools/call', {'name': 'other_tool'}, request_id=1), _request('resources/list', request_id=2)]
        with self._channel(FileNotFoundError):
            answers = _run(_stdin(*requests))

        self.assertEqual([answer['error']['code'] for answer in answers], [-32602, -32601])

    def test_every_request_tries_again_and_none_waits(self):
        """Each request probes the channel once and is answered at once, so a missing application costs no time."""
        with self._channel(FileNotFoundError) as mock_open:
            answers = _run(_stdin(*(_tool_call(request_id) for request_id in range(1, 5))))

        self.assertTrue(all(_tool_error_text(answer) for answer in answers))
        self.assertEqual(mock_open.call_count, 4)

    def test_app_started_after_the_client_is_reached_without_reconnecting(self):
        """The case the bridge's own answers exist for: Claude opened first, the app started later, the next call gets the data."""
        availability = _AppAvailability(_InMemoryApp())
        with self._channel(availability):
            answers = _run(_lines(_tool_call(1), availability.start, _tool_call(2)))

        self.assertIsNotNone(_tool_error_text(answers[0]))
        self.assertIsNone(_tool_error_text(answers[1]))
        self.assertEqual(json.loads(answers[1]['result']['content'][0]['text'])['quotas'], [])

    def test_quit_app_is_reported_and_reached_again_once_it_is_back(self):
        """An application the user quit while Claude was open gets a tool error, and the next call after its start reaches it."""
        availability = _AppAvailability(_InMemoryApp(breaks_after=1), _InMemoryApp())
        availability.start()
        with self._channel(availability):
            answers = _run(_lines(_tool_call(1), availability.stop, _tool_call(2), availability.start, _tool_call(3)))

        self.assertEqual([answer['id'] for answer in answers], [1, 2, 3])
        self.assertEqual([_tool_error_text(answer) is None for answer in answers], [True, False, True])

    def test_app_restart_is_bridged_without_losing_the_message(self):
        """The first message after a restart finds the old connection broken and is resent on a new one."""
        before_restart, after_restart = _InMemoryApp(breaks_after=1), _InMemoryApp()
        with self._channel([before_restart, after_restart]):
            answers = _run(_stdin(_request('server/discover', request_id=1), _tool_call(2)))

        self.assertEqual([answer['id'] for answer in answers], [1, 2])
        self.assertIsNone(_tool_error_text(answers[1]))
        self.assertEqual(len(after_restart.received), 1)
        self.assertTrue(before_restart.closed)

    def test_tool_call_in_the_gap_of_a_restart_gets_a_tool_error_and_the_next_one_the_data(self):
        before_restart, after_restart = _InMemoryApp(breaks_after=1), _InMemoryApp()
        with self._channel([before_restart, FileNotFoundError(), after_restart]):
            answers = _run(_stdin(*(_tool_call(request_id) for request_id in (1, 2, 3))))

        self.assertEqual([_tool_error_text(answer) is None for answer in answers], [True, False, True])

    def test_notifications_get_no_answer_without_an_app(self):
        with self._channel(FileNotFoundError):
            self.assertEqual(_run(_stdin({'jsonrpc': '2.0', 'method': 'notifications/cancelled'})), [])

    def test_malformed_line_gets_a_parse_error_without_id(self):
        with self._channel(FileNotFoundError):
            answers = _run(_stdin(b'{not json'))

        self.assertIsNone(answers[0]['id'])
        self.assertEqual(answers[0]['error']['code'], -32700)

@unittest.skipUnless(_REAL_CHANNEL_AVAILABLE, 'this system offers neither named pipes nor Unix domain sockets')
class TestRealChannel(unittest.TestCase):
    """End to end over the real channel: bridge, channel, server."""

    def _serve(self, name: str, connections: int = 1) -> None:
        from usage_monitor_for_claude.platforms.channel import LocalChannelListener

        listener = LocalChannelListener(name)

        def accept_all() -> None:
            for _ in range(connections):
                _serve_connection(listener.accept(), lambda: _SNAPSHOT)
        threading.Thread(target=accept_all, daemon=True).start()

    def test_bridge_over_the_real_channel(self):
        name = f'usage-monitor-for-claude-test-{uuid.uuid4().hex[:12]}'
        self._serve(name)

        answers = _run(_stdin(_request('server/discover', request_id=1), _request('tools/call', {'name': 'get_usage'}, request_id=2)), name)

        self.assertEqual([answer['id'] for answer in answers], [1, 2])
        self.assertEqual(answers[1]['result']['resultType'], 'complete')

    def test_entry_point_reaches_the_instance_of_its_config_dir(self):
        """``--mcp --config-dir=...`` must find the tray instance of that account, not the default one."""
        config_dir = tempfile.TemporaryDirectory()
        self.addCleanup(config_dir.cleanup)
        with patch.dict(os.environ, {'CLAUDE_CONFIG_DIR': str(Path(config_dir.name).resolve())}):
            name = mcp_channel_name()
        self.assertNotEqual(name, 'usage-monitor-for-claude-mcp')
        self._serve(name)

        completed = subprocess.run(
            [sys.executable, '-m', 'usage_monitor_for_claude', '--mcp', f'--config-dir={config_dir.name}'],
            input=json.dumps(_request('server/discover')).encode() + b'\n', capture_output=True, cwd=_REPO_ROOT,
            env={**os.environ, 'PYTHONPATH': str(_REPO_ROOT)}, timeout=60, check=False,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr.decode(errors='replace'))
        lines = completed.stdout.splitlines()
        self.assertEqual(len(lines), 1, completed.stdout)
        self.assertEqual(json.loads(lines[0])['result']['supportedVersions'], ['2026-07-28'])

    def test_entry_point_follows_the_clients_config_dir(self):
        """A Claude Code started with CLAUDE_CONFIG_DIR passes it on, and so reaches that account's instance by itself."""
        config_dir = tempfile.TemporaryDirectory()
        self.addCleanup(config_dir.cleanup)
        resolved = str(Path(config_dir.name).resolve())
        with patch.dict(os.environ, {'CLAUDE_CONFIG_DIR': resolved}):
            self._serve(mcp_channel_name())

        completed = subprocess.run(
            [sys.executable, '-m', 'usage_monitor_for_claude', '--mcp'], input=json.dumps(_request('server/discover')).encode() + b'\n',
            capture_output=True, cwd=_REPO_ROOT, env={**os.environ, 'PYTHONPATH': str(_REPO_ROOT), 'CLAUDE_CONFIG_DIR': resolved},
            timeout=60, check=False,
        )

        self.assertEqual(json.loads(completed.stdout.splitlines()[0])['result']['resultType'], 'complete', completed.stderr)


class TestBridgeProcess(unittest.TestCase):
    """Tests for what the bridge process loads.

    Run in a subprocess, because this test process has long imported the
    whole package: only a fresh interpreter shows what the bridge pulls in.
    """

    def test_bridge_never_loads_settings_or_the_credentials_module(self):
        """settings can open a dialog at import time, which would block a process Claude started without a window; api.py handles the credentials."""
        forbidden = tuple(f'usage_monitor_for_claude.{module}' for module in ('settings', 'api', 'i18n', 'mcp_server'))
        probe = f'import sys, usage_monitor_for_claude.mcp_bridge; print(sorted(m for m in {forbidden!r} if m in sys.modules))'

        completed = subprocess.run(
            [sys.executable, '-c', probe], capture_output=True, cwd=_REPO_ROOT, env={**os.environ, 'PYTHONPATH': str(_REPO_ROOT)},
            timeout=60, check=False,
        )

        self.assertEqual(completed.stdout.strip(), b'[]', completed.stderr.decode(errors='replace'))

    def test_entry_point_without_an_app_answers_at_once_despite_an_invalid_settings_file(self):
        """Nothing serves this account's channel: the handshake still succeeds, the tool call reports it, and a broken settings file opens no dialog."""
        config_dir = tempfile.TemporaryDirectory()
        self.addCleanup(config_dir.cleanup)
        Path(config_dir.name, 'usage-monitor-settings.json').write_text('{not json', encoding='utf-8')
        handshake = {'jsonrpc': '2.0', 'id': 4, 'method': 'initialize', 'params': {'protocolVersion': '2025-06-18', 'capabilities': {}}}

        completed = subprocess.run(
            [sys.executable, '-m', 'usage_monitor_for_claude', '--mcp', f'--config-dir={config_dir.name}'],
            input=_stdin(handshake, _tool_call(5)).getvalue(), capture_output=True, cwd=_REPO_ROOT,
            env={**os.environ, 'PYTHONPATH': str(_REPO_ROOT)}, timeout=30, check=False,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr.decode(errors='replace'))
        initialized, tool_answer = [json.loads(line) for line in completed.stdout.splitlines()]
        self.assertEqual(initialized['result']['serverInfo']['name'], 'usage-monitor-for-claude')
        self.assertEqual(tool_answer['id'], 5)
        self.assertIn('"mcp_server": true', _tool_error_text(tool_answer))

    def test_bridge_never_loads_a_network_client(self):
        """Nothing in the bridge needs HTTP; a requests or http.client import would mean a new way out of the machine."""
        probe = 'import sys, usage_monitor_for_claude.mcp_bridge; print(sorted(m for m in ("requests", "http.client", "urllib3") if m in sys.modules))'

        completed = subprocess.run(
            [sys.executable, '-c', probe], capture_output=True, cwd=_REPO_ROOT, env={**os.environ, 'PYTHONPATH': str(_REPO_ROOT)},
            timeout=60, check=False,
        )

        self.assertEqual(completed.stdout.strip(), b'[]', completed.stderr.decode(errors='replace'))


if __name__ == '__main__':
    unittest.main()

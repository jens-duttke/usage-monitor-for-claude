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
from unittest.mock import MagicMock, patch

from usage_monitor_for_claude import mcp_bridge
from usage_monitor_for_claude.cache import CacheSnapshot
from usage_monitor_for_claude.mcp_bridge import run_stdio_bridge, start_app_in_background
from usage_monitor_for_claude.mcp_server import _serve_connection, handle_line, mcp_channel_name

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


def _run(stdin: Iterable[bytes], channel_name: str = 'test-channel', launch_app: Callable[[], None] | None = None) -> list[dict[str, Any]]:
    stdout = io.BytesIO()
    exit_code = run_stdio_bridge(stdin, stdout, channel_name=channel_name, launch_app=launch_app)  # type: ignore[arg-type]  # any iterable of lines will do
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


class TestAppNotReachable(unittest.TestCase):
    """Tests for an application that is not running, is started by the bridge, is quit, or restarts."""

    def setUp(self):
        # Waits that end almost at once, and a channel probe that never sleeps
        for name, value in (('_APP_WAIT_SECONDS', 0.05), ('_APP_POLL_SECONDS', 0.0)):
            patcher = patch.object(mcp_bridge, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

        self.wait = MagicMock(wraps=mcp_bridge._wait_for_channel)
        patcher = patch.object(mcp_bridge, '_wait_for_channel', self.wait)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _channel(self, availability: Any) -> Any:
        return patch.object(mcp_bridge, 'open_local_channel', side_effect=availability)

    def test_requests_get_an_error_that_says_why(self):
        with self._channel(FileNotFoundError):
            answers = _run(_stdin(_request('server/discover', request_id=7)))

        self.assertEqual(answers[0]['id'], 7)
        self.assertIn('not running', answers[0]['error']['message'])

    def test_bridge_starts_the_app_and_waits_for_it(self):
        """Claude may start before the tray application; the first request starts it and is answered by it."""
        availability = _AppAvailability(_InMemoryApp())
        launch = MagicMock(side_effect=availability.start)
        with patch.object(mcp_bridge, '_APP_WAIT_SECONDS', 5.0), self._channel(availability):
            answers = _run(_stdin(_request('server/discover', request_id=1), _request('tools/list', request_id=2)), launch_app=launch)

        self.assertEqual([answer['id'] for answer in answers], [1, 2])
        self.assertTrue(all('result' in answer for answer in answers))
        launch.assert_called_once_with()

    def test_app_that_does_not_come_up_is_started_again_without_the_wait(self):
        """After one wait in vain every call still starts the application, without waiting for it."""
        launch = MagicMock()
        with self._channel(FileNotFoundError):
            answers = _run(_stdin(_request('server/discover', request_id=1), _request('tools/list', request_id=2)), launch_app=launch)

        self.assertEqual([answer['error']['code'] for answer in answers], [-32603, -32603])
        self.assertEqual(launch.call_count, 2)
        self.assertEqual(self.wait.call_count, 1)

    def test_app_that_comes_up_late_is_reached_by_a_later_call(self):
        """A start that took longer than the wait is picked up, and from then on the bridge waits again when needed."""
        availability = _AppAvailability(_InMemoryApp(breaks_after=1), _InMemoryApp())
        launch = MagicMock()
        with self._channel(availability):
            answers = _run(_lines(
                _request('server/discover', request_id=1), availability.start, _request('tools/list', request_id=2),
                availability.stop, _request('tools/list', request_id=3),
            ), launch_app=launch)

        self.assertIn('error', answers[0])
        self.assertIn('result', answers[1])
        self.assertEqual(self.wait.call_count, 2)

    def test_app_started_by_the_user_after_a_failed_wait_is_picked_up(self):
        availability = _AppAvailability(_InMemoryApp())
        with self._channel(availability):
            answers = _run(_lines(_request('server/discover', request_id=1), availability.start, _request('server/discover', request_id=2)))

        self.assertIn('error', answers[0])
        self.assertEqual(answers[1]['result']['resultType'], 'complete')

    def test_quit_app_is_started_again(self):
        """An application the user quit while Claude was open comes back with the next call."""
        availability = _AppAvailability(_InMemoryApp(breaks_after=1), _InMemoryApp())
        availability.start()
        launch = MagicMock(side_effect=availability.start)
        with self._channel(availability):
            answers = _run(_lines(_request('server/discover', request_id=1), availability.stop, _request('tools/list', request_id=2)), launch_app=launch)

        self.assertEqual([answer['id'] for answer in answers], [1, 2])
        self.assertIn('result', answers[1])
        launch.assert_called_once_with()
        self.assertEqual(self.wait.call_count, 1)

    def test_restarting_app_is_waited_for(self):
        """A Restart leaves a gap between the old process and the new one; the request waits it out, and the start it adds exits by itself."""
        before_restart, after_restart = _InMemoryApp(breaks_after=1), _InMemoryApp()
        launch = MagicMock()
        with patch.object(mcp_bridge, '_APP_WAIT_SECONDS', 5.0), \
             self._channel([before_restart, FileNotFoundError(), FileNotFoundError(), after_restart]):
            answers = _run(_stdin(_request('server/discover', request_id=1), _request('tools/list', request_id=2)), launch_app=launch)

        self.assertEqual([answer['id'] for answer in answers], [1, 2])
        self.assertIn('result', answers[1])
        self.assertEqual(len(after_restart.received), 1)
        launch.assert_called_once_with()

    def test_app_restart_is_bridged_without_losing_the_message(self):
        """The first message after a restart finds the old connection broken and is resent on a new one."""
        before_restart, after_restart = _InMemoryApp(breaks_after=1), _InMemoryApp()
        with self._channel([before_restart, after_restart]):
            answers = _run(_stdin(_request('server/discover', request_id=1), _request('tools/list', request_id=2)))

        self.assertEqual([answer['id'] for answer in answers], [1, 2])
        self.assertIn('result', answers[1])
        self.assertEqual(len(after_restart.received), 1)
        self.assertTrue(before_restart.closed)
        self.assertEqual(self.wait.call_count, 0)

    def test_notifications_neither_start_nor_wait_for_the_app(self):
        launch = MagicMock()
        with self._channel(FileNotFoundError):
            self.assertEqual(_run(_stdin({'jsonrpc': '2.0', 'method': 'notifications/cancelled'}), launch_app=launch), [])

        launch.assert_not_called()
        self.assertEqual(self.wait.call_count, 0)

    def test_malformed_line_gets_an_error_without_id(self):
        with self._channel(FileNotFoundError):
            answers = _run(_stdin(b'{not json'))

        self.assertIsNone(answers[0]['id'])


class TestStartAppInBackground(unittest.TestCase):
    """Tests for the start of the tray application from the bridge."""

    def test_starts_this_application_detached_without_the_replace_dialog(self):
        with patch.object(mcp_bridge, 'app_command', return_value=(['app.exe', '--exit-if-running'], {'A': 'B'})) as mock_command, \
             patch.object(mcp_bridge, 'start_detached') as mock_start:
            start_app_in_background()

        mock_command.assert_called_once_with(['--exit-if-running'])
        mock_start.assert_called_once_with(['app.exe', '--exit-if-running'], {'A': 'B'})

    def test_inherits_the_bridges_config_dir(self):
        """The started application must open the very channel the bridge connects to."""
        with patch.dict(os.environ, {'CLAUDE_CONFIG_DIR': 'C:\\accounts\\work'}), patch.object(mcp_bridge, 'start_detached') as mock_start:
            start_app_in_background()

        self.assertEqual(mock_start.call_args.args[1]['CLAUDE_CONFIG_DIR'], 'C:\\accounts\\work')

    def test_failed_start_is_not_an_error(self):
        """The request then reports that the application is not running instead of ending the bridge."""
        with patch.object(mcp_bridge, 'start_detached', side_effect=FileNotFoundError):
            start_app_in_background()


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
    """Tests for what the bridge process loads."""

    def test_bridge_never_loads_the_credentials_module(self):
        """The bridge holds no data and talks only to the local channel, so api.py has no business being imported."""
        probe = 'import sys, usage_monitor_for_claude.mcp_bridge; print("usage_monitor_for_claude.api" in sys.modules)'

        completed = subprocess.run(
            [sys.executable, '-c', probe], capture_output=True, cwd=_REPO_ROOT, env={**os.environ, 'PYTHONPATH': str(_REPO_ROOT)},
            timeout=60, check=False,
        )

        self.assertEqual(completed.stdout.strip(), b'False', completed.stderr.decode(errors='replace'))

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

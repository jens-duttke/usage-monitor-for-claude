"""
MCP Server Tests
=================

Unit tests for the usage report, the MCP messages and the per-connection
framing.  The protocol is tested through ``handle_line()`` directly, because
the channel underneath carries the lines unchanged; ``TestRealChannel`` runs
one exchange over the real channel of the running system.
"""
from __future__ import annotations

import json
import socket
import sys
import threading
import time
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import MagicMock, patch

from usage_monitor_for_claude.app import UsageMonitorForClaude
from usage_monitor_for_claude.cache import CacheSnapshot
from usage_monitor_for_claude.mcp_server import (
    _LISTEN_ATTEMPTS, MAX_MESSAGE_BYTES, LineReader, _serve, _serve_connection, expects_answer, handle_line, mcp_channel_name,
    start_mcp_server, usage_report,
)

_REAL_CHANNEL_AVAILABLE = sys.platform == 'win32' or hasattr(socket, 'AF_UNIX')
_META = {
    'io.modelcontextprotocol/protocolVersion': '2026-07-28',
    'io.modelcontextprotocol/clientInfo': {'name': 'test', 'version': '1'},
    'io.modelcontextprotocol/clientCapabilities': {},
}


def setUpModule() -> None:
    """Keep a local settings file from changing which quotas the tests see."""
    patcher = patch('usage_monitor_for_claude.mcp_server.POPUP_FIELDS', ['*'])
    patcher.start()
    unittest.addModuleCleanup(patcher.stop)


def _snapshot(usage: dict[str, Any] | None = None, last_success_time: float | None = None, last_error: str | None = None) -> CacheSnapshot:
    return CacheSnapshot(
        usage=usage if usage is not None else {}, profile=None, prepaid=None, last_success_time=last_success_time,
        refreshing=False, last_error=last_error, version=1,
    )


def _resets_in(**kwargs: float) -> str:
    return (datetime.now(timezone.utc) + timedelta(**kwargs)).isoformat()


def _request(method: str, params: dict[str, Any] | None = None, request_id: Any = 1, meta: dict[str, Any] | None = None) -> bytes:
    """Return one request line as a client of revision 2026-07-28 writes it."""
    message = {'jsonrpc': '2.0', 'id': request_id, 'method': method, 'params': {'_meta': dict(_META if meta is None else meta), **(params or {})}}
    return json.dumps(message).encode()


class _FakeConnection:
    """A connection whose incoming bytes are given up front and whose outgoing bytes are recorded."""

    def __init__(self, *chunks: bytes, fail_on_send: bool = False) -> None:
        self._chunks = list(chunks)
        self.sent = b''
        self.closed = False
        self._fail_on_send = fail_on_send

    def recv(self, size: int) -> bytes:
        return self._chunks.pop(0) if self._chunks else b''

    def sendall(self, data: bytes) -> None:
        if self._fail_on_send:
            raise BrokenPipeError
        self.sent += data

    def close(self) -> None:
        self.closed = True

    def answers(self) -> list[dict[str, Any]]:
        return [json.loads(line) for line in self.sent.splitlines()]


class TestUsageReport(unittest.TestCase):
    """Tests for the data the tool hands to the model."""

    def test_reports_percentages_reset_and_time_marker(self):
        """Half a five-hour window gone at 80% used is 30 points ahead of the marker."""
        resets_at = _resets_in(hours=2, minutes=30)

        report = usage_report(_snapshot({'five_hour': {'utilization': 80.0, 'resets_at': resets_at}}))

        quota = report['quotas'][0]
        self.assertEqual(quota['field'], 'five_hour')
        self.assertEqual(quota['used_percent'], 80.0)
        self.assertEqual(quota['remaining_percent'], 20.0)
        self.assertEqual(datetime.fromisoformat(quota['resets_at']), datetime.fromisoformat(resets_at).replace(microsecond=0))
        self.assertEqual(quota['resets_in_minutes'], 150)
        self.assertAlmostEqual(quota['expected_percent'], 50.0, delta=0.2)
        self.assertAlmostEqual(quota['vs_expected'], 30.0, delta=0.2)

    def test_usage_behind_the_marker_is_negative(self):
        """Below the marker means usage is behind the clock."""
        usage = {'seven_day': {'utilization': 10.0, 'resets_at': _resets_in(days=3, hours=12)}}

        quota = usage_report(_snapshot(usage))['quotas'][0]

        self.assertAlmostEqual(quota['vs_expected'], -40.0, delta=0.2)

    def test_time_marker_follows_the_working_hours_of_the_field(self):
        """The marker is the one the popup draws, so the field reaches elapsed_pct for its schedule."""
        resets_at = _resets_in(days=3)
        usage = {'seven_day_sonnet': {'utilization': 40.0, 'resets_at': resets_at}}

        with patch('usage_monitor_for_claude.mcp_server.elapsed_pct', return_value=25.0) as mock_elapsed:
            quota = usage_report(_snapshot(usage))['quotas'][0]

        mock_elapsed.assert_called_once_with(resets_at, 7 * 24 * 3600, field='seven_day_sonnet')
        self.assertEqual(quota['expected_percent'], 25.0)
        self.assertEqual(quota['vs_expected'], 15.0)

    def test_null_reset_window_leaves_the_marker_unknown(self):
        """A quota between a reset and its next use has no window to compare against."""
        quota = usage_report(_snapshot({'five_hour': {'utilization': 0, 'resets_at': None}}))['quotas'][0]

        self.assertEqual(quota['used_percent'], 0)
        self.assertIsNone(quota['resets_at'])
        self.assertIsNone(quota['resets_in_minutes'])
        self.assertIsNone(quota['expected_percent'])
        self.assertIsNone(quota['vs_expected'])

    def test_unparsable_period_leaves_the_marker_unknown(self):
        """A code name carrying a reset window still reports its numbers."""
        usage = {'tangelo': {'utilization': 5.0, 'resets_at': _resets_in(hours=1)}}

        quota = usage_report(_snapshot(usage))['quotas'][0]

        self.assertEqual(quota['used_percent'], 5.0)
        self.assertIsNone(quota['expected_percent'])

    def test_long_api_value_is_rounded_to_one_decimal(self):
        """The API sends some quotas unrounded; used and remaining still add up to 100."""
        quota = usage_report(_snapshot({'iguana_necktie': {'utilization': 53.26981919999999, 'resets_at': _resets_in(days=1)}}))['quotas'][0]

        self.assertEqual(quota['used_percent'], 53.3)
        self.assertEqual(quota['remaining_percent'], 46.7)

    def test_derived_values_come_from_the_rounded_ones(self):
        """vs_expected is exactly used minus expected as the model reads them."""
        usage = {'seven_day': {'utilization': 45.26, 'resets_at': _resets_in(days=3)}}

        with patch('usage_monitor_for_claude.mcp_server.elapsed_pct', return_value=35.94):
            quota = usage_report(_snapshot(usage))['quotas'][0]

        self.assertEqual((quota['used_percent'], quota['expected_percent'], quota['vs_expected']), (45.3, 35.9, 9.4))

    def test_every_one_decimal_value_adds_up(self):
        """No value from 0.0 to 100.0 leaves float noise or breaks used + remaining = 100."""
        for tenths in range(1001):
            used_percent = tenths / 10
            quota = usage_report(_snapshot({'five_hour': {'utilization': used_percent, 'resets_at': _resets_in(hours=1)}}))['quotas'][0]
            self.assertEqual(round(quota['used_percent'] + quota['remaining_percent'], 1), 100.0, used_percent)
            self.assertLessEqual(len(repr(quota['remaining_percent']).split('.')[-1]), 1, used_percent)

    def test_reset_time_is_local_and_whole_seconds(self):
        """The API sends UTC with microseconds; the model gets the user's clock time without them."""
        quota = usage_report(_snapshot({'five_hour': {'utilization': 1.0, 'resets_at': '2026-11-02T10:20:00.339983+00:00'}}))['quotas'][0]

        local_reset = datetime(2026, 11, 2, 10, 20, tzinfo=timezone.utc).astimezone()
        self.assertEqual(quota['resets_at'], local_reset.isoformat())
        self.assertNotIn('.', quota['resets_at'])
        self.assertEqual(datetime.fromisoformat(quota['resets_at']).utcoffset(), local_reset.utcoffset())

    def test_reset_in_the_past_is_reported_as_is(self):
        """A reset the API has not confirmed yet shows how long ago it was due."""
        quota = usage_report(_snapshot({'five_hour': {'utilization': 99.0, 'resets_at': _resets_in(minutes=-3)}}))['quotas'][0]

        self.assertEqual(quota['resets_in_minutes'], -3)

    def test_malformed_reset_time_is_dropped(self):
        """A timestamp that does not parse, or names no time zone, yields no reset and no marker."""
        for resets_at in ('soon', '2026-11-02T10:20:00', 1730000000):
            with self.subTest(resets_at=resets_at):
                quota = usage_report(_snapshot({'five_hour': {'utilization': 5.0, 'resets_at': resets_at}}))['quotas'][0]
                self.assertEqual(quota['used_percent'], 5.0)
                self.assertIsNone(quota['resets_at'])
                self.assertIsNone(quota['resets_in_minutes'])
                self.assertIsNone(quota['expected_percent'])

    def test_quota_order_and_filtering(self):
        """Shorter periods first; placeholders, null fields and extra_usage stay out."""
        usage = {
            'seven_day': {'utilization': 1.0, 'resets_at': _resets_in(days=1)},
            'five_hour': {'utilization': 2.0, 'resets_at': _resets_in(hours=1)},
            'nimbus_quill': {'utilization': 0, 'resets_at': None},
            'seven_day_opus': None,
            'extra_usage': {'is_enabled': True, 'utilization': 50.0},
        }

        report = usage_report(_snapshot(usage))

        self.assertEqual([quota['field'] for quota in report['quotas']], ['five_hour', 'seven_day'])

    def test_excluded_field_is_not_reported(self):
        """A quota the user excluded from the popup stays hidden from the model too."""
        usage = {
            'five_hour': {'utilization': 2.0, 'resets_at': _resets_in(hours=1)},
            'seven_day': {'utilization': 1.0, 'resets_at': _resets_in(days=1)},
            'iguana_necktie': {'utilization': 9.0, 'resets_at': _resets_in(days=1)},
        }

        with patch('usage_monitor_for_claude.mcp_server.POPUP_FIELDS', ['*', '-iguana_necktie', '-seven_day']):
            report = usage_report(_snapshot(usage))

        self.assertEqual([quota['field'] for quota in report['quotas']], ['five_hour'])
        self.assertNotIn('iguana_necktie', json.dumps(report))

    def test_field_selection_and_order_match_the_popup(self):
        """An explicit list limits the report and sets its order, as it does for the bars."""
        usage = {
            'five_hour': {'utilization': 2.0, 'resets_at': _resets_in(hours=1)},
            'seven_day': {'utilization': 1.0, 'resets_at': _resets_in(days=1)},
            'seven_day_sonnet': {'utilization': 3.0, 'resets_at': _resets_in(days=1)},
        }

        with patch('usage_monitor_for_claude.mcp_server.POPUP_FIELDS', ['seven_day_sonnet', 'five_hour']):
            report = usage_report(_snapshot(usage))

        self.assertEqual([quota['field'] for quota in report['quotas']], ['seven_day_sonnet', 'five_hour'])

    def test_excluding_everything_leaves_an_empty_report(self):
        with patch('usage_monitor_for_claude.mcp_server.POPUP_FIELDS', ['*', '-five_hour']):
            report = usage_report(_snapshot({'five_hour': {'utilization': 2.0, 'resets_at': _resets_in(hours=1)}}))

        self.assertEqual(report['quotas'], [])

    def test_non_numeric_utilization_is_skipped(self):
        """A shape change in one field must not cost the report of the others."""
        usage = {
            'five_hour': {'utilization': 'high', 'resets_at': _resets_in(hours=1)},
            'seven_day': {'utilization': True, 'resets_at': _resets_in(days=1)},
            'seven_day_sonnet': {'utilization': 3.0, 'resets_at': _resets_in(days=1)},
        }

        report = usage_report(_snapshot(usage))

        self.assertEqual([quota['field'] for quota in report['quotas']], ['seven_day_sonnet'])

    def test_before_first_fetch(self):
        """No data yet is reported as such rather than as an empty success."""
        report = usage_report(_snapshot(last_error='no token'))

        self.assertEqual(report, {'quotas': [], 'data_age_seconds': None, 'error': 'no token'})

    def test_data_age(self):
        """The model can tell how old the numbers are."""
        report = usage_report(_snapshot(last_success_time=time.time() - 90))

        self.assertAlmostEqual(report['data_age_seconds'], 90, delta=2)

    def test_report_is_json_serializable_and_holds_no_account_data(self):
        """Only quota numbers leave the process, whatever else the response carried."""
        usage = {'five_hour': {'utilization': 1.0, 'resets_at': _resets_in(hours=1), 'secret': 'x'}, 'extra_usage': {'used_credits': 5}}
        snapshot = CacheSnapshot(
            usage=usage, profile={'account': {'email': 'someone@example.com'}}, prepaid={'amount': 123},
            last_success_time=None, refreshing=False, last_error=None, version=1,
        )

        text = json.dumps(usage_report(snapshot))

        for leaked in ('example.com', 'secret', 'used_credits', 'amount'):
            self.assertNotIn(leaked, text)


class TestHandleLine(unittest.TestCase):
    """Tests for the answers to the messages a client of revision 2026-07-28 sends."""

    def setUp(self):
        self.read_snapshot = MagicMock(return_value=_snapshot({'five_hour': {'utilization': 12.0, 'resets_at': _resets_in(hours=1)}}, last_success_time=time.time()))

    def _ask(self, method: str, params: dict[str, Any] | None = None, **kwargs: Any) -> dict[str, Any]:
        answer = handle_line(_request(method, params, **kwargs), self.read_snapshot)
        assert answer is not None
        return answer

    def test_discover_names_the_supported_version_and_the_tools_capability(self):
        result = self._ask('server/discover')['result']

        self.assertEqual(result['supportedVersions'], ['2026-07-28'])
        self.assertEqual(result['capabilities'], {'tools': {}})
        self.assertIn('get_usage', result['instructions'])

    def test_every_result_carries_the_fields_of_the_revision(self):
        """resultType is required on every result, and the server names itself in _meta."""
        for method, params in (('server/discover', None), ('tools/list', None), ('tools/call', {'name': 'get_usage'})):
            with self.subTest(method=method):
                result = self._ask(method, params)['result']
                self.assertEqual(result['resultType'], 'complete')
                self.assertEqual(result['_meta']['io.modelcontextprotocol/serverInfo']['name'], 'usage-monitor-for-claude')

    def test_cacheable_results_carry_caching_hints(self):
        """server/discover and tools/list must say how long they stay fresh and who may cache them."""
        for method in ('server/discover', 'tools/list'):
            with self.subTest(method=method):
                result = self._ask(method)['result']
                self.assertIsInstance(result['ttlMs'], int)
                self.assertGreaterEqual(result['ttlMs'], 0)
                self.assertEqual(result['cacheScope'], 'public')

    def test_tools_list_offers_only_the_read_only_tool(self):
        tools = self._ask('tools/list')['result']['tools']

        self.assertEqual([tool['name'] for tool in tools], ['get_usage'])
        self.assertIs(tools[0]['annotations']['readOnlyHint'], True)
        self.assertEqual(tools[0]['inputSchema']['properties'], {})

    def test_tool_call_returns_the_report_as_text(self):
        result = self._ask('tools/call', {'name': 'get_usage', 'arguments': {}})['result']

        self.assertIs(result['isError'], False)
        self.assertEqual(json.loads(result['content'][0]['text'])['quotas'][0]['used_percent'], 12.0)

    def test_tool_call_reads_the_cache_fresh_each_time(self):
        """Each call sees the state of that moment, not one from startup."""
        self.read_snapshot.assert_not_called()
        self._ask('tools/call', {'name': 'get_usage'})
        self._ask('tools/call', {'name': 'get_usage'})

        self.assertEqual(self.read_snapshot.call_count, 2)

    def test_request_id_is_echoed(self):
        for request_id in (7, 'abc'):
            with self.subTest(request_id=request_id):
                self.assertEqual(self._ask('server/discover', request_id=request_id)['id'], request_id)

    def test_unknown_tool(self):
        self.assertEqual(self._ask('tools/call', {'name': 'delete_everything'})['error']['code'], -32602)

    def test_tool_arguments_are_refused(self):
        """The input schema allows no properties, and tool inputs must be validated."""
        self.assertEqual(self._ask('tools/call', {'name': 'get_usage', 'arguments': {'account': 'other'}})['error']['code'], -32602)
        self.read_snapshot.assert_not_called()

    def test_unknown_method(self):
        """Including ping, which this revision removed."""
        for method in ('resources/list', 'ping'):
            with self.subTest(method=method):
                self.assertEqual(self._ask(method)['error']['code'], -32601)

    def test_unsupported_version_lists_the_supported_ones(self):
        """A recognized modern error, so a dual-era client retries instead of falling back to initialize."""
        for version in ('2025-11-25', '2099-01-01'):
            with self.subTest(version=version):
                error = self._ask('server/discover', meta={**_META, 'io.modelcontextprotocol/protocolVersion': version})['error']
                self.assertEqual(error['code'], -32022)
                self.assertEqual(error['data'], {'supported': ['2026-07-28'], 'requested': version})

    def test_unsupported_version_is_refused_before_the_tool_runs(self):
        self._ask('tools/call', {'name': 'get_usage'}, meta={**_META, 'io.modelcontextprotocol/protocolVersion': '2025-11-25'})

        self.read_snapshot.assert_not_called()

    def test_incomplete_modern_meta_is_invalid_params(self):
        """A request that names its version in _meta but lacks the rest of the required fields is malformed."""
        bodies = (
            {'jsonrpc': '2.0', 'id': 1, 'method': 'server/discover', 'params': {'_meta': {'io.modelcontextprotocol/protocolVersion': '2026-07-28'}}},
            {'jsonrpc': '2.0', 'id': 1, 'method': 'server/discover', 'params': {'_meta': {
                'io.modelcontextprotocol/protocolVersion': '2026-07-28', 'io.modelcontextprotocol/clientCapabilities': 'x'}}},
            {'jsonrpc': '2.0', 'id': 1, 'method': 'server/discover', 'params': {'_meta': {
                'io.modelcontextprotocol/protocolVersion': 20260728, 'io.modelcontextprotocol/clientCapabilities': {}}}},
        )
        for body in bodies:
            with self.subTest(body=body):
                self.assertEqual(handle_line(json.dumps(body).encode(), self.read_snapshot)['error']['code'], -32602)

    def test_request_without_a_version_is_served_as_a_handshake_revision(self):
        """server/discover exists only in 2026-07-28, so a request that names no version cannot reach it."""
        bodies = (
            {'jsonrpc': '2.0', 'id': 1, 'method': 'server/discover'},
            {'jsonrpc': '2.0', 'id': 1, 'method': 'server/discover', 'params': {'_meta': 'x'}},
            {'jsonrpc': '2.0', 'id': 1, 'method': 'server/discover', 'params': {'_meta': {'io.modelcontextprotocol/clientCapabilities': {}}}},
        )
        for body in bodies:
            with self.subTest(body=body):
                self.assertEqual(handle_line(json.dumps(body).encode(), self.read_snapshot)['error']['code'], -32601)

    def test_params_that_are_no_object_are_invalid(self):
        body = {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call', 'params': ['x']}

        self.assertEqual(handle_line(json.dumps(body).encode(), self.read_snapshot)['error']['code'], -32602)

    def test_client_info_is_optional(self):
        meta = {key: value for key, value in _META.items() if not key.endswith('clientInfo')}

        self.assertEqual(self._ask('server/discover', meta=meta)['result']['resultType'], 'complete')

    def test_notification_gets_no_answer(self):
        notification = json.dumps({'jsonrpc': '2.0', 'method': 'notifications/cancelled', 'params': {'requestId': 1}}).encode()

        self.assertIsNone(handle_line(notification, self.read_snapshot))

    def test_client_response_is_refused(self):
        """A client of this revision must not send JSON-RPC responses."""
        answer = handle_line(b'{"jsonrpc": "2.0", "id": 7, "result": {}}', self.read_snapshot)

        self.assertEqual(answer['error']['code'], -32600)

    def test_null_boolean_and_float_ids_are_refused(self):
        for request_id in (None, True, 1.5):
            with self.subTest(request_id=request_id):
                self.assertEqual(self._ask('server/discover', request_id=request_id)['error']['code'], -32600)

    def test_malformed_lines(self):
        for line, code in ((b'{not json', -32700), (b'\xff\xfe', -32700), (b'[' * 100000, -32700), (b'[{"jsonrpc": "2.0"}]', -32600), (b'42', -32600)):
            with self.subTest(line=line[:20]):
                answer = handle_line(line, self.read_snapshot)
                self.assertEqual(answer['error']['code'], code)
                self.assertIsNone(answer['id'])

    def test_failing_snapshot_reports_internal_error(self):
        """A bug in the report must not reach the client as a traceback."""
        self.read_snapshot.side_effect = RuntimeError('boom')

        with self.assertLogs('usage_monitor_for_claude.mcp_server', level='ERROR'):
            answer = self._ask('tools/call', {'name': 'get_usage'})

        self.assertEqual(answer['error']['code'], -32603)
        self.assertNotIn('boom', json.dumps(answer))


class TestFirstFetch(unittest.TestCase):
    """A tool call that reaches an application started a moment ago, before its first fetch has returned."""

    def setUp(self):
        patcher = patch('usage_monitor_for_claude.mcp_server._FIRST_FETCH_POLL_SECONDS', 0.0)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.loaded = _snapshot({'five_hour': {'utilization': 12.0, 'resets_at': _resets_in(hours=1)}}, last_success_time=time.time())

    def _report(self, read_snapshot: MagicMock) -> dict[str, Any]:
        answer = handle_line(_request('tools/call', {'name': 'get_usage'}), read_snapshot)
        assert answer is not None
        return json.loads(answer['result']['content'][0]['text'])

    def test_waits_for_the_first_fetch(self):
        """Answering at once would report no quotas at all to a model that asked for them."""
        read_snapshot = MagicMock(side_effect=[_snapshot(), _snapshot(), self.loaded])

        report = self._report(read_snapshot)

        self.assertEqual(report['quotas'][0]['used_percent'], 12.0)
        self.assertEqual(read_snapshot.call_count, 3)

    def test_failed_first_fetch_is_reported_at_once(self):
        """An expired login is the answer; waiting would not change it."""
        read_snapshot = MagicMock(return_value=_snapshot(last_error='no token'))

        self.assertEqual(self._report(read_snapshot)['error'], 'no token')
        read_snapshot.assert_called_once_with()

    def test_fetched_cache_is_answered_without_waiting(self):
        read_snapshot = MagicMock(return_value=self.loaded)

        self._report(read_snapshot)

        read_snapshot.assert_called_once_with()

    def test_gives_up_on_a_first_fetch_that_never_returns(self):
        """The call must end some time, and then says plainly that there are no numbers yet."""
        read_snapshot = MagicMock(return_value=_snapshot())
        with patch('usage_monitor_for_claude.mcp_server._FIRST_FETCH_WAIT_SECONDS', 0.05):
            report = self._report(read_snapshot)

        self.assertEqual(report['quotas'], [])
        self.assertIsNone(report['data_age_seconds'])
        self.assertGreater(read_snapshot.call_count, 1)

    def test_the_handshake_revisions_wait_too(self):
        read_snapshot = MagicMock(side_effect=[_snapshot(), self.loaded])
        line = json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call', 'params': {'name': 'get_usage'}}).encode()

        answer = handle_line(line, read_snapshot)

        assert answer is not None
        self.assertEqual(json.loads(answer['result']['content'][0]['text'])['quotas'][0]['used_percent'], 12.0)


def _handshake_request(method: str, params: dict[str, Any] | None = None, request_id: Any = 1) -> bytes:
    """Return one request line as a client of a handshake revision (Claude Desktop) writes it: no version in _meta."""
    message: dict[str, Any] = {'jsonrpc': '2.0', 'id': request_id, 'method': method}
    if params is not None:
        message['params'] = params
    return json.dumps(message).encode()


class TestHandshakeRevisions(unittest.TestCase):
    """Tests for clients of the revisions before 2026-07-28, which open with initialize."""

    def setUp(self):
        self.read_snapshot = MagicMock(return_value=_snapshot({'five_hour': {'utilization': 12.0, 'resets_at': _resets_in(hours=1)}}, last_success_time=time.time()))

    def _ask(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        answer = handle_line(_handshake_request(method, params), self.read_snapshot)
        assert answer is not None
        return answer

    def test_initialize_accepts_every_handshake_revision(self):
        for version in ('2025-11-25', '2025-06-18', '2025-03-26', '2024-11-05'):
            with self.subTest(version=version):
                result = self._ask('initialize', {'protocolVersion': version, 'capabilities': {}, 'clientInfo': {'name': 'c', 'version': '1'}})['result']
                self.assertEqual(result['protocolVersion'], version)

    def test_initialize_answers_an_unknown_version_with_the_newest_handshake_revision(self):
        """The client then decides whether it can speak that one; 2026-07-28 has no initialize to negotiate."""
        for version in ('2099-01-01', '2026-07-28', None):
            with self.subTest(version=version):
                self.assertEqual(self._ask('initialize', {'protocolVersion': version, 'capabilities': {}})['result']['protocolVersion'], '2025-11-25')

    def test_initialize_offers_the_tools_and_names_the_server(self):
        result = self._ask('initialize', {'protocolVersion': '2025-06-18', 'capabilities': {}})['result']

        self.assertEqual(result['capabilities'], {'tools': {}})
        self.assertEqual(result['serverInfo']['name'], 'usage-monitor-for-claude')
        self.assertIn('get_usage', result['instructions'])

    def test_initialized_notification_gets_no_answer(self):
        notification = json.dumps({'jsonrpc': '2.0', 'method': 'notifications/initialized'}).encode()

        self.assertIsNone(handle_line(notification, self.read_snapshot))

    def test_ping(self):
        self.assertEqual(self._ask('ping')['result'], {})

    def test_tools_list_and_call(self):
        tools = self._ask('tools/list', {})['result']['tools']
        result = self._ask('tools/call', {'name': 'get_usage', 'arguments': {}})['result']

        self.assertEqual([tool['name'] for tool in tools], ['get_usage'])
        self.assertIs(result['isError'], False)
        self.assertEqual(json.loads(result['content'][0]['text'])['quotas'][0]['used_percent'], 12.0)

    def test_tool_calls_need_no_initialize_on_the_same_connection(self):
        """The bridge reconnects when the application restarts, and the client does not repeat initialize on the new connection."""
        self.assertIn('result', self._ask('tools/call', {'name': 'get_usage'}))

    def test_a_progress_token_in_meta_does_not_make_a_request_modern(self):
        """Handshake clients use _meta too, just never with the protocol version in it."""
        result = self._ask('tools/call', {'name': 'get_usage', '_meta': {'progressToken': 1}})['result']

        self.assertIs(result['isError'], False)

    def test_results_carry_no_fields_of_the_stateless_revision(self):
        for method, params in (('initialize', {'protocolVersion': '2025-11-25'}), ('tools/list', None), ('tools/call', {'name': 'get_usage'})):
            with self.subTest(method=method):
                result = self._ask(method, params)['result']
                self.assertNotIn('resultType', result)
                self.assertNotIn('_meta', result)

    def test_tool_call_is_validated_as_in_the_stateless_revision(self):
        self.assertEqual(self._ask('tools/call', {'name': 'delete_everything'})['error']['code'], -32602)
        self.assertEqual(self._ask('tools/call', {'name': 'get_usage', 'arguments': {'account': 'other'}})['error']['code'], -32602)
        self.read_snapshot.assert_not_called()

    def test_unknown_method(self):
        self.assertEqual(self._ask('resources/list')['error']['code'], -32601)

    def test_both_revisions_side_by_side(self):
        """Claude Code speaks 2026-07-28 and Claude Desktop a handshake revision; one application serves both at once."""
        modern = handle_line(_request('tools/call', {'name': 'get_usage'}), self.read_snapshot)
        handshake = self._ask('tools/call', {'name': 'get_usage'})

        assert modern is not None
        self.assertEqual(modern['result']['resultType'], 'complete')
        self.assertEqual(modern['result']['content'], handshake['result']['content'])


class TestExpectsAnswer(unittest.TestCase):
    """The bridge waits for a reply exactly when this says so, so it must match handle_line()."""

    def test_only_a_well_formed_notification_goes_unanswered(self):
        cases = (
            (b'{"jsonrpc": "2.0", "method": "notifications/cancelled"}', False),
            (_request('server/discover'), True),
            (b'{"jsonrpc": "2.0", "id": 1, "result": {}}', True),
            (b'{"jsonrpc": "2.0"}', True),
            (b'{"jsonrpc": "2.0", "method": 5}', True),
            (b'not json', True),
            (b'[]', True),
        )
        for line, expected in cases:
            with self.subTest(line=line):
                self.assertIs(expects_answer(line), expected)
                self.assertIs(handle_line(line, MagicMock()) is not None, expected)


class TestLineReader(unittest.TestCase):
    """Tests for splitting the byte stream into messages."""

    def test_several_messages_in_one_chunk(self):
        reader = LineReader(_FakeConnection(b'a\nb\n'))

        self.assertEqual([reader.readline(), reader.readline(), reader.readline()], [b'a', b'b', None])

    def test_message_split_across_chunks(self):
        reader = LineReader(_FakeConnection(b'{"a"', b': 1}', b'\n'))

        self.assertEqual(reader.readline(), b'{"a": 1}')

    def test_crlf_is_stripped(self):
        self.assertEqual(LineReader(_FakeConnection(b'a\r\n')).readline(), b'a')

    def test_unterminated_tail_at_close_is_dropped(self):
        """A message is complete only with its newline."""
        self.assertIsNone(LineReader(_FakeConnection(b'{"half"')).readline())

    def test_message_at_the_limit_is_accepted(self):
        line = b'x' * MAX_MESSAGE_BYTES

        self.assertEqual(LineReader(_FakeConnection(line + b'\n')).readline(), line)

    def test_message_over_the_limit_is_refused_without_newline(self):
        """Without a limit a client could grow the buffer until the application runs out of memory."""
        chunks = [b'x' * 4096] * (MAX_MESSAGE_BYTES // 4096 + 2)

        with self.assertRaises(ValueError):
            LineReader(_FakeConnection(*chunks)).readline()

    def test_message_over_the_limit_is_refused_with_newline(self):
        with self.assertRaises(ValueError):
            LineReader(_FakeConnection(b'x' * (MAX_MESSAGE_BYTES + 1) + b'\n')).readline()


class TestServeConnection(unittest.TestCase):
    """Tests for serving one client."""

    def setUp(self):
        self.read_snapshot = MagicMock(return_value=_snapshot())

    def test_answers_in_order_one_line_each(self):
        connection = _FakeConnection(_request('server/discover', request_id=1) + b'\n' + _request('tools/list', request_id=2) + b'\n')

        _serve_connection(connection, self.read_snapshot)

        self.assertEqual([answer['id'] for answer in connection.answers()], [1, 2])
        self.assertTrue(connection.sent.endswith(b'\n'))
        self.assertEqual(connection.sent.count(b'\n'), 2)
        self.assertTrue(connection.closed)

    def test_notifications_and_blank_lines_produce_nothing(self):
        connection = _FakeConnection(b'\n  \n{"jsonrpc": "2.0", "method": "notifications/cancelled"}\n')

        _serve_connection(connection, self.read_snapshot)

        self.assertEqual(connection.sent, b'')

    def test_oversized_message_gets_an_error_and_the_connection_ends(self):
        connection = _FakeConnection(b'x' * (MAX_MESSAGE_BYTES + 1) + b'\n', _request('server/discover') + b'\n')

        _serve_connection(connection, self.read_snapshot)

        answers = connection.answers()
        self.assertEqual(len(answers), 1)
        self.assertEqual(answers[0]['error']['code'], -32700)
        self.assertTrue(connection.closed)

    def test_broken_connection_is_closed_quietly(self):
        connection = _FakeConnection(_request('server/discover') + b'\n', fail_on_send=True)

        _serve_connection(connection, self.read_snapshot)

        self.assertTrue(connection.closed)


class _StopServing(BaseException):
    """Ends the otherwise endless accept loop of _serve() in a test."""


class TestServe(unittest.TestCase):
    """Tests for opening the channel and accepting clients."""

    def test_retries_until_the_channel_is_free(self):
        """A restart starts the new process while the old one still serves the channel."""
        listener = MagicMock()
        listener.accept.side_effect = _StopServing
        with patch('usage_monitor_for_claude.mcp_server.LocalChannelListener', side_effect=[OSError(), OSError(), listener]) as mock_listener, \
             patch('usage_monitor_for_claude.mcp_server.time.sleep') as mock_sleep:
            with self.assertLogs('usage_monitor_for_claude.mcp_server', level='INFO') as logs, self.assertRaises(_StopServing):
                _serve(MagicMock())

        self.assertEqual(mock_listener.call_count, 3)
        self.assertEqual(mock_sleep.call_count, 2)
        self.assertEqual(mock_listener.call_args.args[0], mcp_channel_name())
        self.assertIn(mcp_channel_name(), logs.output[0])

    def test_gives_up_when_the_channel_stays_in_use(self):
        """Must not raise: the rest of the application is unaffected."""
        with patch('usage_monitor_for_claude.mcp_server.LocalChannelListener', side_effect=OSError()) as mock_listener, \
             patch('usage_monitor_for_claude.mcp_server.time.sleep'):
            with self.assertLogs('usage_monitor_for_claude.mcp_server', level='WARNING'):
                _serve(MagicMock())

        self.assertEqual(mock_listener.call_count, _LISTEN_ATTEMPTS)

    def test_each_client_is_served_on_its_own_daemon_thread(self):
        listener = MagicMock()
        first, second = MagicMock(), MagicMock()
        listener.accept.side_effect = [first, OSError(), second, _StopServing()]
        read_snapshot = MagicMock()
        with patch('usage_monitor_for_claude.mcp_server.LocalChannelListener', return_value=listener), \
             patch('usage_monitor_for_claude.mcp_server.threading.Thread') as mock_thread:
            with self.assertRaises(_StopServing):
                _serve(read_snapshot)

        self.assertEqual([call.kwargs['args'] for call in mock_thread.call_args_list], [(first, read_snapshot), (second, read_snapshot)])
        self.assertTrue(all(call.kwargs['daemon'] for call in mock_thread.call_args_list))

    def test_start_uses_a_daemon_thread(self):
        """The server must never keep the process alive after Quit."""
        with patch('usage_monitor_for_claude.mcp_server.threading.Thread') as mock_thread:
            start_mcp_server(MagicMock())

        self.assertIs(mock_thread.call_args.kwargs['daemon'], True)
        mock_thread.return_value.start.assert_called_once()

    def test_each_account_gets_its_own_channel(self):
        """A --config-dir instance serves its own account, so it must not share the default one's channel."""
        with patch('usage_monitor_for_claude.mcp_server.config_dir_suffix', return_value=''):
            default = mcp_channel_name()
        with patch('usage_monitor_for_claude.mcp_server.config_dir_suffix', return_value='_0123456789ab'):
            secondary = mcp_channel_name()

        self.assertEqual(default, 'usage-monitor-for-claude-mcp')
        self.assertEqual(secondary, 'usage-monitor-for-claude-mcp_0123456789ab')


@unittest.skipUnless(_REAL_CHANNEL_AVAILABLE, 'this system offers neither named pipes nor Unix domain sockets')
class TestRealChannel(unittest.TestCase):
    """One exchange over the real channel of the running system."""

    def test_request_and_answer(self):
        from usage_monitor_for_claude.platforms.channel import LocalChannelListener, open_local_channel

        name = f'usage-monitor-for-claude-test-{uuid.uuid4().hex[:12]}'
        listener = LocalChannelListener(name)
        read_snapshot = MagicMock(return_value=_snapshot())
        threading.Thread(target=lambda: _serve_connection(listener.accept(), read_snapshot), daemon=True).start()

        client = open_local_channel(name)
        self.addCleanup(client.close)
        client.sendall(_request('server/discover') + b'\n')

        self.assertEqual(json.loads(LineReader(client).readline())['result']['supportedVersions'], ['2026-07-28'])


class TestAppWiring(unittest.TestCase):
    """Every instance serves its own account's channel."""

    def test_default_and_secondary_instances_both_serve(self):
        for is_default in (True, False):
            with self.subTest(is_default=is_default):
                with patch('usage_monitor_for_claude.app.pystray'), \
                     patch('usage_monitor_for_claude.app.create_icon_image'), \
                     patch('usage_monitor_for_claude.app.taskbar_uses_light_theme', return_value=False), \
                     patch('usage_monitor_for_claude.app.install_tray_activate_handler'), \
                     patch('usage_monitor_for_claude.app.is_default_config_dir', return_value=is_default):
                    app = UsageMonitorForClaude()

                with patch('usage_monitor_for_claude.app.autostart_supported', return_value=False), \
                     patch('usage_monitor_for_claude.app.api_headers', return_value={'x': 'y'}), \
                     patch('usage_monitor_for_claude.app.threading.Thread'), \
                     patch('usage_monitor_for_claude.app.start_mcp_server') as mock_start, \
                     patch.object(app, 'poll_loop'):
                    app._on_icon_ready(MagicMock())

                mock_start.assert_called_once()
                self.assertEqual(mock_start.call_args.args[0](), app.cache.snapshot)


if __name__ == '__main__':
    unittest.main()

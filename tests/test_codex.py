"""
Codex Provider Tests
====================

Tests for normalization of the Codex app-server rate-limit response.
"""
from __future__ import annotations

import json
import unittest

from usage_monitor_for_claude.codex import _normalize_response, _request_rate_limits


class _FakeStdin:
    def __init__(self) -> None:
        self.writes: list[str] = []

    def write(self, value: str) -> None:
        self.writes.append(value)

    def flush(self) -> None:
        pass


class _FakeProcess:
    def __init__(self, responses: list[dict[str, object]]) -> None:
        self.stdin = _FakeStdin()
        self.stdout = [json.dumps(response) + '\n' for response in responses]


class TestRequestRateLimits(unittest.TestCase):
    """Verify the provider extracts the matching JSON-RPC response."""

    def test_ignores_notifications_and_unrelated_responses(self):
        process = _FakeProcess([
            {'method': 'remoteControl/status/changed', 'params': {}},
            {'id': 1, 'result': {'userAgent': 'codex'}},
            {'id': 2, 'result': {'rateLimits': {'planType': 'plus'}}},
        ])

        result = _request_rate_limits(process)

        self.assertEqual(result, {'rateLimits': {'planType': 'plus'}})
        sent = [json.loads(message) for message in process.stdin.writes]
        self.assertEqual([message.get('method') for message in sent], [
            'initialize', 'initialized', 'account/rateLimits/read',
        ])


class TestNormalizeResponseShape(unittest.TestCase):
    """Verify current Codex response variants become quota entries."""

    def test_normalizes_current_app_server_snapshot(self):
        result = _normalize_response({
            'rateLimits': {
                'limitId': 'codex',
                'primary': {'usedPercent': 3, 'windowDurationMins': 300, 'resetsAt': 1_800_000_000},
                'secondary': {'usedPercent': 17, 'windowDurationMins': 10_080, 'resetsAt': 1_800_100_000},
                'credits': {'hasCredits': False, 'unlimited': False, 'balance': '0'},
                'planType': 'plus',
            },
            'rateLimitsByLimitId': {
                'codex': {
                    'primary': {'usedPercent': 3, 'windowDurationMins': 300, 'resetsAt': 1_800_000_000},
                    'secondary': {'usedPercent': 17, 'windowDurationMins': 10_080, 'resetsAt': 1_800_100_000},
                    'credits': {'hasCredits': False, 'unlimited': False, 'balance': '0'},
                    'planType': 'plus',
                },
            },
        })

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result['five_hour']['utilization'], 3.0)
        self.assertEqual(result['seven_day']['utilization'], 17.0)
        self.assertEqual(result['codex_plan'], 'plus')
        self.assertEqual(result['codex_credits']['balance'], '0')

    def test_falls_back_to_legacy_snapshot_when_limit_buckets_are_null(self):
        result = _normalize_response({
            'rateLimitsByLimitId': None,
            'rateLimits': {'primary': {'usedPercent': 12, 'resetsAt': 1_800_000_000}},
        })

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result['five_hour']['utilization'], 12.0)

    def test_ignores_windows_with_null_reset_timestamp(self):
        result = _normalize_response({
            'rateLimits': {
                'primary': {'usedPercent': 12, 'resetsAt': None},
                'secondary': {'usedPercent': 34, 'resetsAt': 1_800_100_000},
            },
        })

        self.assertIsNotNone(result)
        assert result is not None
        self.assertNotIn('five_hour', result)
        self.assertEqual(result['seven_day']['utilization'], 34.0)


class TestNormalizeResponse(unittest.TestCase):
    """Verify Codex rate-limit snapshots become quota entries."""

    def test_normalizes_primary_and_secondary_windows(self):
        result = _normalize_response({
            'rateLimits': {
                'primary': {'usedPercent': 12, 'windowDurationMins': 300, 'resetsAt': 1_800_000_000},
                'secondary': {'usedPercent': 34, 'windowDurationMins': 10_080, 'resetsAt': 1_800_100_000},
                'planType': 'plus',
            },
        })

        self.assertEqual(result['five_hour']['utilization'], 12.0)
        self.assertEqual(result['seven_day']['utilization'], 34.0)
        self.assertEqual(result['codex_plan'], 'plus')
        self.assertTrue(result['five_hour']['resets_at'].endswith('Z'))

    def test_prefers_codex_limit_bucket(self):
        result = _normalize_response({
            'rateLimits': {'primary': {'usedPercent': 99, 'resetsAt': 1_800_000_000}},
            'rateLimitsByLimitId': {
                'codex': {'primary': {'usedPercent': 5, 'resetsAt': 1_800_000_000}},
            },
        })

        self.assertEqual(result['five_hour']['utilization'], 5.0)

    def test_rejects_missing_windows(self):
        self.assertIsNone(_normalize_response({'rateLimits': {'planType': 'plus'}}))


if __name__ == '__main__':
    unittest.main()

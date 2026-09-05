"""
Cache Tests
=============

Unit tests for UsageCache: lock, cooldown, error tracking, token refresh,
snapshot consistency, and state management.
"""
from __future__ import annotations

import unittest
from unittest.mock import patch

from usage_monitor_for_claude.cache import CacheSnapshot, UpdateResult, UsageCache
from usage_monitor_for_claude.claude_cli import RefreshResult

_SUCCESS_DATA = {'five_hour': {'utilization': 42.0}}
_ERROR_DATA = {'error': 'server down'}
_AUTH_ERROR_DATA = {'error': 'expired', 'auth_error': True}
_SERVER_MSG_DATA = {'error': 'HTTP 429', 'server_message': 'Rate limited.'}


def _make_cache() -> UsageCache:
    """Create a fresh UsageCache instance."""
    return UsageCache()


# ---------------------------------------------------------------------------
# Lock behavior
# ---------------------------------------------------------------------------

class TestLockBehavior(unittest.TestCase):
    """Tests for non-blocking lock acquisition in update()."""

    @patch('usage_monitor_for_claude.cache.fetch_usage', return_value=_SUCCESS_DATA)
    def test_concurrent_update_skipped(self, _mock_fetch):
        """Second update() returns None data when lock is held."""
        cache = _make_cache()
        # Manually acquire the lock to simulate a concurrent call
        cache._lock.acquire()
        try:
            result = cache.update()
            self.assertIsNone(result.data)
        finally:
            cache._lock.release()

    @patch('usage_monitor_for_claude.cache.fetch_usage', return_value=_SUCCESS_DATA)
    def test_update_succeeds_when_lock_free(self, _mock_fetch):
        """update() returns data when lock is not held."""
        cache = _make_cache()
        result = cache.update()
        self.assertIsNotNone(result.data)

    def test_lock_released_on_exception(self):
        """Lock is released even when fetch_usage raises an exception."""
        cache = _make_cache()
        with patch('usage_monitor_for_claude.cache.fetch_usage', side_effect=RuntimeError('boom')):
            with self.assertRaises(RuntimeError):
                cache.update()

        # Lock must be free for the next call
        self.assertFalse(cache._lock.locked())

    def test_refreshing_reset_on_exception(self):
        """refreshing is False after fetch_usage raises an exception."""
        cache = _make_cache()
        with patch('usage_monitor_for_claude.cache.fetch_usage', side_effect=RuntimeError('boom')):
            with self.assertRaises(RuntimeError):
                cache.update()

        self.assertFalse(cache.refreshing)

    @patch('usage_monitor_for_claude.cache.fetch_usage', return_value=_AUTH_ERROR_DATA)
    @patch('usage_monitor_for_claude.cache.read_access_token', return_value='token-abc')
    def test_refreshing_reset_on_refresh_exception(self, _mock_token, _mock_fetch):
        """refreshing is False when _try_token_refresh raises an exception."""
        cache = _make_cache()
        with patch('usage_monitor_for_claude.cache.refresh_token', side_effect=RuntimeError('cli crash')):
            with self.assertRaises(RuntimeError):
                cache.update()

        self.assertFalse(cache.refreshing)
        self.assertFalse(cache._lock.locked())


# ---------------------------------------------------------------------------
# Cooldown behavior
# ---------------------------------------------------------------------------

class TestCooldownBehavior(unittest.TestCase):
    """Tests for POLL_FAST cooldown between updates."""

    @patch('usage_monitor_for_claude.cache.fetch_usage', return_value=_SUCCESS_DATA)
    def test_second_call_within_cooldown_skipped(self, mock_fetch):
        """update() within cooldown returns None data."""
        cache = _make_cache()
        cache.update()
        mock_fetch.reset_mock()

        result = cache.update()
        self.assertIsNone(result.data)
        mock_fetch.assert_not_called()

    @patch('usage_monitor_for_claude.cache.fetch_usage', return_value=_SUCCESS_DATA)
    @patch('usage_monitor_for_claude.cache.time')
    def test_call_after_cooldown_proceeds(self, mock_time, mock_fetch):
        """update() after cooldown expires fetches fresh data."""
        cache = _make_cache()
        mock_time.time.return_value = 1000.0
        cache.update()
        mock_fetch.reset_mock()

        mock_time.time.return_value = 1000.0 + 121  # > POLL_FAST (120s)
        result = cache.update()
        self.assertIsNotNone(result.data)

    def test_first_call_always_proceeds(self):
        """First update() always proceeds (no prior success time)."""
        cache = _make_cache()
        with patch('usage_monitor_for_claude.cache.fetch_usage', return_value=_SUCCESS_DATA):
            result = cache.update()
        self.assertIsNotNone(result.data)

    @patch('usage_monitor_for_claude.cache.fetch_usage', return_value=_ERROR_DATA)
    def test_no_cooldown_after_error(self, mock_fetch):
        """Cooldown only applies after success, not after error."""
        cache = _make_cache()
        cache.update()
        mock_fetch.reset_mock()

        # Second call should proceed since error doesn't set last_success_time
        result = cache.update()
        self.assertIsNotNone(result.data)
        mock_fetch.assert_called_once()

    @patch('usage_monitor_for_claude.cache.fetch_usage', return_value=_SUCCESS_DATA)
    def test_force_bypasses_cooldown(self, mock_fetch):
        """update(force=True) fetches again within the cooldown window."""
        cache = _make_cache()
        cache.update()
        mock_fetch.reset_mock()

        result = cache.update(force=True)
        self.assertIsNotNone(result.data)
        mock_fetch.assert_called_once()

    @patch('usage_monitor_for_claude.cache.fetch_usage', return_value=_SUCCESS_DATA)
    @patch('usage_monitor_for_claude.cache.time')
    def test_backward_clock_jump_does_not_stall_cooldown(self, mock_time, mock_fetch):
        """A backward clock jump (manual correction, VM restore) must not block
        fetches until the wall clock catches up with the pre-jump timestamp."""
        cache = _make_cache()
        mock_time.time.return_value = 10000.0
        cache.update()
        mock_fetch.reset_mock()

        # Clock jumps back one hour; without a clamp the cooldown would block
        # for that whole hour (time since "last success" stays negative).
        mock_time.time.return_value = 6400.0
        result = cache.update()

        self.assertIsNotNone(result.data)
        mock_fetch.assert_called_once()

    @patch('usage_monitor_for_claude.cache.fetch_usage', return_value=_SUCCESS_DATA)
    @patch('usage_monitor_for_claude.cache.time')
    def test_backward_clock_jump_caps_rate_limit_backoff(self, mock_time, _mock_fetch):
        """After a backward clock jump, the remaining 429 backoff is capped to
        MAX_BACKOFF instead of lasting until the pre-jump timestamp."""
        cache = _make_cache()
        cache._rate_limit_until = 10000.0

        mock_time.time.return_value = 5000.0
        cache.update()

        self.assertLessEqual(cache._rate_limit_until - 5000.0, 900)


# ---------------------------------------------------------------------------
# Success state management
# ---------------------------------------------------------------------------

class TestSuccessState(unittest.TestCase):
    """Tests for state updates after successful API calls."""

    @patch('usage_monitor_for_claude.cache.fetch_usage', return_value=_SUCCESS_DATA)
    def test_success_stores_usage_data(self, _mock):
        cache = _make_cache()
        cache.update()
        self.assertEqual(cache.usage, _SUCCESS_DATA)

    @patch('usage_monitor_for_claude.cache.fetch_usage', return_value=_SUCCESS_DATA)
    def test_success_sets_last_success_time(self, _mock):
        cache = _make_cache()
        self.assertIsNone(cache.last_success_time)
        cache.update()
        self.assertIsNotNone(cache.last_success_time)

    @patch('usage_monitor_for_claude.cache.fetch_usage', return_value=_SUCCESS_DATA)
    def test_success_clears_error(self, _mock):
        cache = _make_cache()
        cache._last_error = 'old error'
        cache.update()
        self.assertIsNone(cache.last_error)

    @patch('usage_monitor_for_claude.cache.fetch_usage', return_value=_SUCCESS_DATA)
    def test_success_resets_consecutive_errors(self, _mock):
        cache = _make_cache()
        cache._consecutive_errors = 5
        cache.update()
        self.assertEqual(cache.consecutive_errors, 0)

    @patch('usage_monitor_for_claude.cache.fetch_usage', return_value=_SUCCESS_DATA)
    def test_success_clears_refreshing_flag(self, _mock):
        cache = _make_cache()
        cache.update()
        self.assertFalse(cache.refreshing)

    @patch('usage_monitor_for_claude.cache.fetch_usage', return_value=_SUCCESS_DATA)
    def test_success_increments_version_twice(self, _mock):
        """Version increments once for refreshing=True, once for success."""
        cache = _make_cache()
        cache.update()
        # refreshing sets version to 1, _record_success sets version to 2
        self.assertEqual(cache.version, 2)

    @patch('usage_monitor_for_claude.cache.read_access_token', return_value='tok-a')
    @patch('usage_monitor_for_claude.cache.fetch_usage', return_value=_SUCCESS_DATA)
    def test_success_reports_the_token_used(self, _mock_fetch, _mock_token):
        """The result carries the token the request was sent with, so the caller
        can tell whether the data still matches the current credentials."""
        cache = _make_cache()

        result = cache.update()

        self.assertEqual(result.token, 'tok-a')

    @patch('usage_monitor_for_claude.cache.refresh_token')
    @patch('usage_monitor_for_claude.cache.read_access_token', side_effect=['tok-a', 'tok-b'])
    @patch('usage_monitor_for_claude.cache.fetch_usage')
    def test_retry_after_auth_error_reports_the_retry_token(self, mock_fetch, _mock_token, mock_refresh):
        """After a 401 recovered on a changed token, the result carries the retry's token."""
        mock_fetch.side_effect = [_AUTH_ERROR_DATA, _SUCCESS_DATA]
        cache = _make_cache()

        result = cache.update()

        mock_refresh.assert_not_called()
        self.assertEqual(result.data, _SUCCESS_DATA)
        self.assertEqual(result.token, 'tok-b')

    @patch('usage_monitor_for_claude.cache.read_access_token', return_value='new-token')
    @patch('usage_monitor_for_claude.cache.fetch_usage', return_value=_SUCCESS_DATA)
    def test_success_clears_failed_token_guard(self, _mock_fetch, _mock_token):
        """Successful response clears a stale _last_failed_token."""
        cache = _make_cache()
        cache._last_failed_token = 'stale-token'
        cache.update()
        self.assertIsNone(cache._last_failed_token)


# ---------------------------------------------------------------------------
# Error state management
# ---------------------------------------------------------------------------

class TestErrorState(unittest.TestCase):
    """Tests for state updates after API errors."""

    @patch('usage_monitor_for_claude.cache.fetch_usage', return_value=_ERROR_DATA)
    def test_error_increments_consecutive_errors(self, _mock):
        cache = _make_cache()
        cache.update()
        self.assertEqual(cache.consecutive_errors, 1)
        cache.update()
        self.assertEqual(cache.consecutive_errors, 2)

    @patch('usage_monitor_for_claude.cache.fetch_usage', return_value=_ERROR_DATA)
    def test_error_sets_last_error(self, _mock):
        cache = _make_cache()
        cache.update()
        self.assertEqual(cache.last_error, 'server down')

    @patch('usage_monitor_for_claude.cache.fetch_usage', return_value=_SERVER_MSG_DATA)
    def test_server_message_appended_to_last_error(self, _mock):
        cache = _make_cache()
        cache.update()
        self.assertEqual(cache.last_error, 'HTTP 429\nRate limited.')

    @patch('usage_monitor_for_claude.cache.fetch_usage', return_value={'error': 'HTTP 500'})
    def test_no_server_message_leaves_error_unchanged(self, _mock):
        cache = _make_cache()
        cache.update()
        self.assertEqual(cache.last_error, 'HTTP 500')

    @patch('usage_monitor_for_claude.cache.fetch_usage', return_value=_ERROR_DATA)
    def test_error_preserves_cached_usage(self, _mock):
        """API error does not overwrite previously cached successful data."""
        cache = _make_cache()
        cache._usage = {'five_hour': {'utilization': 42.0}}
        cache.update()
        self.assertEqual(cache.usage, {'five_hour': {'utilization': 42.0}})

    @patch('usage_monitor_for_claude.cache.fetch_usage', return_value=_ERROR_DATA)
    def test_error_clears_refreshing_flag(self, _mock):
        cache = _make_cache()
        cache.update()
        self.assertFalse(cache.refreshing)

    @patch('usage_monitor_for_claude.cache.fetch_usage', return_value=_ERROR_DATA)
    def test_error_increments_version_twice(self, _mock):
        """Version increments for refreshing=True, then for error completion."""
        cache = _make_cache()
        cache.update()
        self.assertEqual(cache.version, 2)


# ---------------------------------------------------------------------------
# Refreshing flag
# ---------------------------------------------------------------------------

class TestRefreshingFlag(unittest.TestCase):
    """Tests for the refreshing flag behavior during update."""

    def test_refreshing_true_during_api_call(self):
        """refreshing is True while fetch_usage is executing."""
        cache = _make_cache()
        observed = []

        def capture():
            observed.append(cache.refreshing)
            return _SUCCESS_DATA

        with patch('usage_monitor_for_claude.cache.fetch_usage', side_effect=capture):
            cache.update()

        self.assertTrue(observed[0])
        self.assertFalse(cache.refreshing)


# ---------------------------------------------------------------------------
# Failed token guard
# ---------------------------------------------------------------------------

class TestFailedTokenGuard(unittest.TestCase):
    """Tests for _last_failed_token preventing repeated auth failures."""

    @patch('usage_monitor_for_claude.cache.read_access_token', return_value='same-token')
    @patch('usage_monitor_for_claude.cache.fetch_usage', return_value=_AUTH_ERROR_DATA)
    def test_same_token_skips_update(self, mock_fetch, _mock_token):
        """When current token matches last failed token, update is skipped."""
        cache = _make_cache()
        cache._last_failed_token = 'same-token'

        result = cache.update()
        self.assertIsNone(result.data)
        mock_fetch.assert_not_called()

    @patch('usage_monitor_for_claude.cache.read_access_token', return_value='new-token')
    @patch('usage_monitor_for_claude.cache.fetch_usage', return_value=_SUCCESS_DATA)
    def test_new_token_proceeds(self, mock_fetch, _mock_token):
        """When current token differs from failed token, update proceeds."""
        cache = _make_cache()
        cache._last_failed_token = 'old-token'

        result = cache.update()
        self.assertIsNotNone(result.data)
        mock_fetch.assert_called_once()

    @patch('usage_monitor_for_claude.cache.read_access_token', return_value='new-token')
    @patch('usage_monitor_for_claude.cache.fetch_usage', return_value=_SUCCESS_DATA)
    def test_new_token_clears_failed_guard(self, _mock_fetch, _mock_token):
        """Proceeding with a new token clears the failed token guard."""
        cache = _make_cache()
        cache._last_failed_token = 'old-token'
        cache.update()
        self.assertIsNone(cache._last_failed_token)

    @patch('usage_monitor_for_claude.cache.read_access_token', return_value='token-123')
    @patch('usage_monitor_for_claude.cache.fetch_usage', return_value=_AUTH_ERROR_DATA)
    @patch('usage_monitor_for_claude.cache.refresh_token')
    def test_auth_error_sets_failed_token_when_no_refresh(self, mock_refresh, _mock_fetch, _mock_token):
        """Auth error without successful refresh sets failed token guard."""
        mock_refresh.return_value = RefreshResult(success=False, updated=False, old_version='', new_version='', error='CLI not found')
        cache = _make_cache()
        cache.update()
        self.assertEqual(cache._last_failed_token, 'token-123')


# ---------------------------------------------------------------------------
# Rate limit guard
# ---------------------------------------------------------------------------

class TestRateLimitGuard(unittest.TestCase):
    """Tests for _rate_limit_until preventing calls during 429 backoff."""

    @patch('usage_monitor_for_claude.cache.fetch_usage', return_value={'error': 'HTTP 429', 'rate_limited': True})
    @patch('usage_monitor_for_claude.cache.time')
    def test_rate_limit_blocks_subsequent_call(self, mock_time, mock_fetch):
        """After a 429, non-forced update within the backoff window is skipped."""
        cache = _make_cache()
        mock_time.time.return_value = 1000.0
        cache.update()
        mock_fetch.reset_mock()

        # Still within backoff window
        mock_time.time.return_value = 1050.0
        result = cache.update()
        self.assertIsNone(result.data)
        mock_fetch.assert_not_called()

    @patch('usage_monitor_for_claude.cache.fetch_usage')
    @patch('usage_monitor_for_claude.cache.time')
    def test_force_bypasses_rate_limit_backoff(self, mock_time, mock_fetch):
        """update(force=True) fetches even while the 429 backoff window is active."""
        mock_fetch.return_value = {'error': 'HTTP 429', 'rate_limited': True}
        cache = _make_cache()
        mock_time.time.return_value = 1000.0
        cache.update()
        mock_fetch.reset_mock()

        # Still within backoff, but a forced fetch proceeds anyway
        mock_fetch.return_value = _SUCCESS_DATA
        mock_time.time.return_value = 1050.0
        result = cache.update(force=True)
        self.assertIsNotNone(result.data)
        mock_fetch.assert_called_once()

    @patch('usage_monitor_for_claude.cache.fetch_usage')
    @patch('usage_monitor_for_claude.cache.time')
    def test_rate_limit_expires(self, mock_time, mock_fetch):
        """After the backoff window expires, update proceeds normally."""
        mock_fetch.return_value = {'error': 'HTTP 429', 'rate_limited': True}
        cache = _make_cache()
        mock_time.time.return_value = 1000.0
        cache.update()

        mock_fetch.return_value = _SUCCESS_DATA
        mock_time.time.return_value = 1000.0 + 190  # Well past POLL_INTERVAL (180s)
        result = cache.update()
        self.assertIsNotNone(result.data)

    @patch('usage_monitor_for_claude.cache.fetch_usage', return_value={'error': 'HTTP 429', 'rate_limited': True, 'retry_after': 300})
    @patch('usage_monitor_for_claude.cache.time')
    def test_retry_after_used_for_backoff(self, mock_time, mock_fetch):
        """Rate limit with retry_after uses that value (clamped to at least POLL_INTERVAL)."""
        cache = _make_cache()
        mock_time.time.return_value = 1000.0
        cache.update()
        mock_fetch.reset_mock()

        # At 200s: within retry_after (300s) window
        mock_time.time.return_value = 1200.0
        result = cache.update()
        self.assertIsNone(result.data)

        # At 301s: past retry_after window
        mock_time.time.return_value = 1301.0
        mock_fetch.return_value = _SUCCESS_DATA
        result = cache.update()
        self.assertIsNotNone(result.data)

    @patch('usage_monitor_for_claude.cache.fetch_usage', return_value={'error': 'HTTP 429', 'rate_limited': True, 'retry_after': 86400})
    @patch('usage_monitor_for_claude.cache.time')
    def test_retry_after_capped_by_max_backoff(self, mock_time, mock_fetch):
        """Unreasonably large retry_after is capped to MAX_BACKOFF (900s)."""
        cache = _make_cache()
        mock_time.time.return_value = 1000.0
        cache.update()
        mock_fetch.reset_mock()

        # At 901s: past MAX_BACKOFF cap
        mock_time.time.return_value = 1901.0
        mock_fetch.return_value = _SUCCESS_DATA
        result = cache.update()
        self.assertIsNotNone(result.data)

    @patch('usage_monitor_for_claude.cache.fetch_usage')
    @patch('usage_monitor_for_claude.cache.time')
    def test_exponential_backoff_on_repeated_429(self, mock_time, mock_fetch):
        """Repeated 429s without retry_after use exponential backoff."""
        mock_fetch.return_value = {'error': 'HTTP 429', 'rate_limited': True}
        cache = _make_cache()

        # First 429: backoff = POLL_INTERVAL (180s)
        mock_time.time.return_value = 1000.0
        cache.update()
        self.assertEqual(cache.consecutive_errors, 1)

        # At 190s: past first backoff (180s) - proceed to second 429
        mock_time.time.return_value = 1190.0
        cache.update()
        self.assertEqual(cache.consecutive_errors, 2)

        mock_fetch.reset_mock()
        # At 250s: within second backoff (360s from 1190)
        mock_time.time.return_value = 1440.0
        result = cache.update()
        self.assertIsNone(result.data)
        mock_fetch.assert_not_called()

    @patch('usage_monitor_for_claude.cache.fetch_usage')
    @patch('usage_monitor_for_claude.cache.time')
    def test_success_clears_rate_limit(self, mock_time, mock_fetch):
        """Successful response clears the rate limit guard."""
        mock_fetch.return_value = {'error': 'HTTP 429', 'rate_limited': True}
        cache = _make_cache()
        mock_time.time.return_value = 1000.0
        cache.update()

        # Wait past backoff, then succeed
        mock_time.time.return_value = 1200.0
        mock_fetch.return_value = _SUCCESS_DATA
        cache.update()

        mock_fetch.reset_mock()
        # Next call after cooldown should proceed (rate limit cleared by success)
        mock_time.time.return_value = 1200.0 + 121
        result = cache.update()
        self.assertIsNotNone(result.data)

    @patch('usage_monitor_for_claude.cache.fetch_usage', return_value=_ERROR_DATA)
    @patch('usage_monitor_for_claude.cache.time')
    def test_non_429_error_does_not_set_rate_limit(self, mock_time, mock_fetch):
        """Non-rate-limit errors do not trigger the rate limit guard."""
        cache = _make_cache()
        mock_time.time.return_value = 1000.0
        cache.update()
        mock_fetch.reset_mock()

        # Immediate second call should proceed (no rate limit, no success cooldown)
        mock_time.time.return_value = 1001.0
        result = cache.update()
        self.assertIsNotNone(result.data)
        mock_fetch.assert_called_once()


# ---------------------------------------------------------------------------
# rate_limit_remaining property
# ---------------------------------------------------------------------------

class TestRateLimitRemaining(unittest.TestCase):
    """Tests for the rate_limit_remaining property."""

    @patch('usage_monitor_for_claude.cache.time')
    def test_active_rate_limit(self, mock_time):
        """Returns remaining seconds when rate limit is active."""
        cache = _make_cache()
        cache._rate_limit_until = 1300.0
        mock_time.time.return_value = 1000.0
        self.assertAlmostEqual(cache.rate_limit_remaining, 300.0)

    @patch('usage_monitor_for_claude.cache.time')
    def test_expired_rate_limit(self, mock_time):
        """Returns 0 when rate limit has expired."""
        cache = _make_cache()
        cache._rate_limit_until = 1000.0
        mock_time.time.return_value = 1100.0
        self.assertEqual(cache.rate_limit_remaining, 0)

    def test_no_rate_limit(self):
        """Returns 0 when no rate limit was ever set."""
        cache = _make_cache()
        self.assertEqual(cache.rate_limit_remaining, 0)


# ---------------------------------------------------------------------------
# Token refresh
# ---------------------------------------------------------------------------

class TestTokenRefresh(unittest.TestCase):
    """Tests for _try_token_refresh() automatic token renewal."""

    @patch('usage_monitor_for_claude.cache.read_access_token', return_value='old-token')
    @patch('usage_monitor_for_claude.cache.refresh_token')
    def test_refresh_failure_returns_none(self, mock_refresh, _mock_token):
        """When the token is unchanged and refresh_token() fails, returns None."""
        mock_refresh.return_value = RefreshResult(success=False, updated=False, old_version='', new_version='', error='CLI not found')
        cache = _make_cache()
        self.assertEqual(cache._try_token_refresh('old-token'), (None, None))

    @patch('usage_monitor_for_claude.cache.fetch_usage', return_value=_SUCCESS_DATA)
    @patch('usage_monitor_for_claude.cache.read_access_token', side_effect=['old-token', 'new-token'])
    @patch('usage_monitor_for_claude.cache.refresh_token')
    def test_refresh_success_with_new_token_retries(self, mock_refresh, _mock_token, _mock_fetch):
        """When the CLI refresh changes the token, retries API and returns RefreshResult on success."""
        mock_refresh.return_value = RefreshResult(success=True, updated=False, old_version='2.1.69', new_version='2.1.69', error='')
        cache = _make_cache()

        result, retry_data = cache._try_token_refresh('old-token')

        assert result is not None
        self.assertTrue(result.success)
        self.assertEqual(retry_data, _SUCCESS_DATA)
        self.assertEqual(cache.usage, _SUCCESS_DATA)
        self.assertIsNone(cache.last_error)
        self.assertEqual(cache.consecutive_errors, 0)

    @patch('usage_monitor_for_claude.cache.fetch_usage', return_value=_SUCCESS_DATA)
    @patch('usage_monitor_for_claude.cache.read_access_token', return_value='new-token')
    @patch('usage_monitor_for_claude.cache.refresh_token')
    def test_token_already_changed_skips_cli_refresh(self, mock_refresh, _mock_token, mock_fetch):
        """When the credentials already hold a different token (account switch), retry directly without claude update."""
        cache = _make_cache()

        result, _retry_data = cache._try_token_refresh('old-token')

        mock_refresh.assert_not_called()
        mock_fetch.assert_called_once()
        assert result is not None
        self.assertTrue(result.success)
        self.assertFalse(result.updated)
        self.assertEqual(cache.usage, _SUCCESS_DATA)

    @patch('usage_monitor_for_claude.cache.fetch_usage')
    @patch('usage_monitor_for_claude.cache.read_access_token', side_effect=['old-token', 'new-token'])
    @patch('usage_monitor_for_claude.cache.refresh_token')
    def test_switch_recovers_without_cli_refresh(self, mock_refresh, _mock_token, mock_fetch):
        """A 401 whose token already changed recovers via a direct retry, not the slow claude update.

        read_access_token calls: (1) token_before in _fetch_and_process,
        (2) already-changed check in _try_token_refresh (returns the new token,
        so the CLI refresh is skipped).
        """
        mock_fetch.side_effect = [_AUTH_ERROR_DATA, _SUCCESS_DATA]
        cache = _make_cache()

        result = cache.update()

        mock_refresh.assert_not_called()
        self.assertEqual(result.data, _SUCCESS_DATA)
        self.assertIsNone(cache.last_error)

    @patch('usage_monitor_for_claude.cache.read_access_token', return_value='same-token')
    @patch('usage_monitor_for_claude.cache.refresh_token')
    def test_refresh_success_but_token_unchanged(self, mock_refresh, _mock_token):
        """When token doesn't change after refresh, returns None without retry."""
        mock_refresh.return_value = RefreshResult(success=True, updated=False, old_version='2.1.69', new_version='2.1.69', error='')
        cache = _make_cache()

        self.assertEqual(cache._try_token_refresh('same-token'), (None, None))

    @patch('usage_monitor_for_claude.cache.fetch_usage', return_value=_SUCCESS_DATA)
    @patch('usage_monitor_for_claude.cache.read_access_token', return_value='same-token')
    @patch('usage_monitor_for_claude.cache.refresh_token')
    def test_token_unchanged_skips_retry_fetch(self, mock_refresh, _mock_token, mock_fetch):
        """When token doesn't change after refresh, no retry fetch_usage() call is made."""
        mock_refresh.return_value = RefreshResult(success=True, updated=False, old_version='2.1.69', new_version='2.1.69', error='')
        cache = _make_cache()

        cache._try_token_refresh('same-token')

        mock_fetch.assert_not_called()

    @patch('usage_monitor_for_claude.cache.fetch_usage', return_value=_AUTH_ERROR_DATA)
    @patch('usage_monitor_for_claude.cache.read_access_token', side_effect=['old-token', 'new-token'])
    @patch('usage_monitor_for_claude.cache.refresh_token')
    def test_refresh_success_but_retry_fails(self, mock_refresh, _mock_token, _mock_fetch):
        """When the CLI refresh changes the token but retry still fails, returns RefreshResult and records error."""
        mock_refresh.return_value = RefreshResult(success=True, updated=False, old_version='2.1.69', new_version='2.1.69', error='')
        cache = _make_cache()

        result, retry_data = cache._try_token_refresh('old-token')
        assert result is not None
        self.assertTrue(result.success)
        self.assertEqual(retry_data, _AUTH_ERROR_DATA)
        self.assertEqual(cache.last_error, 'expired')
        # _try_token_refresh does not increment _consecutive_errors (caller already did)
        self.assertEqual(cache.consecutive_errors, 0)

    @patch('usage_monitor_for_claude.cache.fetch_usage')
    @patch('usage_monitor_for_claude.cache.read_access_token', return_value='token-123')
    @patch('usage_monitor_for_claude.cache.refresh_token')
    def test_auth_error_triggers_refresh_via_update(self, mock_refresh, _mock_token, mock_fetch):
        """update() calls _try_token_refresh on 401 auth error."""
        mock_refresh.return_value = RefreshResult(success=False, updated=False, old_version='', new_version='', error='')
        mock_fetch.return_value = _AUTH_ERROR_DATA
        cache = _make_cache()

        with patch.object(cache, '_try_token_refresh', wraps=cache._try_token_refresh) as spy:
            cache.update()
            spy.assert_called_once_with('token-123')

    @patch('usage_monitor_for_claude.cache.fetch_usage', return_value=_ERROR_DATA)
    def test_non_auth_error_skips_refresh(self, _mock_fetch):
        """Non-auth errors do not trigger token refresh."""
        cache = _make_cache()

        with patch.object(cache, '_try_token_refresh') as spy:
            cache.update()
            spy.assert_not_called()

    @patch('usage_monitor_for_claude.cache.fetch_usage')
    @patch('usage_monitor_for_claude.cache.read_access_token', side_effect=['old-token', 'old-token', 'new-token'])
    @patch('usage_monitor_for_claude.cache.refresh_token')
    def test_successful_refresh_clears_error(self, mock_refresh, _mock_token, mock_fetch):
        """Auth error + successful token refresh + successful retry clears error state.

        read_access_token calls: (1) token_before in _fetch_and_process,
        (2) unchanged-token check and (3) post-refresh comparison in
        _try_token_refresh.
        """
        mock_refresh.return_value = RefreshResult(success=True, updated=False, old_version='2.1.69', new_version='2.1.69', error='')
        mock_fetch.side_effect = [_AUTH_ERROR_DATA, _SUCCESS_DATA]
        cache = _make_cache()

        result = cache.update()

        self.assertIsNone(cache.last_error)
        self.assertEqual(cache.consecutive_errors, 0)
        self.assertIsNotNone(result.token_refresh)
        # Returns cached success data, not the error
        self.assertEqual(result.data, _SUCCESS_DATA)

    @patch('usage_monitor_for_claude.cache.fetch_usage')
    @patch('usage_monitor_for_claude.cache.read_access_token', side_effect=['old-token', 'old-token', 'new-token'])
    @patch('usage_monitor_for_claude.cache.refresh_token')
    def test_refresh_success_retry_fail_returns_retry_data(self, mock_refresh, _mock_token, mock_fetch):
        """Auth error + successful refresh + failed retry returns the retry's error
        data, so the caller reacts to the current failure, not the repaired 401.

        read_access_token calls: (1) token_before in _fetch_and_process,
        (2) unchanged-token check and (3) post-refresh comparison in
        _try_token_refresh.
        """
        mock_refresh.return_value = RefreshResult(success=True, updated=True, old_version='2.1.38', new_version='2.1.69', error='')
        retry_error = {'error': 'still broken', 'auth_error': True}
        mock_fetch.side_effect = [_AUTH_ERROR_DATA, retry_error]
        cache = _make_cache()

        result = cache.update()

        self.assertEqual(result.data, retry_error)
        assert result.token_refresh is not None

    @patch('usage_monitor_for_claude.cache.time.time', return_value=1000.0)
    @patch('usage_monitor_for_claude.cache.fetch_usage')
    @patch('usage_monitor_for_claude.cache.read_access_token', side_effect=['old-token', 'old-token', 'new-token'])
    @patch('usage_monitor_for_claude.cache.refresh_token')
    def test_rate_limited_retry_arms_backoff(self, mock_refresh, _mock_token, mock_fetch, _mock_time):
        """A 429 on the post-refresh retry must arm the rate-limit backoff and be
        reported to the caller, instead of re-polling an already limited endpoint
        at the error cadence while showing the stale 401 state.

        read_access_token calls: (1) token_before in _fetch_and_process,
        (2) unchanged-token check and (3) post-refresh comparison in
        _try_token_refresh.
        """
        mock_refresh.return_value = RefreshResult(success=True, updated=True, old_version='2.1.38', new_version='2.1.69', error='')
        mock_fetch.side_effect = [_AUTH_ERROR_DATA, {'error': 'HTTP 429', 'rate_limited': True, 'retry_after': 300}]
        cache = _make_cache()

        result = cache.update()

        self.assertGreater(cache.rate_limit_remaining, 0)
        assert result.data is not None
        self.assertTrue(result.data.get('rate_limited'))
        assert result.token_refresh is not None
        self.assertTrue(result.token_refresh.updated)
        self.assertIsNotNone(cache.last_error)

    @patch('usage_monitor_for_claude.cache.fetch_usage')
    @patch('usage_monitor_for_claude.cache.read_access_token', side_effect=['old-token', 'old-token', 'new-token'])
    @patch('usage_monitor_for_claude.cache.refresh_token')
    def test_refresh_retry_fail_does_not_block_new_token(self, mock_refresh, _mock_token, mock_fetch):
        """Auth error + successful refresh + failed retry does NOT set _last_failed_token.

        The new token may be valid - the retry failure could be transient (500, network).
        Blocking the new token would prevent all future polls until manual refresh.
        """
        mock_refresh.return_value = RefreshResult(success=True, updated=False, old_version='2.1.69', new_version='2.1.69', error='')
        mock_fetch.side_effect = [_AUTH_ERROR_DATA, {'error': 'still broken', 'auth_error': True}]
        cache = _make_cache()

        cache.update()

        self.assertIsNone(cache._last_failed_token)

    @patch('usage_monitor_for_claude.cache.fetch_usage')
    @patch('usage_monitor_for_claude.cache.read_access_token', side_effect=['old-token', 'old-token', 'new-token'])
    @patch('usage_monitor_for_claude.cache.refresh_token')
    def test_auth_retry_fail_increments_errors_once(self, mock_refresh, _mock_token, mock_fetch):
        """Auth error + successful refresh + failed retry increments _consecutive_errors only once."""
        mock_refresh.return_value = RefreshResult(success=True, updated=False, old_version='2.1.69', new_version='2.1.69', error='')
        mock_fetch.side_effect = [_AUTH_ERROR_DATA, {'error': 'still broken', 'auth_error': True}]
        cache = _make_cache()

        cache.update()

        self.assertEqual(cache.consecutive_errors, 1)

    @patch('usage_monitor_for_claude.cache.fetch_usage', return_value=_AUTH_ERROR_DATA)
    @patch('usage_monitor_for_claude.cache.read_access_token', return_value='token-123')
    @patch('usage_monitor_for_claude.cache.refresh_token')
    def test_failed_refresh_returns_none_token_refresh(self, mock_refresh, _mock_token, _mock_fetch):
        """When refresh CLI is not available, token_refresh is None in result."""
        mock_refresh.return_value = RefreshResult(success=False, updated=False, old_version='', new_version='', error='not found')
        cache = _make_cache()

        result = cache.update()
        self.assertIsNone(result.token_refresh)


# ---------------------------------------------------------------------------
# Snapshot
# ---------------------------------------------------------------------------

class TestSnapshot(unittest.TestCase):
    """Tests for the CacheSnapshot property."""

    def test_snapshot_type(self):
        cache = _make_cache()
        self.assertIsInstance(cache.snapshot, CacheSnapshot)

    def test_snapshot_reflects_current_state(self):
        cache = _make_cache()
        cache._usage = _SUCCESS_DATA
        cache._last_error = 'some error'
        cache._version = 42

        snap = cache.snapshot
        self.assertEqual(snap.usage, _SUCCESS_DATA)
        self.assertEqual(snap.last_error, 'some error')
        self.assertEqual(snap.version, 42)

    def test_snapshot_is_frozen(self):
        cache = _make_cache()
        snap = cache.snapshot
        with self.assertRaises(AttributeError):
            snap.version = 99  # type: ignore[misc]  # intentional test


# ---------------------------------------------------------------------------
# ensure_profile
# ---------------------------------------------------------------------------

class TestEnsureProfile(unittest.TestCase):
    """Tests for ensure_profile() lazy loading."""

    @patch('usage_monitor_for_claude.cache.fetch_profile', return_value={'name': 'Test User'})
    def test_fetches_profile_when_none(self, mock_fetch):
        cache = _make_cache()
        cache.ensure_profile()
        self.assertEqual(cache.profile, {'name': 'Test User'})
        mock_fetch.assert_called_once()

    @patch('usage_monitor_for_claude.cache.read_access_token', return_value='token-x')
    @patch('usage_monitor_for_claude.cache.fetch_profile')
    def test_skips_when_already_loaded(self, mock_fetch, _mock_token):
        cache = _make_cache()
        cache._profile = {'name': 'Cached'}
        cache._profile_token = 'token-x'
        cache.ensure_profile()
        mock_fetch.assert_not_called()

    @patch('usage_monitor_for_claude.cache.fetch_profile', return_value={'name': 'Test User'})
    @patch('usage_monitor_for_claude.cache.time')
    def test_skips_during_rate_limit_backoff(self, mock_time, mock_fetch):
        """ensure_profile() does not fetch while the 429 backoff window is active."""
        cache = _make_cache()
        cache._rate_limit_until = 1300.0
        mock_time.time.return_value = 1000.0

        cache.ensure_profile()

        mock_fetch.assert_not_called()
        self.assertIsNone(cache.profile)

    @patch('usage_monitor_for_claude.cache.fetch_profile', return_value={'name': 'Test User'})
    @patch('usage_monitor_for_claude.cache.time')
    def test_bypass_rate_limit_fetches_during_backoff(self, mock_time, mock_fetch):
        """ensure_profile(bypass_rate_limit=True) fetches even while the 429 backoff is active."""
        cache = _make_cache()
        cache._rate_limit_until = 1300.0
        mock_time.time.return_value = 1000.0

        cache.ensure_profile(bypass_rate_limit=True)

        mock_fetch.assert_called_once()
        self.assertEqual(cache.profile, {'name': 'Test User'})

    @patch('usage_monitor_for_claude.cache.fetch_profile', return_value={'name': 'Test User'})
    @patch('usage_monitor_for_claude.cache.time')
    def test_fetches_after_rate_limit_expires(self, mock_time, mock_fetch):
        """ensure_profile() fetches once the 429 backoff window has elapsed."""
        cache = _make_cache()
        cache._rate_limit_until = 1300.0
        mock_time.time.return_value = 1400.0

        cache.ensure_profile()

        mock_fetch.assert_called_once()
        self.assertEqual(cache.profile, {'name': 'Test User'})

    @patch('usage_monitor_for_claude.cache.fetch_profile', return_value={'name': 'New User'})
    @patch('usage_monitor_for_claude.cache.read_access_token', return_value='token-b')
    @patch('usage_monitor_for_claude.cache.time')
    def test_token_change_refetch_skipped_during_backoff(self, mock_time, _mock_token, mock_fetch):
        """A token-change re-fetch is also suppressed while the 429 backoff is active."""
        cache = _make_cache()
        cache._profile = {'name': 'Old User'}
        cache._profile_token = 'token-a'
        cache._rate_limit_until = 1300.0
        mock_time.time.return_value = 1000.0

        cache.ensure_profile()

        mock_fetch.assert_not_called()
        self.assertEqual(cache.profile, {'name': 'Old User'})

    @patch('usage_monitor_for_claude.cache.fetch_profile', return_value={'name': 'Test User'})
    def test_concurrent_calls_fetch_only_once(self, mock_fetch):
        """Two threads calling ensure_profile result in only one fetch_profile call."""
        import threading
        cache = _make_cache()
        barrier = threading.Barrier(2)

        def call():
            barrier.wait()
            cache.ensure_profile()

        t1 = threading.Thread(target=call)
        t2 = threading.Thread(target=call)
        t1.start()
        t2.start()
        t1.join(timeout=5)
        t2.join(timeout=5)

        mock_fetch.assert_called_once()
        self.assertEqual(cache.profile, {'name': 'Test User'})


# ---------------------------------------------------------------------------
# UpdateResult
# ---------------------------------------------------------------------------

class TestUpdateResult(unittest.TestCase):
    """Tests for UpdateResult dataclass."""

    def test_default_token_refresh_is_none(self):
        result = UpdateResult(data={'key': 'value'})
        self.assertIsNone(result.token_refresh)

    def test_default_token_is_none(self):
        result = UpdateResult(data={'key': 'value'})
        self.assertIsNone(result.token)

    def test_skipped_result(self):
        result = UpdateResult(data=None)
        self.assertIsNone(result.data)

    def test_with_token_refresh(self):
        refresh = RefreshResult(success=True, updated=True, old_version='1.0', new_version='2.0', error='')
        result = UpdateResult(data=_SUCCESS_DATA, token_refresh=refresh)
        self.assertEqual(result.data, _SUCCESS_DATA)
        assert result.token_refresh is not None
        self.assertTrue(result.token_refresh.updated)


# ---------------------------------------------------------------------------
# API response edge cases
# ---------------------------------------------------------------------------

class TestNullQuotaFields(unittest.TestCase):
    """Tests for API responses with null/None quota field values (issue #26)."""

    @patch('usage_monitor_for_claude.cache.fetch_usage', return_value={'five_hour': None, 'seven_day': None})
    def test_null_quota_fields_do_not_crash(self, _mock):
        """update() succeeds when quota fields are explicitly None."""
        cache = _make_cache()
        result = cache.update()
        self.assertIsNotNone(result.data)
        self.assertEqual(cache.usage, {'five_hour': None, 'seven_day': None})

    @patch('usage_monitor_for_claude.cache.fetch_usage', return_value={'five_hour': None, 'seven_day': {'utilization': 30.0}})
    def test_mixed_null_and_valid_quota_fields(self, _mock):
        """update() succeeds when some quota fields are None and others are valid."""
        cache = _make_cache()
        result = cache.update()
        self.assertIsNotNone(result.data)
        self.assertEqual(cache.usage, {'five_hour': None, 'seven_day': {'utilization': 30.0}})

    @patch('usage_monitor_for_claude.cache.fetch_usage', return_value={})
    def test_empty_response_treated_as_success(self, _mock):
        """Empty dict without 'error' key is treated as success."""
        cache = _make_cache()
        result = cache.update()
        self.assertIsNotNone(result.data)
        self.assertIsNone(cache.last_error)
        self.assertEqual(cache.consecutive_errors, 0)


# ---------------------------------------------------------------------------
# Prepaid credit balance
# ---------------------------------------------------------------------------

_PREPAID_BALANCE = {'amount_minor': 5597.0, 'currency': 'EUR', 'decimal_places': 2}
_ORG_PROFILE = {'organization': {'uuid': 'org-uuid-1'}}
# The balance is only read while extra usage is enabled, so the usage data of
# these tests carries the section that renders it.
_EXTRA_USAGE_DATA = {**_SUCCESS_DATA, 'extra_usage': {'is_enabled': True, 'used_credits': 0.0}}
_SPEND_BALANCE = {'money': {'amount_minor': 4200, 'currency': 'EUR', 'exponent': 2}, 'credits': None}


class TestPrepaidBalance(unittest.TestCase):
    """Tests for fetching the prepaid credit balance alongside the usage data."""

    @patch('usage_monitor_for_claude.cache.fetch_prepaid_credits', return_value=_PREPAID_BALANCE)
    @patch('usage_monitor_for_claude.cache.fetch_usage', return_value=_EXTRA_USAGE_DATA)
    def test_success_stores_balance(self, _mock_fetch, mock_prepaid):
        """A successful usage fetch stores the balance for the same cycle."""
        cache = _make_cache()
        cache._profile = _ORG_PROFILE

        cache.update()

        mock_prepaid.assert_called_once_with('org-uuid-1')
        self.assertEqual(cache.prepaid, _PREPAID_BALANCE)
        self.assertEqual(cache.snapshot.prepaid, _PREPAID_BALANCE)

    @patch('usage_monitor_for_claude.cache.fetch_prepaid_credits')
    @patch('usage_monitor_for_claude.cache.fetch_usage', return_value=_EXTRA_USAGE_DATA)
    def test_unknown_org_uuid_skips_request(self, _mock_fetch, mock_prepaid):
        """Without a profile - or without an organization uuid in it - no request is made."""
        for profile in (None, {}, {'organization': None}, {'organization': {}}):
            with self.subTest(profile=profile):
                cache = _make_cache()
                cache._profile = profile

                cache.update()

                mock_prepaid.assert_not_called()
                self.assertIsNone(cache.prepaid)

    @patch('usage_monitor_for_claude.cache.fetch_prepaid_credits', return_value=None)
    @patch('usage_monitor_for_claude.cache.fetch_usage', return_value=_EXTRA_USAGE_DATA)
    def test_unavailable_balance_leaves_usage_intact(self, _mock_fetch, _mock_prepaid):
        """An unavailable balance never affects the usage result."""
        cache = _make_cache()
        cache._profile = _ORG_PROFILE

        result = cache.update()

        self.assertEqual(result.data, _EXTRA_USAGE_DATA)
        self.assertEqual(cache.usage, _EXTRA_USAGE_DATA)
        self.assertIsNone(cache.last_error)
        self.assertEqual(cache.consecutive_errors, 0)
        self.assertIsNone(cache.prepaid)

    @patch('usage_monitor_for_claude.cache.fetch_prepaid_credits', return_value=None)
    @patch('usage_monitor_for_claude.cache.fetch_usage', return_value=_EXTRA_USAGE_DATA)
    def test_balance_cleared_when_no_longer_available(self, _mock_fetch, mock_prepaid):
        """A balance that disappears is cleared instead of being shown stale."""
        cache = _make_cache()
        cache._profile = _ORG_PROFILE
        cache._prepaid = _PREPAID_BALANCE

        cache.update()

        self.assertIsNone(cache.prepaid)
        mock_prepaid.assert_called_once()

    @patch('usage_monitor_for_claude.cache.fetch_prepaid_credits', return_value=_PREPAID_BALANCE)
    @patch('usage_monitor_for_claude.cache.fetch_usage', return_value=_ERROR_DATA)
    def test_failed_usage_fetch_skips_balance(self, _mock_fetch, mock_prepaid):
        """No balance request is made when the usage fetch failed."""
        cache = _make_cache()
        cache._profile = _ORG_PROFILE

        cache.update()

        mock_prepaid.assert_not_called()
        self.assertIsNone(cache.prepaid)

    @patch('usage_monitor_for_claude.cache.fetch_prepaid_credits', return_value=_PREPAID_BALANCE)
    @patch('usage_monitor_for_claude.cache.refresh_token')
    @patch('usage_monitor_for_claude.cache.read_access_token', side_effect=['tok-a', 'tok-b'])
    @patch('usage_monitor_for_claude.cache.fetch_usage')
    def test_balance_fetched_after_recovered_auth_error(self, mock_fetch, _mock_token, _mock_refresh, mock_prepaid):
        """The retry that recovers a 401 also stores the balance."""
        mock_fetch.side_effect = [_AUTH_ERROR_DATA, _EXTRA_USAGE_DATA]
        cache = _make_cache()
        cache._profile = _ORG_PROFILE

        cache.update()

        mock_prepaid.assert_called_once_with('org-uuid-1')
        self.assertEqual(cache.prepaid, _PREPAID_BALANCE)

    @patch('usage_monitor_for_claude.cache.fetch_prepaid_credits')
    @patch('usage_monitor_for_claude.cache.fetch_usage')
    def test_disabled_extra_usage_skips_request(self, mock_fetch, mock_prepaid):
        """Without extra usage the balance cannot be shown, so it is not requested."""
        for extra_usage in (None, {}, {'is_enabled': False}):
            with self.subTest(extra_usage=extra_usage):
                mock_fetch.return_value = {**_SUCCESS_DATA, 'extra_usage': extra_usage}
                cache = _make_cache()
                cache._profile = _ORG_PROFILE

                cache.update()

                mock_prepaid.assert_not_called()
                self.assertIsNone(cache.prepaid)

    @patch('usage_monitor_for_claude.cache.fetch_prepaid_credits')
    @patch('usage_monitor_for_claude.cache.fetch_usage')
    def test_balance_in_usage_response_skips_request(self, mock_fetch, mock_prepaid):
        """A balance carried by the usage response is used instead of a second request."""
        mock_fetch.return_value = {**_EXTRA_USAGE_DATA, 'spend': {'balance': _SPEND_BALANCE}}
        cache = _make_cache()
        cache._profile = _ORG_PROFILE

        cache.update()

        mock_prepaid.assert_not_called()
        self.assertEqual(cache.prepaid, {'amount_minor': 4200.0, 'currency': 'EUR', 'decimal_places': 2})

    @patch('usage_monitor_for_claude.cache.fetch_prepaid_credits', return_value=_PREPAID_BALANCE)
    @patch('usage_monitor_for_claude.cache.fetch_usage')
    def test_null_balance_in_usage_response_falls_back(self, mock_fetch, mock_prepaid):
        """A spend block without a balance falls back to the dedicated endpoint."""
        mock_fetch.return_value = {**_EXTRA_USAGE_DATA, 'spend': {'balance': None}}
        cache = _make_cache()
        cache._profile = _ORG_PROFILE

        cache.update()

        mock_prepaid.assert_called_once_with('org-uuid-1')
        self.assertEqual(cache.prepaid, _PREPAID_BALANCE)


# ---------------------------------------------------------------------------
# ensure_profile token-change re-fetch
# ---------------------------------------------------------------------------

class TestEnsureProfileTokenChange(unittest.TestCase):
    """Tests for ensure_profile() re-fetching when the access token changes."""

    @patch('usage_monitor_for_claude.cache.fetch_profile', return_value={'account': {'uuid': 'uuid-1'}})
    @patch('usage_monitor_for_claude.cache.read_access_token', return_value='token-a')
    def test_initial_fetch(self, _mock_token, mock_profile):
        """ensure_profile() fetches profile on first call."""
        cache = _make_cache()
        cache.ensure_profile()
        mock_profile.assert_called_once()
        self.assertEqual(cache.profile, {'account': {'uuid': 'uuid-1'}})

    @patch('usage_monitor_for_claude.cache.fetch_profile', return_value={'account': {'uuid': 'uuid-1'}})
    @patch('usage_monitor_for_claude.cache.read_access_token', return_value='token-a')
    def test_no_refetch_when_token_unchanged(self, _mock_token, mock_profile):
        """ensure_profile() does not re-fetch when profile is loaded and token unchanged."""
        cache = _make_cache()
        cache.ensure_profile()
        mock_profile.reset_mock()

        cache.ensure_profile()

        mock_profile.assert_not_called()

    @patch('usage_monitor_for_claude.cache.fetch_profile')
    @patch('usage_monitor_for_claude.cache.read_access_token')
    def test_refetch_when_token_changes(self, mock_token, mock_profile):
        """ensure_profile() re-fetches profile when the access token has changed."""
        mock_token.return_value = 'token-a'
        mock_profile.return_value = {'account': {'uuid': 'uuid-1'}}
        cache = _make_cache()
        cache.ensure_profile()

        mock_token.return_value = 'token-b'
        mock_profile.return_value = {'account': {'uuid': 'uuid-2'}}
        cache.ensure_profile()

        self.assertEqual(mock_profile.call_count, 2)
        self.assertEqual(cache.profile, {'account': {'uuid': 'uuid-2'}})

    @patch('usage_monitor_for_claude.cache.fetch_profile')
    @patch('usage_monitor_for_claude.cache.read_access_token')
    def test_profile_token_updated_after_refetch(self, mock_token, mock_profile):
        """After re-fetching, the new token is stored so subsequent calls are skipped."""
        mock_token.return_value = 'token-a'
        mock_profile.return_value = {'account': {'uuid': 'uuid-1'}}
        cache = _make_cache()
        cache.ensure_profile()

        mock_token.return_value = 'token-b'
        mock_profile.return_value = {'account': {'uuid': 'uuid-2'}}
        cache.ensure_profile()
        mock_profile.reset_mock()

        # Third call: same token-b, should not re-fetch
        cache.ensure_profile()
        mock_profile.assert_not_called()


if __name__ == '__main__':
    unittest.main()

"""
Application Tests
===================

Unit tests for the application module: threshold alerts, update orchestration,
tray rendering, polling interval, and reset notifications.
"""
from __future__ import annotations

import unittest
from datetime import datetime, timezone
from unittest.mock import MagicMock, call, patch

from usage_monitor_for_claude.app import (
    POLL_FAST, RESET_BUFFER, UsageMonitorForClaude, _align_to_reset,
)
from usage_monitor_for_claude.cache import UpdateResult
from usage_monitor_for_claude.claude_cli import RefreshResult


def _make_app(thresholds: list[float] | None = None) -> UsageMonitorForClaude:
    """Create a UsageMonitorForClaude with mocked icon and configurable thresholds.

    Parameters
    ----------
    thresholds : list[float] or None
        Alert thresholds to use for all variants.  Defaults to ``[80, 95]``.
    """
    if thresholds is None:
        thresholds = [80, 95]
    with patch('usage_monitor_for_claude.app.pystray'), \
         patch('usage_monitor_for_claude.app.create_icon_image'), \
         patch('usage_monitor_for_claude.app.taskbar_uses_light_theme', return_value=False):
        app = UsageMonitorForClaude()
    app.icon = MagicMock()
    # Patches active for the app's lifetime, stopped by _cleanup.  The presence
    # defaults keep _is_user_away() False so notification tests are deterministic
    # regardless of the real machine's idle/lock state (idle/lock tests override).
    # ICON_FIELDS is pinned to its default so render tests do not inherit a
    # usage-monitor-settings.json present on the machine running the suite
    # (tests for custom fields override it per test).
    app._patches = [
        patch('usage_monitor_for_claude.app.get_alert_thresholds', return_value=thresholds),
        patch('usage_monitor_for_claude.app.is_workstation_locked', return_value=False),
        patch('usage_monitor_for_claude.app.is_screensaver_running', return_value=False),
        patch('usage_monitor_for_claude.app.get_idle_seconds', return_value=0.0),
        patch('usage_monitor_for_claude.app.ICON_FIELDS', ['five_hour', 'seven_day']),
    ]
    for active_patch in app._patches:
        active_patch.start()
    return app


def _cleanup(app: UsageMonitorForClaude) -> None:
    """Stop patches started by _make_app."""
    for active_patch in app._patches:
        active_patch.stop()


def _returns_from_away(app: UsageMonitorForClaude):
    """Return a _polling_throttled stub that reports away, then present, then ends the loop.

    The loop reads the state once before the wait (away) and once at the end of
    the pass (present), which is the transition the away-return path reacts to.
    """
    states = [True, False]

    def polling_throttled() -> bool:
        if states:
            return states.pop(0)
        app.running = False
        return False

    return polling_throttled


def _stop_after_one_pass(app: UsageMonitorForClaude):
    """Return a _polling_throttled stub that ends poll_loop after one wait pass.

    The loop reads the throttle state once before the wait and once at the end
    of every pass, so the second call is where that pass is over.
    """
    calls = [0]

    def polling_throttled() -> bool:
        calls[0] += 1
        if calls[0] > 1:
            app.running = False
        return False

    return polling_throttled


# ---------------------------------------------------------------------------
# _check_threshold_alerts
# ---------------------------------------------------------------------------

class TestCheckThresholdAlerts(unittest.TestCase):
    """Tests for _check_threshold_alerts() notification logic."""

    def setUp(self):
        self.app = _make_app()
        self._cmd_patch = patch('usage_monitor_for_claude.app.run_event_command')
        self._cmd_patch.start()

    def tearDown(self):
        self._cmd_patch.stop()
        _cleanup(self.app)

    def test_notification_on_first_crossing(self):
        """Notification fires when usage crosses a threshold for the first time."""
        self.app._check_threshold_alerts({'five_hour': {'utilization': 82}})

        self.app.icon.notify.assert_called_once()
        args = self.app.icon.notify.call_args
        self.assertIn('82%', args[0][0])

    def test_no_duplicate_notification(self):
        """No notification if threshold was already notified."""
        self.app._check_threshold_alerts({'five_hour': {'utilization': 82}})
        self.app.icon.notify.reset_mock()

        self.app._check_threshold_alerts({'five_hour': {'utilization': 85}})

        self.app.icon.notify.assert_not_called()

    def test_higher_threshold_triggers_new_notification(self):
        """Crossing a higher threshold triggers a new notification."""
        self.app._check_threshold_alerts({'five_hour': {'utilization': 82}})
        self.app.icon.notify.reset_mock()

        self.app._check_threshold_alerts({'five_hour': {'utilization': 97}})

        self.app.icon.notify.assert_called_once()
        args = self.app.icon.notify.call_args
        self.assertIn('97%', args[0][0])

    def test_jump_past_multiple_thresholds_single_notification(self):
        """Jumping from below all thresholds to above multiple shows only one notification."""
        self.app._check_threshold_alerts({'five_hour': {'utilization': 97}})

        self.app.icon.notify.assert_called_once()
        self.assertEqual(self.app._notified_thresholds.get('five_hour'), 95)

    def test_notification_shows_current_pct_not_threshold(self):
        """Notification message contains the actual usage %, not the threshold value."""
        self.app._check_threshold_alerts({'five_hour': {'utilization': 83.7}})

        args = self.app.icon.notify.call_args
        self.assertIn('84%', args[0][0])

    def test_re_notification_after_usage_drops(self):
        """After usage drops below a threshold, it can re-trigger."""
        self.app._check_threshold_alerts({'five_hour': {'utilization': 82}})
        self.app.icon.notify.reset_mock()

        # Usage drops below 80 (e.g. after reset)
        self.app._check_threshold_alerts({'five_hour': {'utilization': 30}})
        self.app.icon.notify.assert_not_called()

        # Usage rises above 80 again
        self.app._check_threshold_alerts({'five_hour': {'utilization': 81}})
        self.app.icon.notify.assert_called_once()

    def test_no_notification_when_thresholds_empty(self):
        """No notification when thresholds list is empty."""
        _cleanup(self.app)
        self.app = _make_app(thresholds=[])

        self.app._check_threshold_alerts({'five_hour': {'utilization': 99}})

        self.app.icon.notify.assert_not_called()

    def test_on_startup_above_threshold(self):
        """On startup (no prior state), notification fires if already above threshold."""
        self.app._check_threshold_alerts({'five_hour': {'utilization': 90}})

        self.app.icon.notify.assert_called_once()

    def test_each_variant_tracked_independently(self):
        """Different variants are tracked independently."""
        self.app._check_threshold_alerts({
            'five_hour': {'utilization': 82},
            'seven_day': {'utilization': 50},
        })

        self.app.icon.notify.assert_called_once()
        self.assertEqual(self.app._notified_thresholds.get('five_hour'), 80)
        self.assertEqual(self.app._notified_thresholds.get('seven_day', 0), 0)

    def test_multiple_variants_crossing_simultaneously(self):
        """Multiple variants crossing thresholds each get their own notification."""
        self.app._check_threshold_alerts({
            'five_hour': {'utilization': 82},
            'seven_day': {'utilization': 96},
        })

        self.assertEqual(self.app.icon.notify.call_count, 2)

    def test_variant_with_no_utilization_skipped(self):
        """Variants with None utilization are skipped."""
        self.app._check_threshold_alerts({'five_hour': {'utilization': None}})

        self.app.icon.notify.assert_not_called()

    def test_missing_variant_skipped(self):
        """Missing variants in data are skipped."""
        self.app._check_threshold_alerts({})

        self.app.icon.notify.assert_not_called()

    def test_usage_exactly_at_threshold(self):
        """Usage exactly at threshold value triggers notification."""
        self.app._check_threshold_alerts({'five_hour': {'utilization': 80}})

        self.app.icon.notify.assert_called_once()

    def test_usage_just_below_threshold(self):
        """Usage just below threshold does not trigger notification."""
        self.app._check_threshold_alerts({'five_hour': {'utilization': 79.9}})

        self.app.icon.notify.assert_not_called()

    def test_non_dict_entries_skipped(self):
        """Non-dict entries in response (strings, booleans) are silently skipped."""
        self.app._check_threshold_alerts({
            'error': 'server down',
            'rate_limited': True,
            'five_hour': {'utilization': 82},
        })

        self.app.icon.notify.assert_called_once()

    def test_extra_usage_excluded_from_regular_alerts(self):
        """extra_usage is handled separately, not by the regular threshold loop."""
        _cleanup(self.app)
        self.app = _make_app(thresholds=[50])

        with patch.object(self.app, '_check_extra_usage_alerts'):
            self.app._check_threshold_alerts({
                'extra_usage': {'is_enabled': True, 'monthly_limit': 1000, 'used_credits': 800, 'utilization': 80},
            })

        self.app.icon.notify.assert_not_called()

    def test_null_entry_skipped(self):
        """Null entry value is silently skipped."""
        self.app._check_threshold_alerts({'five_hour': None, 'seven_day': {'utilization': 82}})

        self.app.icon.notify.assert_called_once()

    def test_entry_without_utilization_key_skipped(self):
        """Entry dict without utilization key is silently skipped."""
        self.app._check_threshold_alerts({'five_hour': {'resets_at': '2026-01-01T05:00:00Z'}})

        self.app.icon.notify.assert_not_called()

    def test_unknown_dynamic_variant_uses_fallback_thresholds(self):
        """Dynamically discovered variant uses base period fallback thresholds."""
        _cleanup(self.app)
        self.app = _make_app()

        # seven_day_cowork is not in the hardcoded thresholds but falls back to seven_day
        self.app._check_threshold_alerts({'seven_day_cowork': {'utilization': 96}})

        self.app.icon.notify.assert_called_once()

    def test_field_without_resets_at_alerts_normally(self):
        """Field missing resets_at still triggers threshold alert (time-aware falls back gracefully)."""
        self.app._check_threshold_alerts({'five_hour': {'utilization': 82}})

        self.app.icon.notify.assert_called_once()


# ---------------------------------------------------------------------------
# Time-aware alerts
# ---------------------------------------------------------------------------

class TestTimeAwareAlerts(unittest.TestCase):
    """Tests for time-aware threshold alert suppression."""

    def setUp(self):
        self.app = _make_app()
        self._cmd_patch = patch('usage_monitor_for_claude.app.run_event_command')
        self._time_aware_patch = patch('usage_monitor_for_claude.app.ALERT_TIME_AWARE', True)
        self._below_patch = patch('usage_monitor_for_claude.app.ALERT_TIME_AWARE_BELOW', 100)
        self._cmd_patch.start()
        self._time_aware_patch.start()
        self._below_patch.start()

    def tearDown(self):
        self._below_patch.stop()
        self._time_aware_patch.stop()
        self._cmd_patch.stop()
        _cleanup(self.app)

    def test_alert_suppressed_when_usage_behind_time(self):
        """No notification when usage (82%) <= elapsed time (90%)."""
        with patch('usage_monitor_for_claude.app.elapsed_pct', return_value=90.0):
            self.app._check_threshold_alerts({'five_hour': {'utilization': 82, 'resets_at': '2025-01-15T14:30:00+00:00'}})

        self.app.icon.notify.assert_not_called()

    def test_alert_shown_when_usage_ahead_of_time(self):
        """Notification fires when usage (82%) > elapsed time (50%)."""
        with patch('usage_monitor_for_claude.app.elapsed_pct', return_value=50.0):
            self.app._check_threshold_alerts({'five_hour': {'utilization': 82, 'resets_at': '2025-01-15T14:30:00+00:00'}})

        self.app.icon.notify.assert_called_once()

    def test_fallback_when_elapsed_pct_none(self):
        """Notification fires normally when elapsed_pct returns None (no resets_at)."""
        with patch('usage_monitor_for_claude.app.elapsed_pct', return_value=None):
            self.app._check_threshold_alerts({'five_hour': {'utilization': 82}})

        self.app.icon.notify.assert_called_once()

    def test_tracking_updated_when_suppressed(self):
        """Notified threshold tracking is updated even when alert is suppressed."""
        with patch('usage_monitor_for_claude.app.elapsed_pct', return_value=90.0):
            self.app._check_threshold_alerts({'five_hour': {'utilization': 82, 'resets_at': '2025-01-15T14:30:00+00:00'}})

        self.assertEqual(self.app._notified_thresholds.get('five_hour'), 80)

    def test_no_re_notification_after_suppression(self):
        """After suppression, the same threshold does not re-trigger."""
        with patch('usage_monitor_for_claude.app.elapsed_pct', return_value=90.0):
            self.app._check_threshold_alerts({'five_hour': {'utilization': 82, 'resets_at': '2025-01-15T14:30:00+00:00'}})

        # Now time catches up less - usage is ahead, but threshold already tracked
        with patch('usage_monitor_for_claude.app.elapsed_pct', return_value=50.0):
            self.app._check_threshold_alerts({'five_hour': {'utilization': 84, 'resets_at': '2025-01-15T14:30:00+00:00'}})

        self.app.icon.notify.assert_not_called()

    def test_disabled_when_false(self):
        """With ALERT_TIME_AWARE=False, alerts fire regardless of time."""
        self._time_aware_patch.stop()
        with patch('usage_monitor_for_claude.app.ALERT_TIME_AWARE', False):
            with patch('usage_monitor_for_claude.app.elapsed_pct', return_value=90.0):
                self.app._check_threshold_alerts({'five_hour': {'utilization': 82, 'resets_at': '2025-01-15T14:30:00+00:00'}})
        self._time_aware_patch.start()

        self.app.icon.notify.assert_called_once()

    def test_usage_equal_to_time_suppressed(self):
        """Notification suppressed when usage exactly equals elapsed time."""
        with patch('usage_monitor_for_claude.app.elapsed_pct', return_value=82.0):
            self.app._check_threshold_alerts({'five_hour': {'utilization': 82, 'resets_at': '2025-01-15T14:30:00+00:00'}})

        self.app.icon.notify.assert_not_called()

    def test_threshold_at_or_above_below_cutoff_always_fires(self):
        """Threshold >= alert_time_aware_below fires even when usage <= time."""
        self._below_patch.stop()
        with patch('usage_monitor_for_claude.app.ALERT_TIME_AWARE_BELOW', 90):
            # Thresholds are [80, 95]. Usage crosses 95 which is >= 90 cutoff.
            with patch('usage_monitor_for_claude.app.elapsed_pct', return_value=98.0):
                self.app._check_threshold_alerts({'five_hour': {'utilization': 97, 'resets_at': '2025-01-15T14:30:00+00:00'}})
        self._below_patch.start()

        self.app.icon.notify.assert_called_once()

    def test_threshold_below_cutoff_suppressed(self):
        """Threshold < alert_time_aware_below is suppressed when usage <= time."""
        self._below_patch.stop()
        with patch('usage_monitor_for_claude.app.ALERT_TIME_AWARE_BELOW', 90):
            # Thresholds are [80, 95]. Usage crosses 80 which is < 90 cutoff.
            with patch('usage_monitor_for_claude.app.elapsed_pct', return_value=90.0):
                self.app._check_threshold_alerts({'five_hour': {'utilization': 82, 'resets_at': '2025-01-15T14:30:00+00:00'}})
        self._below_patch.start()

        self.app.icon.notify.assert_not_called()

    def test_below_cutoff_exact_boundary_fires(self):
        """Threshold exactly at alert_time_aware_below fires regardless of time."""
        self._below_patch.stop()
        with patch('usage_monitor_for_claude.app.ALERT_TIME_AWARE_BELOW', 80):
            with patch('usage_monitor_for_claude.app.elapsed_pct', return_value=90.0):
                self.app._check_threshold_alerts({'five_hour': {'utilization': 82, 'resets_at': '2025-01-15T14:30:00+00:00'}})
        self._below_patch.start()

        self.app.icon.notify.assert_called_once()


# ---------------------------------------------------------------------------
# Extra usage alerts
# ---------------------------------------------------------------------------

class TestExtraUsageAlerts(unittest.TestCase):
    """Tests for _check_extra_usage_alerts() notification logic."""

    def setUp(self):
        self.app = _make_app()
        self._cmd_patch = patch('usage_monitor_for_claude.app.run_event_command')
        self._cmd_patch.start()

    def tearDown(self):
        self._cmd_patch.stop()
        _cleanup(self.app)

    def _extra_data(self, used: float = 0.0, limit: float = 1000, enabled: bool = True) -> dict:
        return {'extra_usage': {'is_enabled': enabled, 'monthly_limit': limit, 'used_credits': used, 'utilization': None}}

    def test_notification_at_threshold(self):
        """Notification fires when extra usage crosses a threshold."""
        self.app._check_extra_usage_alerts(self._extra_data(used=820, limit=1000))

        self.app.icon.notify.assert_called_once()
        args = self.app.icon.notify.call_args[0]
        self.assertIn('82%', args[0])

    def test_no_notification_below_threshold(self):
        """No notification when usage is below all thresholds."""
        self.app._check_extra_usage_alerts(self._extra_data(used=100, limit=1000))

        self.app.icon.notify.assert_not_called()

    def test_no_duplicate_notification(self):
        """No notification if threshold was already notified."""
        self.app._check_extra_usage_alerts(self._extra_data(used=820, limit=1000))
        self.app.icon.notify.reset_mock()

        self.app._check_extra_usage_alerts(self._extra_data(used=850, limit=1000))

        self.app.icon.notify.assert_not_called()

    def test_higher_threshold_triggers_new_notification(self):
        """Crossing a higher threshold triggers a new notification."""
        self.app._check_extra_usage_alerts(self._extra_data(used=820, limit=1000))
        self.app.icon.notify.reset_mock()

        self.app._check_extra_usage_alerts(self._extra_data(used=960, limit=1000))

        self.app.icon.notify.assert_called_once()
        args = self.app.icon.notify.call_args[0]
        self.assertIn('96%', args[0])

    def test_re_notification_after_usage_drops(self):
        """After usage drops (e.g. new billing cycle), thresholds re-trigger."""
        self.app._check_extra_usage_alerts(self._extra_data(used=820, limit=1000))
        self.app.icon.notify.reset_mock()

        self.app._check_extra_usage_alerts(self._extra_data(used=100, limit=1000))
        self.app.icon.notify.assert_not_called()

        self.app._check_extra_usage_alerts(self._extra_data(used=820, limit=1000))
        self.app.icon.notify.assert_called_once()

    def test_disabled_extra_usage_skipped(self):
        """No notification when extra usage is disabled."""
        self.app._check_extra_usage_alerts(self._extra_data(used=950, limit=1000, enabled=False))

        self.app.icon.notify.assert_not_called()

    def test_missing_extra_usage_skipped(self):
        """No notification when extra_usage is missing from data."""
        self.app._check_extra_usage_alerts({})

        self.app.icon.notify.assert_not_called()

    def test_zero_limit_skipped(self):
        """No notification when monthly limit is zero."""
        self.app._check_extra_usage_alerts(self._extra_data(used=0, limit=0))

        self.app.icon.notify.assert_not_called()

    def test_no_notification_when_thresholds_empty(self):
        """No notification when thresholds list is empty."""
        _cleanup(self.app)
        self.app = _make_app(thresholds=[])

        self.app._check_extra_usage_alerts(self._extra_data(used=950, limit=1000))

        self.app.icon.notify.assert_not_called()

    def test_notification_includes_credit_amounts(self):
        """Notification message includes formatted credit amounts."""
        with patch('usage_monitor_for_claude.app.format_credits', side_effect=lambda c, *_: f'${c / 100:.2f}'):
            self.app._check_extra_usage_alerts(self._extra_data(used=820, limit=1000))

        args = self.app.icon.notify.call_args[0]
        self.assertIn('$8.20', args[0])
        self.assertIn('$10.00', args[0])

    def test_called_from_check_threshold_alerts(self):
        """_check_threshold_alerts delegates to _check_extra_usage_alerts."""
        data = self._extra_data(used=820, limit=1000)
        self.app._check_threshold_alerts(data)

        self.app.icon.notify.assert_called_once()

    def test_no_time_aware_logic(self):
        """Extra usage alerts are not affected by time-aware settings."""
        with patch('usage_monitor_for_claude.app.ALERT_TIME_AWARE', True):
            self.app._check_extra_usage_alerts(self._extra_data(used=820, limit=1000))

        self.app.icon.notify.assert_called_once()


# ---------------------------------------------------------------------------
# _check_extra_usage_spent_alerts
# ---------------------------------------------------------------------------

class TestExtraUsageSpentAlerts(unittest.TestCase):
    """Tests for _check_extra_usage_spent_alerts() notification logic."""

    def setUp(self):
        self.app = _make_app(thresholds=[])
        self._cmd_patch = patch('usage_monitor_for_claude.app.run_event_command')
        self._cmd_patch.start()
        self._amounts_patch = patch('usage_monitor_for_claude.app.ALERT_EXTRA_USAGE_SPENT', [50, 100, 150])
        self._amounts_patch.start()

    def tearDown(self):
        self._amounts_patch.stop()
        self._cmd_patch.stop()
        _cleanup(self.app)

    def _extra_data(self, used: float, limit: float | None = None, enabled: bool = True, decimal_places: int | None = 2) -> dict:
        extra = {'is_enabled': enabled, 'monthly_limit': limit, 'used_credits': used, 'utilization': None}
        if decimal_places is not None:
            extra['decimal_places'] = decimal_places
        return {'extra_usage': extra}

    def test_notification_when_spend_crosses_amount(self):
        """Notification fires when uncapped spending crosses a configured amount."""
        self.app._check_extra_usage_alerts(self._extra_data(used=5000))

        self.app.icon.notify.assert_called_once()

    def test_no_notification_below_lowest_amount(self):
        """No notification while spending is below every configured amount."""
        self.app._check_extra_usage_alerts(self._extra_data(used=4999))

        self.app.icon.notify.assert_not_called()

    def test_no_duplicate_notification(self):
        """No notification if the crossed amount was already notified."""
        self.app._check_extra_usage_alerts(self._extra_data(used=5000))
        self.app.icon.notify.reset_mock()

        self.app._check_extra_usage_alerts(self._extra_data(used=6000))

        self.app.icon.notify.assert_not_called()

    def test_higher_amount_triggers_new_notification(self):
        """Crossing a higher configured amount triggers a new notification."""
        self.app._check_extra_usage_alerts(self._extra_data(used=5000))
        self.app.icon.notify.reset_mock()

        self.app._check_extra_usage_alerts(self._extra_data(used=10500))

        self.app.icon.notify.assert_called_once()

    def test_re_notification_after_spend_drops(self):
        """After spending drops (e.g. new billing cycle), amounts re-trigger."""
        self.app._check_extra_usage_alerts(self._extra_data(used=10500))
        self.app.icon.notify.reset_mock()

        self.app._check_extra_usage_alerts(self._extra_data(used=1000))
        self.app.icon.notify.assert_not_called()

        self.app._check_extra_usage_alerts(self._extra_data(used=5500))
        self.app.icon.notify.assert_called_once()

    def test_notification_includes_spent_amount(self):
        """Notification message includes the formatted spent amount."""
        with patch('usage_monitor_for_claude.app.format_credits', side_effect=lambda c, *_: f'${c / 100:.2f}'):
            self.app._check_extra_usage_alerts(self._extra_data(used=10631))

        args = self.app.icon.notify.call_args[0]
        self.assertIn('$106.31', args[0])

    def test_fires_alongside_percentage_alerts_when_capped(self):
        """With a monthly limit, spend amounts and percentage thresholds alert independently."""
        _cleanup(self.app)
        self.app = _make_app(thresholds=[80, 95])

        self.app._check_extra_usage_alerts(self._extra_data(used=8200, limit=10000))

        self.assertEqual(self.app.icon.notify.call_count, 2)

    def test_disabled_extra_usage_skipped(self):
        """No notification when extra usage is disabled."""
        self.app._check_extra_usage_alerts(self._extra_data(used=10500, enabled=False))

        self.app.icon.notify.assert_not_called()

    def test_no_notification_when_amounts_empty(self):
        """No notification when no spend amounts are configured (the default)."""
        with patch('usage_monitor_for_claude.app.ALERT_EXTRA_USAGE_SPENT', []):
            self.app._check_extra_usage_alerts(self._extra_data(used=10500))

        self.app.icon.notify.assert_not_called()

    def test_decimal_places_respected(self):
        """Spending is converted to major units using the API's decimal_places."""
        self.app._check_extra_usage_alerts(self._extra_data(used=50, decimal_places=0))

        self.app.icon.notify.assert_called_once()

    def test_missing_decimal_places_defaults_to_two(self):
        """Without decimal_places, used_credits is treated as hundredths."""
        self.app._check_extra_usage_alerts(self._extra_data(used=5000, decimal_places=None))

        self.app.icon.notify.assert_called_once()

    @patch('usage_monitor_for_claude.app.ON_THRESHOLD_COMMAND', ['notify.bat'])
    def test_threshold_command_omits_utilization(self):
        """The spend alert's threshold command carries no utilization percentage."""
        self.app._first_update_done = True
        with patch('usage_monitor_for_claude.app.run_event_command') as mock_cmd:
            self.app._check_extra_usage_alerts(self._extra_data(used=5000))

        env = mock_cmd.call_args[0][1]
        self.assertEqual(env['USAGE_MONITOR_VARIANT'], 'extra_usage_spent')
        self.assertNotIn('USAGE_MONITOR_UTILIZATION', env)
        self.assertEqual(env['USAGE_MONITOR_THRESHOLD'], '50')
        self.assertIn('USAGE_MONITOR_EXTRA_USED', env)
        self.assertNotIn('USAGE_MONITOR_EXTRA_LIMIT', env)


# ---------------------------------------------------------------------------
# update() orchestration
# ---------------------------------------------------------------------------

class TestUpdateOrchestration(unittest.TestCase):
    """Tests for update() delegating to cache and processing results."""

    def setUp(self):
        self.app = _make_app()
        self._cmd_patch = patch('usage_monitor_for_claude.app.run_event_command')
        self._cmd_patch.start()

    def tearDown(self):
        self._cmd_patch.stop()
        _cleanup(self.app)

    def test_skipped_update_does_nothing(self):
        """When cache.update() returns None data, update() returns early."""
        self.app.cache = MagicMock()
        self.app.cache.update.return_value = UpdateResult(data=None)

        self.app.update()

        self.assertEqual(self.app._last_response, {})

    def test_skipped_claude_still_refreshes_codex_cadence(self):
        """A Claude cooldown or error does not prevent an expired Codex refresh."""
        self.app.cache = MagicMock()
        self.app.cache.update.return_value = UpdateResult(data=None)

        self.app.update()

        self.app.cache.refresh_codex_if_stale.assert_called_once_with()

    @patch('usage_monitor_for_claude.app.format_tooltip', return_value='tooltip')
    @patch('usage_monitor_for_claude.app.create_icon_image')
    def test_success_updates_last_response(self, _icon, _tooltip):
        """Successful update stores response in _last_response."""
        data = {'five_hour': {'utilization': 42.0}}
        self.app.cache = MagicMock()
        self.app.cache.update.return_value = UpdateResult(data=data)

        self.app.update()

        self.assertEqual(self.app._last_response, data)

    @patch('usage_monitor_for_claude.app.format_tooltip', return_value='tooltip')
    @patch('usage_monitor_for_claude.app.create_status_image')
    def test_error_updates_last_response(self, _status, _tooltip):
        """Error update stores error response in _last_response."""
        data = {'error': 'server down'}
        self.app.cache = MagicMock()
        self.app.cache.update.return_value = UpdateResult(data=data)

        self.app.update()

        self.assertEqual(self.app._last_response, data)

    @patch('usage_monitor_for_claude.app.format_tooltip', return_value='tooltip')
    @patch('usage_monitor_for_claude.app.create_icon_image')
    def test_token_refresh_notification(self, _icon, _tooltip):
        """Shows notification when token refresh updated CLI version."""
        data = {'five_hour': {'utilization': 10.0}}
        refresh = RefreshResult(success=True, updated=True, old_version='2.1.38', new_version='2.1.69', error='')
        self.app.cache = MagicMock()
        self.app.cache.update.return_value = UpdateResult(data=data, token_refresh=refresh)

        self.app.update()

        self.app.icon.notify.assert_called_once()
        args = self.app.icon.notify.call_args[0]
        self.assertIn('2.1.38', args[0])
        self.assertIn('2.1.69', args[0])

    @patch('usage_monitor_for_claude.app.format_tooltip', return_value='tooltip')
    @patch('usage_monitor_for_claude.app.create_icon_image')
    def test_no_notification_when_no_cli_update(self, _icon, _tooltip):
        """No notification when token refreshed but no CLI update."""
        data = {'five_hour': {'utilization': 10.0}}
        refresh = RefreshResult(success=True, updated=False, old_version='2.1.69', new_version='2.1.69', error='')
        self.app.cache = MagicMock()
        self.app.cache.update.return_value = UpdateResult(data=data, token_refresh=refresh)

        self.app.update()

        self.app.icon.notify.assert_not_called()

    @patch('usage_monitor_for_claude.app.NOTIFY_CLAUDE_UPDATE', False)
    @patch('usage_monitor_for_claude.app.format_tooltip', return_value='tooltip')
    @patch('usage_monitor_for_claude.app.create_icon_image')
    def test_update_notification_suppressed_when_disabled(self, _icon, _tooltip):
        """No notification when notify_claude_update is disabled, even after a CLI update."""
        data = {'five_hour': {'utilization': 10.0}}
        refresh = RefreshResult(success=True, updated=True, old_version='2.1.38', new_version='2.1.69', error='')
        self.app.cache = MagicMock()
        self.app.cache.update.return_value = UpdateResult(data=data, token_refresh=refresh)

        self.app.update()

        self.app.icon.notify.assert_not_called()

    @patch('usage_monitor_for_claude.app.format_tooltip', return_value='tooltip')
    @patch('usage_monitor_for_claude.app.create_status_image')
    def test_error_returns_before_threshold_checks(self, _status, _tooltip):
        """Error response returns early without threshold checks."""
        data = {'error': 'fail'}
        self.app.cache = MagicMock()
        self.app.cache.update.return_value = UpdateResult(data=data)

        with patch.object(self.app, '_check_threshold_alerts') as mock_check:
            self.app.update()
            mock_check.assert_not_called()

    @patch('usage_monitor_for_claude.app.format_tooltip', return_value='tooltip')
    @patch('usage_monitor_for_claude.app.create_icon_image')
    def test_update_tracks_previous_values(self, _icon, _tooltip):
        """update() stores current pct values for next comparison."""
        data = {'five_hour': {'utilization': 42.0}, 'seven_day': {'utilization': 15.0}}
        self.app.cache = MagicMock()
        self.app.cache.update.return_value = UpdateResult(data=data)

        self.app.update()

        self.assertEqual(self.app._prev_utilization.get('five_hour'), 42.0)
        self.assertEqual(self.app._prev_utilization.get('seven_day'), 15.0)

    @patch('usage_monitor_for_claude.app.format_tooltip', return_value='tooltip')
    @patch('usage_monitor_for_claude.app.create_status_image')
    def test_error_does_not_update_previous_values(self, _status, _tooltip):
        """Error response does not change tracked previous values."""
        self.app._prev_utilization = {'five_hour': 50.0, 'seven_day': 20.0}
        data = {'error': 'fail'}
        self.app.cache = MagicMock()
        self.app.cache.update.return_value = UpdateResult(data=data)

        self.app.update()

        self.assertEqual(self.app._prev_utilization.get('five_hour'), 50.0)
        self.assertEqual(self.app._prev_utilization.get('seven_day'), 20.0)


# ---------------------------------------------------------------------------
# Reset notifications
# ---------------------------------------------------------------------------

class TestResetNotifications(unittest.TestCase):
    """Tests for quota reset notifications in update()."""

    def setUp(self):
        self.app = _make_app()
        self._cmd_patch = patch('usage_monitor_for_claude.app.run_event_command')
        self._cmd_patch.start()

    def tearDown(self):
        self._cmd_patch.stop()
        _cleanup(self.app)

    @patch('usage_monitor_for_claude.app.format_tooltip', return_value='tooltip')
    @patch('usage_monitor_for_claude.app.create_icon_image')
    def test_5h_reset_notification(self, _icon, _tooltip):
        """Notification fires when 5h usage drops from >95% with 7d not blocking."""
        self.app._prev_utilization = {'five_hour': 97.0, 'seven_day': 50.0}
        data = {'five_hour': {'utilization': 10.0}, 'seven_day': {'utilization': 50.0}}
        self.app.cache = MagicMock()
        self.app.cache.update.return_value = UpdateResult(data=data)

        self.app.update()

        self.app.icon.notify.assert_called_once()

    @patch('usage_monitor_for_claude.app.format_tooltip', return_value='tooltip')
    @patch('usage_monitor_for_claude.app.create_icon_image')
    def test_5h_reset_suppressed_when_7d_blocking(self, _icon, _tooltip):
        """No 5h reset notification when 7d is at 99%+."""
        self.app._prev_utilization = {'five_hour': 97.0, 'seven_day': 50.0}
        data = {'five_hour': {'utilization': 10.0}, 'seven_day': {'utilization': 99.5}}
        self.app.cache = MagicMock()
        self.app.cache.update.return_value = UpdateResult(data=data)

        with patch.object(self.app, '_check_threshold_alerts'):
            self.app.update()

        self.app.icon.notify.assert_not_called()

    @patch('usage_monitor_for_claude.app.format_tooltip', return_value='tooltip')
    @patch('usage_monitor_for_claude.app.create_icon_image')
    def test_7d_reset_notification(self, _icon, _tooltip):
        """Notification fires when 7d usage drops from >98% with 5h not blocking."""
        self.app._prev_utilization = {'five_hour': 50.0, 'seven_day': 99.0}
        data = {'five_hour': {'utilization': 50.0}, 'seven_day': {'utilization': 10.0}}
        self.app.cache = MagicMock()
        self.app.cache.update.return_value = UpdateResult(data=data)

        self.app.update()

        self.app.icon.notify.assert_called_once()

    @patch('usage_monitor_for_claude.app.format_tooltip', return_value='tooltip')
    @patch('usage_monitor_for_claude.app.create_icon_image')
    def test_two_quotas_resetting_together_notify_once(self, _icon, _tooltip):
        """Two quotas resetting within one polling gap (e.g. a weekly window and its
        model-scoped sibling) produce a single reset notification, not one per field."""
        self.app._prev_utilization = {'seven_day': 99.0, 'seven_day_fable': 99.0}
        data = {'seven_day': {'utilization': 1.0}, 'seven_day_fable': {'utilization': 1.0}}
        self.app.cache = MagicMock()
        self.app.cache.update.return_value = UpdateResult(data=data)

        self.app.update()

        self.app.icon.notify.assert_called_once()

    @patch('usage_monitor_for_claude.app.format_tooltip', return_value='tooltip')
    @patch('usage_monitor_for_claude.app.create_icon_image')
    def test_no_reset_notification_on_first_update(self, _icon, _tooltip):
        """No reset notification on first update (no previous values)."""
        data = {'five_hour': {'utilization': 10.0}, 'seven_day': {'utilization': 10.0}}
        self.app.cache = MagicMock()
        self.app.cache.update.return_value = UpdateResult(data=data)

        self.app.update()

        self.app.icon.notify.assert_not_called()

    @patch('usage_monitor_for_claude.app.format_tooltip', return_value='tooltip')
    @patch('usage_monitor_for_claude.app.create_icon_image')
    def test_update_ignores_non_dict_entries(self, _icon, _tooltip):
        """Non-dict entries in API response don't affect quota tracking."""
        self.app._prev_utilization = {'five_hour': 50.0}
        data = {
            'error_code': 'temporary',
            'rate_limited': False,
            'five_hour': {'utilization': 55.0},
        }
        self.app.cache = MagicMock()
        self.app.cache.update.return_value = UpdateResult(data=data)

        self.app.update()

        self.assertEqual(self.app._prev_utilization.get('five_hour'), 55.0)
        self.assertNotIn('error_code', self.app._prev_utilization)

    @patch('usage_monitor_for_claude.app.format_tooltip', return_value='tooltip')
    @patch('usage_monitor_for_claude.app.create_icon_image')
    def test_update_excludes_extra_usage_from_quota_tracking(self, _icon, _tooltip):
        """extra_usage is not tracked as a quota field for resets or fast polling."""
        data = {
            'five_hour': {'utilization': 42.0},
            'extra_usage': {'is_enabled': True, 'monthly_limit': 1000, 'used_credits': 500, 'utilization': 50.0},
        }
        self.app.cache = MagicMock()
        self.app.cache.update.return_value = UpdateResult(data=data)

        self.app.update()

        self.assertIn('five_hour', self.app._prev_utilization)
        self.assertNotIn('extra_usage', self.app._prev_utilization)

    @patch('usage_monitor_for_claude.app.format_tooltip', return_value='tooltip')
    @patch('usage_monitor_for_claude.app.create_icon_image')
    def test_update_handles_all_null_fields(self, _icon, _tooltip):
        """All-null quota fields produce empty tracking state."""
        data = {'five_hour': None, 'seven_day': None}
        self.app.cache = MagicMock()
        self.app.cache.update.return_value = UpdateResult(data=data)

        self.app.update()

        self.assertEqual(self.app._prev_utilization, {})

    @patch('usage_monitor_for_claude.app.format_tooltip', return_value='tooltip')
    @patch('usage_monitor_for_claude.app.create_icon_image')
    def test_7d_reset_suppressed_when_5h_blocking(self, _icon, _tooltip):
        """No 7d reset notification when 5h is at 99%+."""
        self.app._prev_utilization = {'five_hour': 50.0, 'seven_day': 99.0}
        data = {'five_hour': {'utilization': 99.5}, 'seven_day': {'utilization': 10.0}}
        self.app.cache = MagicMock()
        self.app.cache.update.return_value = UpdateResult(data=data)

        with patch.object(self.app, '_check_threshold_alerts'):
            self.app.update()

        self.app.icon.notify.assert_not_called()

    @patch('usage_monitor_for_claude.app.is_workstation_locked', return_value=True)
    @patch('usage_monitor_for_claude.app.format_tooltip', return_value='tooltip')
    @patch('usage_monitor_for_claude.app.create_icon_image')
    def test_5h_reset_notification_deferred_while_idle(self, _icon, _tooltip, _locked):
        """Reset notification is deferred (not shown) while user is away."""
        self.app._prev_utilization = {'five_hour': 97.0, 'seven_day': 50.0}
        data = {'five_hour': {'utilization': 10.0}, 'seven_day': {'utilization': 50.0}}
        self.app.cache = MagicMock()
        self.app.cache.update.return_value = UpdateResult(data=data)

        self.app.update()

        self.app.icon.notify.assert_not_called()
        self.assertEqual(len(self.app._deferred_notifications), 1)

    @patch('usage_monitor_for_claude.app.is_workstation_locked', return_value=True)
    @patch('usage_monitor_for_claude.app.format_tooltip', return_value='tooltip')
    @patch('usage_monitor_for_claude.app.create_icon_image')
    def test_deferred_notification_shown_on_flush(self, _icon, _tooltip, _locked):
        """Deferred notifications are shown when flushed."""
        self.app._prev_utilization = {'five_hour': 97.0, 'seven_day': 50.0}
        data = {'five_hour': {'utilization': 10.0}, 'seven_day': {'utilization': 50.0}}
        self.app.cache = MagicMock()
        self.app.cache.update.return_value = UpdateResult(data=data)

        self.app.update()
        self.app.icon.notify.assert_not_called()

        self.app._flush_deferred_notifications()

        self.app.icon.notify.assert_called_once()
        self.assertEqual(len(self.app._deferred_notifications), 0)

    @patch('usage_monitor_for_claude.app.is_workstation_locked', return_value=True)
    @patch('usage_monitor_for_claude.app.format_tooltip', return_value='tooltip')
    @patch('usage_monitor_for_claude.app.create_icon_image')
    def test_repeated_resets_while_idle_deduplicated(self, _icon, _tooltip, _locked):
        """Multiple reset drops while idle produce only one deferred notification."""
        self.app.cache = MagicMock()

        # First reset cycle: 97% -> 10%
        self.app._prev_utilization = {'five_hour': 97.0, 'seven_day': 50.0}
        data = {'five_hour': {'utilization': 10.0}, 'seven_day': {'utilization': 50.0}}
        self.app.cache.update.return_value = UpdateResult(data=data)
        self.app.update()

        # Second reset cycle: usage went back up on another device, then reset again
        self.app._prev_utilization['five_hour'] = 96.0
        data = {'five_hour': {'utilization': 5.0}, 'seven_day': {'utilization': 50.0}}
        self.app.cache.update.return_value = UpdateResult(data=data)
        self.app.update()

        # Only one deferred notification (same 'reset' category, latest wins)
        self.assertEqual(len(self.app._deferred_notifications), 1)

        self.app._flush_deferred_notifications()
        self.app.icon.notify.assert_called_once()

    @patch('usage_monitor_for_claude.app.ALERT_TIME_AWARE', False)
    @patch('usage_monitor_for_claude.app.is_workstation_locked', return_value=True)
    @patch('usage_monitor_for_claude.app.format_tooltip', return_value='tooltip')
    @patch('usage_monitor_for_claude.app.create_icon_image')
    def test_threshold_notifications_deferred_and_deduplicated(self, _icon, _tooltip, _locked):
        """Successive threshold crossings while idle keep only the latest notification per variant."""
        self.app._prev_utilization = {'five_hour': 50.0, 'seven_day': 10.0}
        self.app.cache = MagicMock()

        # Cross 80% threshold
        data = {'five_hour': {'utilization': 82.0, 'resets_at': '2025-01-15T18:00:00Z'}, 'seven_day': {'utilization': 10.0}}
        self.app.cache.update.return_value = UpdateResult(data=data)
        self.app.update()

        # Cross 95% threshold
        self.app._prev_utilization['five_hour'] = 82.0
        data = {'five_hour': {'utilization': 96.0, 'resets_at': '2025-01-15T18:00:00Z'}, 'seven_day': {'utilization': 10.0}}
        self.app.cache.update.return_value = UpdateResult(data=data)
        self.app.update()

        self.app.icon.notify.assert_not_called()
        # Only one deferred notification for threshold_five_hour (the 96% one)
        self.assertIn('threshold_five_hour', self.app._deferred_notifications)
        self.assertIn('96', self.app._deferred_notifications['threshold_five_hour'][0])

        self.app._flush_deferred_notifications()
        self.app.icon.notify.assert_called_once()


# ---------------------------------------------------------------------------
# Fast polling (adaptive)
# ---------------------------------------------------------------------------

class TestFastPolling(unittest.TestCase):
    """Tests for adaptive fast polling when session usage is increasing."""

    def setUp(self):
        self.app = _make_app()
        self._cmd_patch = patch('usage_monitor_for_claude.app.run_event_command')
        self._cmd_patch.start()

    def tearDown(self):
        self._cmd_patch.stop()
        _cleanup(self.app)

    @patch('usage_monitor_for_claude.app.format_tooltip', return_value='tooltip')
    @patch('usage_monitor_for_claude.app.create_icon_image')
    def test_fast_polling_starts_on_usage_increase(self, _icon, _tooltip):
        """Fast polls start when 5h usage is increasing."""
        self.app._prev_utilization = {'five_hour': 40.0, 'seven_day': 10.0}
        data = {'five_hour': {'utilization': 45.0}, 'seven_day': {'utilization': 10.0}}
        self.app.cache = MagicMock()
        self.app.cache.update.return_value = UpdateResult(data=data)

        self.app.update()

        self.assertGreater(self.app._fast_polls_remaining, 0)

    @patch('usage_monitor_for_claude.app.format_tooltip', return_value='tooltip')
    @patch('usage_monitor_for_claude.app.create_icon_image')
    def test_fast_polling_decrements(self, _icon, _tooltip):
        """Fast poll counter decrements when usage is stable."""
        self.app._prev_utilization = {'five_hour': 40.0, 'seven_day': 10.0}
        self.app._fast_polls_remaining = 2
        data = {'five_hour': {'utilization': 40.0}, 'seven_day': {'utilization': 10.0}}
        self.app.cache = MagicMock()
        self.app.cache.update.return_value = UpdateResult(data=data)

        self.app.update()

        self.assertEqual(self.app._fast_polls_remaining, 1)

    @patch('usage_monitor_for_claude.app.format_tooltip', return_value='tooltip')
    @patch('usage_monitor_for_claude.app.create_icon_image')
    def test_fast_polling_not_below_zero(self, _icon, _tooltip):
        """Fast poll counter does not go below zero."""
        self.app._prev_utilization = {'five_hour': 40.0, 'seven_day': 10.0}
        self.app._fast_polls_remaining = 0
        data = {'five_hour': {'utilization': 40.0}, 'seven_day': {'utilization': 10.0}}
        self.app.cache = MagicMock()
        self.app.cache.update.return_value = UpdateResult(data=data)

        self.app.update()

        self.assertEqual(self.app._fast_polls_remaining, 0)


# ---------------------------------------------------------------------------
# _render_tray
# ---------------------------------------------------------------------------

class TestRenderTray(unittest.TestCase):
    """Tests for _render_tray() icon and tooltip rendering."""

    def setUp(self):
        self.app = _make_app()

    def tearDown(self):
        _cleanup(self.app)

    @patch('usage_monitor_for_claude.app.format_tooltip', return_value='Usage: 42%')
    @patch('usage_monitor_for_claude.app.create_icon_image')
    def test_success_renders_icon(self, mock_icon, _tooltip):
        """Successful data renders usage icon."""
        self.app._last_response = {'five_hour': {'utilization': 42.0}, 'seven_day': {'utilization': 10.0}}
        self.app._render_tray()

        mock_icon.assert_called_once_with(42.0, 10.0, False, mode_top='utilization', mode_bottom='utilization', time_pct_top=None, time_pct_bottom=None, extra_usage_available=False)
        self.assertEqual(self.app.icon.title, 'Usage: 42%')

    @patch('usage_monitor_for_claude.app.format_tooltip', return_value='Error')
    @patch('usage_monitor_for_claude.app.create_status_image')
    def test_error_renders_exclamation(self, mock_status, _tooltip):
        """Error data renders '!' status icon."""
        self.app._last_response = {'error': 'server down'}
        self.app._render_tray()

        mock_status.assert_called_once_with('!', False)

    @patch('usage_monitor_for_claude.app.format_tooltip', return_value='Auth Error')
    @patch('usage_monitor_for_claude.app.create_status_image')
    def test_auth_error_renders_c_exclamation(self, mock_status, _tooltip):
        """Auth error data renders 'C!' status icon."""
        self.app._last_response = {'error': 'expired', 'auth_error': True}
        self.app._render_tray()

        mock_status.assert_called_once_with('C!', False)

    @patch('usage_monitor_for_claude.app.format_tooltip', return_value='tooltip')
    @patch('usage_monitor_for_claude.app.create_icon_image')
    def test_missing_utilization_defaults_to_zero(self, mock_icon, _tooltip):
        """Missing utilization values default to 0."""
        self.app._last_response = {'five_hour': {}, 'seven_day': {'utilization': None}}
        self.app._render_tray()

        mock_icon.assert_called_once_with(0, 0, False, mode_top='utilization', mode_bottom='utilization', time_pct_top=None, time_pct_bottom=None, extra_usage_available=False)

    @patch('usage_monitor_for_claude.app.format_tooltip', return_value='tooltip')
    @patch('usage_monitor_for_claude.app.create_icon_image')
    @patch('usage_monitor_for_claude.app.ICON_FIELDS', ['seven_day_sonnet', 'five_hour'])
    def test_custom_icon_fields(self, mock_icon, _tooltip):
        """Custom icon_fields setting changes which fields are shown in the icon."""
        self.app._last_response = {
            'five_hour': {'utilization': 30.0},
            'seven_day_sonnet': {'utilization': 75.0},
        }
        self.app._render_tray()

        mock_icon.assert_called_once_with(75.0, 30.0, False, mode_top='utilization', mode_bottom='utilization', time_pct_top=None, time_pct_bottom=None, extra_usage_available=False)

    @patch('usage_monitor_for_claude.app.format_tooltip', return_value='tooltip')
    @patch('usage_monitor_for_claude.app.create_icon_image')
    @patch('usage_monitor_for_claude.app.ICON_FIELDS', ['unknown_field', 'five_hour'])
    def test_icon_fields_missing_from_response_defaults_to_zero(self, mock_icon, _tooltip):
        """Icon field not present in API response defaults to 0%."""
        self.app._last_response = {'five_hour': {'utilization': 42.0}}
        self.app._render_tray()

        mock_icon.assert_called_once_with(0, 42.0, False, mode_top='utilization', mode_bottom='utilization', time_pct_top=None, time_pct_bottom=None, extra_usage_available=False)

    @patch('usage_monitor_for_claude.app.format_tooltip', return_value='tooltip')
    @patch('usage_monitor_for_claude.app.create_icon_image')
    @patch('usage_monitor_for_claude.app.ICON_FIELDS', ['seven_day_sonnet', 'five_hour'])
    def test_icon_fields_null_in_response_defaults_to_zero(self, mock_icon, _tooltip):
        """Icon field present but null in API response defaults to 0%."""
        self.app._last_response = {'five_hour': {'utilization': 42.0}, 'seven_day_sonnet': None}
        self.app._render_tray()

        mock_icon.assert_called_once_with(0, 42.0, False, mode_top='utilization', mode_bottom='utilization', time_pct_top=None, time_pct_bottom=None, extra_usage_available=False)

    @patch('usage_monitor_for_claude.app.format_tooltip', return_value='tooltip')
    @patch('usage_monitor_for_claude.app.create_icon_image')
    @patch('usage_monitor_for_claude.app.ICON_FIELDS', ['limits', 'five_hour'])
    def test_icon_field_pointing_to_non_dict_defaults_to_zero(self, mock_icon, _tooltip):
        """An icon field holding a non-dict response value (e.g. the limits array)
        renders as 0% instead of crashing the render path."""
        self.app._last_response = {'five_hour': {'utilization': 42.0}, 'limits': [{'percent': 12}]}
        self.app._render_tray()

        mock_icon.assert_called_once_with(0, 42.0, False, mode_top='utilization', mode_bottom='utilization', time_pct_top=None, time_pct_bottom=None, extra_usage_available=False)

    @patch('usage_monitor_for_claude.app.format_tooltip', return_value='tooltip')
    @patch('usage_monitor_for_claude.app.create_icon_image')
    @patch('usage_monitor_for_claude.app.elapsed_pct', return_value=40.0)
    @patch('usage_monitor_for_claude.app.ICON_FIELDS', ['five_hour:overage', 'seven_day'])
    def test_overage_mode_passes_time_pct(self, mock_elapsed, mock_icon, _tooltip):
        """Elapsed time pct is passed for both bars regardless of display mode."""
        self.app._last_response = {
            'five_hour': {'utilization': 60.0, 'resets_at': '2025-01-15T18:00:00Z'},
            'seven_day': {'utilization': 20.0},
        }
        self.app._render_tray()

        mock_icon.assert_called_once_with(60.0, 20.0, False, mode_top='overage', mode_bottom='utilization', time_pct_top=40.0, time_pct_bottom=40.0, extra_usage_available=False)

    @patch('usage_monitor_for_claude.app.format_tooltip', return_value='tooltip')
    @patch('usage_monitor_for_claude.app.create_icon_image')
    @patch('usage_monitor_for_claude.app.elapsed_pct', return_value=50.0)
    @patch('usage_monitor_for_claude.app.ICON_FIELDS', ['five_hour:overage', 'seven_day:overage'])
    def test_both_overage_mode_passes_both_time_pcts(self, mock_elapsed, mock_icon, _tooltip):
        """Both bars in overage mode pass elapsed time pct for both top and bottom."""
        self.app._last_response = {
            'five_hour': {'utilization': 30.0, 'resets_at': '2025-01-15T18:00:00Z'},
            'seven_day': {'utilization': 10.0, 'resets_at': '2025-01-20T00:00:00Z'},
        }
        self.app._render_tray()

        mock_icon.assert_called_once_with(30.0, 10.0, False, mode_top='overage', mode_bottom='overage', time_pct_top=50.0, time_pct_bottom=50.0, extra_usage_available=False)

    @patch('usage_monitor_for_claude.app.format_tooltip', return_value='tooltip')
    @patch('usage_monitor_for_claude.app.create_icon_image')
    @patch('usage_monitor_for_claude.app.elapsed_pct', return_value=35.0)
    def test_utilization_mode_passes_time_pct(self, mock_elapsed, mock_icon, _tooltip):
        """Default utilization mode passes elapsed time pct so the bars can draw the reset-time marker."""
        self.app._last_response = {
            'five_hour': {'utilization': 42.0, 'resets_at': '2025-01-15T18:00:00Z'},
            'seven_day': {'utilization': 10.0, 'resets_at': '2025-01-20T00:00:00Z'},
        }
        self.app._render_tray()

        mock_icon.assert_called_once_with(42.0, 10.0, False, mode_top='utilization', mode_bottom='utilization', time_pct_top=35.0, time_pct_bottom=35.0, extra_usage_available=False)

    @patch('usage_monitor_for_claude.app.format_tooltip', return_value='tooltip')
    @patch('usage_monitor_for_claude.app.create_icon_image')
    @patch('usage_monitor_for_claude.app.ICON_FIELDS', ['five_hour:overage', 'seven_day'])
    def test_overage_mode_field_parsed_as_dict_key(self, mock_icon, _tooltip):
        """Field name in overage mode is correctly stripped of mode suffix for data lookup."""
        self.app._last_response = {
            'five_hour': {'utilization': 55.0, 'resets_at': '2025-01-15T18:00:00Z'},
            'seven_day': {'utilization': 25.0},
        }
        self.app._render_tray()

        # pct_top should be 55.0 (not 0), confirming 'five_hour' was used as dict key not 'five_hour:overage'
        call_args = mock_icon.call_args
        self.assertEqual(call_args[0][0], 55.0)

    @patch('usage_monitor_for_claude.app.format_tooltip', return_value='tooltip')
    @patch('usage_monitor_for_claude.app.create_icon_image')
    def test_extra_usage_available_true_when_credits_remain(self, mock_icon, _tooltip):
        """extra_usage_available is True when extra-usage is enabled and credits are not exhausted."""
        self.app._last_response = {
            'five_hour': {'utilization': 100.0},
            'seven_day': {'utilization': 80.0},
            'extra_usage': {'is_enabled': True, 'monthly_limit': 1000, 'used_credits': 250.0},
        }
        self.app._render_tray()

        self.assertTrue(mock_icon.call_args.kwargs['extra_usage_available'])

    @patch('usage_monitor_for_claude.app.format_tooltip', return_value='tooltip')
    @patch('usage_monitor_for_claude.app.create_icon_image')
    def test_extra_usage_available_false_when_disabled(self, mock_icon, _tooltip):
        """extra_usage_available is False when the account has not enabled extra usage."""
        self.app._last_response = {
            'five_hour': {'utilization': 100.0},
            'extra_usage': {'is_enabled': False, 'monthly_limit': 0, 'used_credits': 0},
        }
        self.app._render_tray()

        self.assertFalse(mock_icon.call_args.kwargs['extra_usage_available'])

    @patch('usage_monitor_for_claude.app.format_tooltip', return_value='tooltip')
    @patch('usage_monitor_for_claude.app.create_icon_image')
    def test_extra_usage_available_false_when_credits_exhausted(self, mock_icon, _tooltip):
        """extra_usage_available is False when all credits have been spent."""
        self.app._last_response = {
            'five_hour': {'utilization': 100.0},
            'extra_usage': {'is_enabled': True, 'monthly_limit': 1000, 'used_credits': 1000.0},
        }
        self.app._render_tray()

        self.assertFalse(mock_icon.call_args.kwargs['extra_usage_available'])

    @patch('usage_monitor_for_claude.app.format_tooltip', return_value='tooltip')
    @patch('usage_monitor_for_claude.app.create_icon_image')
    def test_extra_usage_available_false_when_no_extra_usage_key(self, mock_icon, _tooltip):
        """extra_usage_available is False when the API response omits the extra_usage object entirely."""
        self.app._last_response = {'five_hour': {'utilization': 100.0}}
        self.app._render_tray()

        self.assertFalse(mock_icon.call_args.kwargs['extra_usage_available'])

    @patch('usage_monitor_for_claude.app.format_tooltip', return_value='tooltip')
    @patch('usage_monitor_for_claude.app.create_icon_image')
    def test_extra_usage_available_false_when_extra_usage_null(self, mock_icon, _tooltip):
        """extra_usage_available is False when the extra_usage field is explicitly null."""
        self.app._last_response = {'five_hour': {'utilization': 100.0}, 'extra_usage': None}
        self.app._render_tray()

        self.assertFalse(mock_icon.call_args.kwargs['extra_usage_available'])

    @patch('usage_monitor_for_claude.app.format_tooltip', return_value='tooltip')
    @patch('usage_monitor_for_claude.app.create_icon_image')
    def test_extra_usage_available_true_when_uncapped(self, mock_icon, _tooltip):
        """extra_usage_available is True for enabled extra usage with a null monthly_limit (uncapped)."""
        self.app._last_response = {
            'five_hour': {'utilization': 100.0},
            'extra_usage': {'is_enabled': True, 'monthly_limit': None, 'used_credits': 10631.0},
        }
        self.app._render_tray()

        self.assertTrue(mock_icon.call_args.kwargs['extra_usage_available'])

    @patch('usage_monitor_for_claude.app.format_tooltip', return_value='tooltip')
    @patch('usage_monitor_for_claude.app.create_icon_image')
    def test_extra_usage_available_true_when_limit_missing(self, mock_icon, _tooltip):
        """extra_usage_available is True for enabled extra usage without a monthly_limit key."""
        self.app._last_response = {
            'five_hour': {'utilization': 100.0},
            'extra_usage': {'is_enabled': True, 'used_credits': 500.0},
        }
        self.app._render_tray()

        self.assertTrue(mock_icon.call_args.kwargs['extra_usage_available'])

    @patch('usage_monitor_for_claude.app.format_tooltip', return_value='tooltip')
    @patch('usage_monitor_for_claude.app.create_icon_image')
    def test_extra_usage_available_false_when_uncapped_but_disabled(self, mock_icon, _tooltip):
        """A null monthly_limit does not make disabled extra usage available."""
        self.app._last_response = {
            'five_hour': {'utilization': 100.0},
            'extra_usage': {'is_enabled': False, 'monthly_limit': None, 'used_credits': 0},
        }
        self.app._render_tray()

        self.assertFalse(mock_icon.call_args.kwargs['extra_usage_available'])


# ---------------------------------------------------------------------------
# _on_theme_changed
# ---------------------------------------------------------------------------

class TestOnThemeChanged(unittest.TestCase):
    """Tests for _on_theme_changed() theme switch handling."""

    def setUp(self):
        self.app = _make_app()

    def tearDown(self):
        _cleanup(self.app)

    @patch('usage_monitor_for_claude.app.format_tooltip', return_value='tooltip')
    @patch('usage_monitor_for_claude.app.create_icon_image')
    @patch('usage_monitor_for_claude.app.taskbar_uses_light_theme', return_value=True)
    def test_theme_change_re_renders(self, _theme, mock_icon, _tooltip):
        """Theme change re-renders the tray icon."""
        self.app._light_taskbar = False
        self.app._last_response = {'five_hour': {'utilization': 50.0}, 'seven_day': {'utilization': 20.0}}

        self.app._on_theme_changed()

        self.assertTrue(self.app._light_taskbar)
        mock_icon.assert_called_once_with(50.0, 20.0, True, mode_top='utilization', mode_bottom='utilization', time_pct_top=None, time_pct_bottom=None, extra_usage_available=False)

    @patch('usage_monitor_for_claude.app.taskbar_uses_light_theme', return_value=False)
    def test_same_theme_no_render(self, _theme):
        """No re-render when theme hasn't changed."""
        self.app._light_taskbar = False
        self.app._last_response = {'five_hour': {'utilization': 50.0}}

        with patch.object(self.app, '_render_tray') as mock_render:
            self.app._on_theme_changed()
            mock_render.assert_not_called()

    @patch('usage_monitor_for_claude.app.taskbar_uses_light_theme', return_value=True)
    def test_theme_change_without_data_no_render(self, _theme):
        """Theme change without any data does not render."""
        self.app._light_taskbar = False
        self.app._last_response = {}

        with patch.object(self.app, '_render_tray') as mock_render:
            self.app._on_theme_changed()
            mock_render.assert_not_called()


# ---------------------------------------------------------------------------
# _calculate_poll_interval
# ---------------------------------------------------------------------------

class TestCalculatePollInterval(unittest.TestCase):
    """Tests for _calculate_poll_interval() adaptive interval logic."""

    def setUp(self):
        self.app = _make_app()

    def tearDown(self):
        _cleanup(self.app)

    def test_normal_interval(self):
        """Normal state returns POLL_INTERVAL."""
        self.app._last_response = {'five_hour': {'utilization': 50.0}}
        interval = self.app._calculate_poll_interval()
        self.assertEqual(interval, 30)

    def test_fast_polling_interval(self):
        """When fast polling is active, returns POLL_FAST."""
        self.app._last_response = {'five_hour': {'utilization': 50.0}}
        self.app._fast_polls_remaining = 3
        interval = self.app._calculate_poll_interval()
        self.assertEqual(interval, 30)

    def test_error_interval(self):
        """Transient error returns POLL_ERROR."""
        self.app._last_response = {'error': 'server down'}
        interval = self.app._calculate_poll_interval()
        self.assertEqual(interval, 30)

    def test_rate_limited_with_high_remaining(self):
        """Rate-limited uses cache.rate_limit_remaining for the interval."""
        self.app._last_response = {'error': 'rate limited', 'rate_limited': True}
        self.app.cache = MagicMock()
        self.app.cache.rate_limit_remaining = 300.0
        interval = self.app._calculate_poll_interval()
        self.assertEqual(interval, 300)

    def test_rate_limited_with_low_remaining(self):
        """Rate-limited with low remaining uses POLL_INTERVAL as minimum."""
        self.app._last_response = {'error': 'rate limited', 'rate_limited': True}
        self.app.cache = MagicMock()
        self.app.cache.rate_limit_remaining = 10.0
        interval = self.app._calculate_poll_interval()
        self.assertEqual(interval, 30)

    def test_rate_limited_with_large_remaining(self):
        """Rate-limited with large remaining uses that value."""
        self.app._last_response = {'error': 'rate limited', 'rate_limited': True}
        self.app.cache = MagicMock()
        self.app.cache.rate_limit_remaining = 480.0
        interval = self.app._calculate_poll_interval()
        self.assertEqual(interval, 480)

    def test_rate_limited_remaining_capped_by_cache(self):
        """Rate-limited remaining reflects cache's capped backoff (MAX_BACKOFF=900)."""
        self.app._last_response = {'error': 'rate limited', 'rate_limited': True}
        self.app.cache = MagicMock()
        self.app.cache.rate_limit_remaining = 900.0
        interval = self.app._calculate_poll_interval()
        self.assertEqual(interval, 900)

    def test_rate_limited_expired(self):
        """Rate-limited with expired backoff uses POLL_INTERVAL."""
        self.app._last_response = {'error': 'rate limited', 'rate_limited': True}
        self.app.cache = MagicMock()
        self.app.cache.rate_limit_remaining = 0.0
        interval = self.app._calculate_poll_interval()
        self.assertEqual(interval, 30)

    def test_empty_response_returns_normal_interval(self):
        """Empty _last_response (initial state) returns POLL_INTERVAL."""
        self.app._last_response = {}
        interval = self.app._calculate_poll_interval()
        self.assertEqual(interval, 30)

    def test_away_uses_idle_interval(self):
        """While the user is away, polling slows to IDLE_INTERVAL instead of stopping."""
        self.app._last_response = {'five_hour': {'utilization': 50.0}}
        with patch.object(self.app, '_polling_throttled', return_value=True):
            interval = self.app._calculate_poll_interval()

        self.assertEqual(interval, 900)

    def test_away_error_still_uses_idle_interval(self):
        """An error while away does not fall back to the fast error cadence."""
        self.app._last_response = {'error': 'server down'}
        with patch.object(self.app, '_polling_throttled', return_value=True):
            interval = self.app._calculate_poll_interval()

        self.assertEqual(interval, 900)

    def test_away_rate_limited_keeps_longer_backoff(self):
        """A backoff longer than IDLE_INTERVAL is not shortened by the away cadence."""
        self.app._last_response = {'error': 'rate limited', 'rate_limited': True}
        self.app.cache = MagicMock()
        self.app.cache.rate_limit_remaining = 1200.0
        with patch.object(self.app, '_polling_throttled', return_value=True):
            interval = self.app._calculate_poll_interval()

        self.assertEqual(interval, 1200)

    def test_away_with_overdue_reset_keeps_normal_interval(self):
        """An unconfirmed reset keeps the normal cadence even while away."""
        self.app._last_response = {'five_hour': {'utilization': 50.0}}
        with patch.object(self.app, '_polling_throttled', return_value=True), \
             patch.object(self.app, '_reset_overdue', return_value=True):
            interval = self.app._calculate_poll_interval()

        self.assertEqual(interval, 30)

    def test_away_still_aligns_to_reset(self):
        """Reset alignment applies on the away cadence, so a reset is caught on time."""
        self.app._last_response = {'five_hour': {'utilization': 50.0}}
        with patch.object(self.app, '_polling_throttled', return_value=True), \
             patch.object(self.app, '_seconds_until_next_reset', return_value=600.0):
            interval = self.app._calculate_poll_interval()

        # 600 + RESET_BUFFER = 605 <= 900 * 1.5, so the poll is committed to
        # just after the reset instead of waiting out the full away interval.
        self.assertEqual(interval, 605)


# ---------------------------------------------------------------------------
# _seconds_until_next_reset
# ---------------------------------------------------------------------------

class TestSecondsUntilNextReset(unittest.TestCase):
    """Tests for _seconds_until_next_reset() calculation."""

    def setUp(self):
        self.app = _make_app()

    def tearDown(self):
        _cleanup(self.app)

    def test_no_data_returns_none(self):
        """No response data returns None."""
        self.app._last_response = {}
        self.assertIsNone(self.app._seconds_until_next_reset())

    def test_no_resets_at_returns_none(self):
        """Entry without resets_at returns None."""
        self.app._last_response = {'five_hour': {'utilization': 50.0}}
        self.assertIsNone(self.app._seconds_until_next_reset())

    @patch('usage_monitor_for_claude.app.datetime')
    def test_returns_seconds_to_nearest_reset(self, mock_dt):
        """Returns seconds to the nearest future reset."""
        now = datetime(2025, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
        mock_dt.now.return_value = now
        mock_dt.fromisoformat = datetime.fromisoformat

        self.app._last_response = {
            'five_hour': {'utilization': 50.0, 'resets_at': '2025-01-15T12:30:00+00:00'},
            'seven_day': {'utilization': 30.0, 'resets_at': '2025-01-15T14:00:00+00:00'},
        }

        result = self.app._seconds_until_next_reset()
        assert result is not None
        self.assertAlmostEqual(result, 1800.0, places=0)  # 30 minutes

    @patch('usage_monitor_for_claude.app.datetime')
    def test_past_reset_ignored(self, mock_dt):
        """Past reset times are ignored."""
        now = datetime(2025, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
        mock_dt.now.return_value = now
        mock_dt.fromisoformat = datetime.fromisoformat

        self.app._last_response = {
            'five_hour': {'utilization': 50.0, 'resets_at': '2025-01-15T11:00:00+00:00'},
        }

        self.assertIsNone(self.app._seconds_until_next_reset())


# ---------------------------------------------------------------------------
# Poll interval reset alignment
# ---------------------------------------------------------------------------

class TestResetAlignment(unittest.TestCase):
    """Tests for poll interval alignment with imminent reset."""

    def setUp(self):
        self.app = _make_app()

    def tearDown(self):
        _cleanup(self.app)

    def test_imminent_reset_aligns_poll(self):
        """When reset is imminent, interval aligns to reset time."""
        self.app._last_response = {'five_hour': {'utilization': 50.0}}
        with patch.object(self.app, '_seconds_until_next_reset', return_value=20.0):
            interval = self.app._calculate_poll_interval()

        # next_reset(20) + RESET_BUFFER(5) = 25 is below the POLL_FAST floor,
        # so the confirming poll stays at the minimum 30-second interval.
        self.assertEqual(interval, 30)

    def test_distant_reset_no_alignment(self):
        """When reset is far away, normal interval is used."""
        self.app._last_response = {'five_hour': {'utilization': 50.0}}
        with patch.object(self.app, '_seconds_until_next_reset', return_value=500.0):
            interval = self.app._calculate_poll_interval()

        # next_reset(500) + 5 = 505 > interval(30) * 1.5 = 45, no alignment
        self.assertEqual(interval, 30)

    def test_reset_alignment_sets_fast_polls(self):
        """Reset alignment sets fast_polls_remaining for post-reset follow-up."""
        self.app._last_response = {'five_hour': {'utilization': 50.0}}
        self.app._fast_polls_remaining = 0
        with patch.object(self.app, '_seconds_until_next_reset', return_value=20.0):
            self.app._calculate_poll_interval()

        self.assertGreaterEqual(self.app._fast_polls_remaining, 2)

# ---------------------------------------------------------------------------
# _align_to_reset
# ---------------------------------------------------------------------------

class TestAlignToReset(unittest.TestCase):
    """Tests for the pure _align_to_reset() poll-phase math.
    RESET_BUFFER = 5, POLL_FAST = 30, so the "danger" window (where a poll
    can no longer be exact) is the last 25 seconds before a reset.
    """

    def test_no_reset(self):
        """No upcoming reset keeps the normal interval, no alignment."""
        self.assertEqual(_align_to_reset(POLL_FAST, None), (POLL_FAST, False))

    def test_non_positive_reset(self):
        """A non-positive next_reset keeps the normal interval."""
        self.assertEqual(_align_to_reset(POLL_FAST, 0.0), (POLL_FAST, False))

    def test_distant_reset(self):
        """A reset beyond one cadence plus the danger window is not aligned."""
        self.assertEqual(_align_to_reset(POLL_FAST, 500.0), (POLL_FAST, False))

    def test_near_reset_commits(self):
        """An imminent reset commits the confirming poll without breaking cooldown."""
        self.assertEqual(_align_to_reset(POLL_FAST, 20.0), (POLL_FAST, True))

    def test_commit_upper_edge(self):
        """next_reset at the commit threshold still commits."""
        self.assertEqual(_align_to_reset(POLL_FAST, 40.0), (45, True))

    def test_cap_pulls_last_poll_forward(self):
        """Just past the commit threshold, the next poll is pulled to the danger boundary."""
        self.assertEqual(_align_to_reset(POLL_FAST, 45.0), (50, True))

    def test_cap_high_edge(self):
        """Highest next_reset that still needs a cap."""
        self.assertEqual(_align_to_reset(POLL_FAST, 54.0), (59, True))

    def test_just_beyond_cap_is_normal(self):
        """At interval plus danger a normal poll already lands safely at the boundary."""
        self.assertEqual(_align_to_reset(POLL_FAST, 55.0), (POLL_FAST, False))

    def test_danger_zone_falls_back_to_poll_fast(self):
        """Inside the last POLL_FAST window the confirming poll stays on the floor."""
        self.assertEqual(_align_to_reset(POLL_FAST, 25.0), (POLL_FAST, True))

    def test_danger_boundary(self):
        """Exactly at the danger boundary uses POLL_FAST."""
        self.assertEqual(_align_to_reset(POLL_FAST, 25.0), (POLL_FAST, True))

    def test_fast_base_commits_directly(self):
        """With a POLL_FAST base, an imminent reset stays on the cooldown floor."""
        self.assertEqual(_align_to_reset(POLL_FAST, 20.0), (POLL_FAST, True))

    def test_two_step_cap_then_commit_lands_after_reset(self):
        """Cap to the danger boundary, then the follow-up commits after the reset."""
        interval, aligned = _align_to_reset(POLL_FAST, 45.0)
        self.assertEqual((interval, aligned), (50, True))
        self.assertEqual(_align_to_reset(POLL_FAST, 25.0), (POLL_FAST, True))
    def test_never_schedules_below_poll_fast(self):
        """No aligned interval is ever shorter than POLL_FAST (the cache cooldown)."""
        for base in (POLL_FAST, 180):
            for next_reset in range(1, 601):
                interval, _ = _align_to_reset(base, float(next_reset))
                self.assertGreaterEqual(
                    interval, POLL_FAST,
                    f'base={base}, next_reset={next_reset} -> {interval} < POLL_FAST',
                )


# ---------------------------------------------------------------------------
# _reset_aligned_poll_target
# ---------------------------------------------------------------------------

class TestResetAlignedPollTarget(unittest.TestCase):
    """Tests for _reset_aligned_poll_target() clamping."""

    def setUp(self):
        self.app = _make_app()
        self.app.cache = MagicMock()

    def tearDown(self):
        _cleanup(self.app)

    @patch('usage_monitor_for_claude.app.time.time', return_value=1000.0)
    def test_lands_just_after_reset(self, _mock_time):
        """Well past the cooldown, the poll lands RESET_BUFFER after the reset."""
        self.app.cache.last_success_time = 700.0
        self.assertEqual(self.app._reset_aligned_poll_target(60.0), 1065.0)

    @patch('usage_monitor_for_claude.app.time.time', return_value=1000.0)
    def test_clamped_to_cooldown(self, _mock_time):
        """Inside the cooldown window the poll is delayed to last_success + POLL_FAST."""
        self.app.cache.last_success_time = 1000.0
        self.assertEqual(self.app._reset_aligned_poll_target(20.0), 1030.0)

    @patch('usage_monitor_for_claude.app.time.time', return_value=1000.0)
    def test_no_last_success_uses_reset_only(self, _mock_time):
        """Without a prior fetch only reset + buffer applies."""
        self.app.cache.last_success_time = None
        self.assertEqual(self.app._reset_aligned_poll_target(60.0), 1065.0)


class TestSafePollTarget(unittest.TestCase):
    """Tests for _safe_poll_target() keeping a poll off slots that delay the reset poll."""

    def setUp(self):
        self.app = _make_app()
        self.app.cache = MagicMock()
        self.app.cache.last_success_time = 1000.0

    def tearDown(self):
        _cleanup(self.app)

    @patch('usage_monitor_for_claude.app.time.time', return_value=1000.0)
    def test_no_reset_keeps_target(self, _mock_time):
        """Without a known reset any target is fine."""
        with patch.object(self.app, '_seconds_until_next_reset', return_value=None):
            self.assertEqual(self.app._safe_poll_target(1500.0), 1500.0)

    @patch('usage_monitor_for_claude.app.time.time', return_value=1000.0)
    def test_target_well_before_reset_kept(self, _mock_time):
        """A target far enough ahead of the reset is left alone."""
        with patch.object(self.app, '_seconds_until_next_reset', return_value=600.0):
            self.assertEqual(self.app._safe_poll_target(1200.0), 1200.0)

    @patch('usage_monitor_for_claude.app.time.time', return_value=1000.0)
    def test_target_in_danger_window_moved_to_aligned_slot(self, _mock_time):
        """A target inside the danger window is deferred past the reset."""
        with patch.object(self.app, '_seconds_until_next_reset', return_value=600.0):
            self.assertEqual(self.app._safe_poll_target(1580.0), 1605.0)

    @patch('usage_monitor_for_claude.app.time.time', return_value=1000.0)
    def test_target_past_aligned_slot_pulled_back(self, _mock_time):
        """A target beyond the reset-aligned slot would delay the confirming poll."""
        with patch.object(self.app, '_seconds_until_next_reset', return_value=600.0):
            self.assertEqual(self.app._safe_poll_target(2000.0), 1605.0)

    @patch('usage_monitor_for_claude.app.time.time', return_value=1000.0)
    def test_aligned_slot_respects_cooldown(self, _mock_time):
        """The fallback slot still honors the cache cooldown after the last fetch."""
        self.app.cache.last_success_time = 1000.0
        with patch.object(self.app, '_seconds_until_next_reset', return_value=20.0):
            self.assertEqual(self.app._safe_poll_target(1015.0), 1030.0)



class TestShouldRefreshUsage(unittest.TestCase):
    """Tests for the popup's background-refresh decision."""

    def setUp(self):
        self.app = _make_app()
        self.app.cache = MagicMock()

    def tearDown(self):
        _cleanup(self.app)

    def test_first_open_always_refreshes(self):
        """With no data yet, refresh even if a reset is imminent."""
        self.app.cache.last_success_time = None
        with patch.object(self.app, '_seconds_until_next_reset', return_value=30.0):
            self.assertTrue(self.app._should_refresh_usage())

    @patch('usage_monitor_for_claude.app.time.time', return_value=1000.0)
    def test_fresh_data_not_refreshed(self, _mock_time):
        """Data younger than the cooldown is not refreshed."""
        self.app.cache.last_success_time = 1000.0 - (POLL_FAST - 10)
        with patch.object(self.app, '_seconds_until_next_reset', return_value=None):
            self.assertFalse(self.app._should_refresh_usage())

    @patch('usage_monitor_for_claude.app.time.time', return_value=1000.0)
    def test_stale_data_refreshed_without_imminent_reset(self, _mock_time):
        """Stale data refreshes when no reset is imminent."""
        self.app.cache.last_success_time = 1000.0 - (POLL_FAST + 10)
        with patch.object(self.app, '_seconds_until_next_reset', return_value=300.0):
            self.assertTrue(self.app._should_refresh_usage())

    @patch('usage_monitor_for_claude.app.time.time', return_value=1000.0)
    def test_stale_data_deferred_when_reset_imminent(self, _mock_time):
        """Stale data is not refreshed when a reset is within the cooldown."""
        self.app.cache.last_success_time = 1000.0 - (POLL_FAST + 10)
        with patch.object(self.app, '_seconds_until_next_reset', return_value=POLL_FAST - 1):
            self.assertFalse(self.app._should_refresh_usage())

    def test_popup_refreshes_expired_codex_on_demand(self):
        """An expired Codex snapshot is refreshed by the popup thread."""
        self.app.cache.profile = {'name': 'User'}
        self.app.cache.codex_expired.return_value = True
        with patch.object(self.app, '_should_refresh_usage', return_value=False), \
             patch('usage_monitor_for_claude.app.UsagePopup'), \
             patch('usage_monitor_for_claude.app.threading.Thread') as mock_thread:
            self.app._open_popup()
            target = mock_thread.call_args.kwargs['target']
            target()

        self.app.cache.refresh_codex_if_stale.assert_called_once_with(force=True)


# ---------------------------------------------------------------------------
# Menu actions
# ---------------------------------------------------------------------------

class TestMenuActions(unittest.TestCase):
    """Tests for menu action methods."""

    def setUp(self):
        self.app = _make_app()

    def tearDown(self):
        _cleanup(self.app)

    def test_on_show_popup_guards_against_double_open(self):
        """on_show_popup() does nothing when popup is already open."""
        self.app._popup_open = True
        with patch('usage_monitor_for_claude.app.threading.Thread') as mock_thread:
            self.app.on_show_popup()
            mock_thread.assert_not_called()

    def test_on_quit_stops_running(self):
        """on_quit() sets running to False and stops the icon."""
        self.app.on_quit()
        self.assertFalse(self.app.running)
        self.app.icon.stop.assert_called_once()


# ---------------------------------------------------------------------------
# _is_user_away (idle/lock detection)
# ---------------------------------------------------------------------------

class TestIsUserAway(unittest.TestCase):
    """Tests for _is_user_away() idle and lock detection."""

    def setUp(self):
        self.app = _make_app()

    def tearDown(self):
        _cleanup(self.app)

    @patch('usage_monitor_for_claude.app.is_workstation_locked', return_value=True)
    def test_locked_is_away(self, _locked):
        """User is away when workstation is locked."""
        self.assertTrue(self.app._is_user_away())

    @patch('usage_monitor_for_claude.app.is_workstation_locked', return_value=False)
    @patch('usage_monitor_for_claude.app.get_idle_seconds', return_value=400.0)
    @patch('usage_monitor_for_claude.app.IDLE_PAUSE', 300)
    def test_idle_over_threshold_is_away(self, _idle, _locked):
        """User is away when idle time exceeds IDLE_PAUSE."""
        self.assertTrue(self.app._is_user_away())

    @patch('usage_monitor_for_claude.app.is_workstation_locked', return_value=False)
    @patch('usage_monitor_for_claude.app.get_idle_seconds', return_value=200.0)
    @patch('usage_monitor_for_claude.app.IDLE_PAUSE', 300)
    def test_idle_under_threshold_not_away(self, _idle, _locked):
        """User is not away when idle time is below IDLE_PAUSE."""
        self.assertFalse(self.app._is_user_away())

    @patch('usage_monitor_for_claude.app.is_workstation_locked', return_value=False)
    @patch('usage_monitor_for_claude.app.get_idle_seconds', return_value=300.0)
    @patch('usage_monitor_for_claude.app.IDLE_PAUSE', 300)
    def test_idle_exactly_at_threshold_is_away(self, _idle, _locked):
        """User is away when idle time equals IDLE_PAUSE exactly."""
        self.assertTrue(self.app._is_user_away())

    @patch('usage_monitor_for_claude.app.is_workstation_locked', return_value=False)
    @patch('usage_monitor_for_claude.app.get_idle_seconds', return_value=9999.0)
    @patch('usage_monitor_for_claude.app.IDLE_PAUSE', 0)
    def test_idle_disabled_with_zero(self, _idle, _locked):
        """Idle detection disabled when IDLE_PAUSE is 0."""
        self.assertFalse(self.app._is_user_away())

    @patch('usage_monitor_for_claude.app.is_workstation_locked', return_value=True)
    @patch('usage_monitor_for_claude.app.IDLE_PAUSE', 0)
    def test_locked_detected_even_when_idle_disabled(self, _locked):
        """Lock detection works even when idle detection is disabled."""
        self.assertTrue(self.app._is_user_away())

    @patch('usage_monitor_for_claude.app.is_workstation_locked', return_value=False)
    @patch('usage_monitor_for_claude.app.get_idle_seconds', return_value=0.0)
    @patch('usage_monitor_for_claude.app.IDLE_PAUSE', 300)
    def test_active_user_not_away(self, _idle, _locked):
        """User is not away when active (0 idle seconds)."""
        self.assertFalse(self.app._is_user_away())


# ---------------------------------------------------------------------------
# _polling_throttled (away cadence decision)
# ---------------------------------------------------------------------------

class TestPollingThrottled(unittest.TestCase):
    """Tests for _polling_throttled() - when polling drops to the away cadence."""

    def setUp(self):
        self.app = _make_app()

    def tearDown(self):
        _cleanup(self.app)

    def test_present_user_not_throttled(self):
        """An active user keeps the normal cadence."""
        self.assertFalse(self.app._polling_throttled())

    def test_away_user_throttled(self):
        """An idle or locked machine drops to the away cadence."""
        with patch.object(self.app, '_is_user_away', return_value=True):
            self.assertTrue(self.app._polling_throttled())

    def test_open_popup_overrides_idle(self):
        """An open popup keeps the normal cadence however long the machine sat idle."""
        self.app._popup_open = True
        with patch.object(self.app, '_is_user_away', return_value=True):
            self.assertFalse(self.app._polling_throttled())

    @patch('usage_monitor_for_claude.app.is_workstation_locked', return_value=True)
    def test_open_popup_behind_lock_screen_throttled(self, _locked):
        """A locked screen hides the popup, so the away cadence applies."""
        self.app._popup_open = True
        self.assertTrue(self.app._polling_throttled())

    @patch('usage_monitor_for_claude.app.is_screensaver_running', return_value=True)
    def test_open_popup_behind_screensaver_throttled(self, _screensaver):
        """A running screensaver covers the popup, so the away cadence applies."""
        self.app._popup_open = True
        with patch.object(self.app, '_is_user_away', return_value=True):
            self.assertTrue(self.app._polling_throttled())

    @patch('usage_monitor_for_claude.app.is_screensaver_running', return_value=True)
    def test_screensaver_alone_does_not_throttle_present_user(self, _screensaver):
        """A screensaver without an open popup still follows the idle decision."""
        self.assertFalse(self.app._polling_throttled())


# ---------------------------------------------------------------------------
# _reset_overdue
# ---------------------------------------------------------------------------

class TestResetOverdue(unittest.TestCase):
    """Tests for _reset_overdue() - a reset the API has not confirmed yet."""

    def setUp(self):
        self.app = _make_app()

    def tearDown(self):
        _cleanup(self.app)

    def _freeze(self, mock_dt):
        now = datetime(2025, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
        mock_dt.now.return_value = now
        mock_dt.fromisoformat = datetime.fromisoformat

    def test_no_data_not_overdue(self):
        """Without data nothing is overdue."""
        self.app._last_response = {}
        self.assertFalse(self.app._reset_overdue())

    @patch('usage_monitor_for_claude.app.datetime')
    def test_future_reset_not_overdue(self, mock_dt):
        """A reset still ahead is not overdue."""
        self._freeze(mock_dt)
        self.app._last_response = {'five_hour': {'utilization': 90.0, 'resets_at': '2025-01-15T12:30:00+00:00'}}
        self.assertFalse(self.app._reset_overdue())

    @patch('usage_monitor_for_claude.app.datetime')
    def test_past_reset_is_overdue(self, mock_dt):
        """A reset timestamp still in the past means the API has not reported it yet."""
        self._freeze(mock_dt)
        self.app._last_response = {'five_hour': {'utilization': 90.0, 'resets_at': '2025-01-15T11:30:00+00:00'}}
        self.assertTrue(self.app._reset_overdue())

    @patch('usage_monitor_for_claude.app.datetime')
    def test_one_overdue_among_several(self, mock_dt):
        """One overdue quota is enough, even next to fresh ones."""
        self._freeze(mock_dt)
        self.app._last_response = {
            'five_hour': {'utilization': 0.0, 'resets_at': '2025-01-15T11:59:00+00:00'},
            'seven_day': {'utilization': 30.0, 'resets_at': '2025-01-18T12:00:00+00:00'},
        }
        self.assertTrue(self.app._reset_overdue())

    def test_null_resets_at_not_overdue(self):
        """A quota without an active window (null resets_at) is not overdue."""
        self.app._last_response = {'five_hour': {'utilization': 0.0, 'resets_at': None}}
        self.assertFalse(self.app._reset_overdue())

    def test_unparsable_resets_at_not_overdue(self):
        """A malformed timestamp is ignored instead of forcing the normal cadence."""
        self.app._last_response = {'five_hour': {'utilization': 0.0, 'resets_at': 'not-a-date'}}
        self.assertFalse(self.app._reset_overdue())

    def test_timestamp_without_offset_ignored(self):
        """A timestamp without a UTC offset parses but cannot be compared - it is ignored."""
        self.app._last_response = {'five_hour': {'utilization': 0.0, 'resets_at': '2020-01-15T12:00:00'}}
        self.assertFalse(self.app._reset_overdue())
        self.assertIsNone(self.app._seconds_until_next_reset())


# ---------------------------------------------------------------------------
# Event command integration
# ---------------------------------------------------------------------------

class TestResetCommand(unittest.TestCase):
    """Tests for on_reset_command execution during quota reset."""

    def setUp(self):
        self.app = _make_app()

    def tearDown(self):
        _cleanup(self.app)

    @patch('usage_monitor_for_claude.app.ON_RESET_COMMAND', ['echo reset'])
    @patch('usage_monitor_for_claude.app.run_event_command')
    @patch('usage_monitor_for_claude.app.format_tooltip', return_value='tooltip')
    @patch('usage_monitor_for_claude.app.create_icon_image')
    def test_reset_command_fires_on_5h_drop(self, _icon, _tooltip, mock_cmd):
        """Reset command fires when 5h usage drops."""
        self.app._prev_utilization = {'five_hour': 98.0, 'seven_day': 10.0}
        data = {'five_hour': {'utilization': 20.0, 'resets_at': '2025-01-15T18:00:00Z'}, 'seven_day': {'utilization': 10.0}}
        self.app.cache = MagicMock()
        self.app.cache.update.return_value = UpdateResult(data=data)

        self.app.update()

        mock_cmd.assert_called_once()
        cmd, env = mock_cmd.call_args[0]
        self.assertEqual(cmd, ['echo reset'])
        self.assertEqual(env['USAGE_MONITOR_EVENT'], 'reset')
        self.assertEqual(env['USAGE_MONITOR_VARIANT'], 'five_hour')
        self.assertEqual(env['USAGE_MONITOR_UTILIZATION'], '20')
        self.assertEqual(env['USAGE_MONITOR_PREV_UTILIZATION'], '98')
        self.assertEqual(env['USAGE_MONITOR_UTILIZATION_FIVE_HOUR'], '20')
        self.assertEqual(env['USAGE_MONITOR_UTILIZATION_SEVEN_DAY'], '10')
        self.assertEqual(env['USAGE_MONITOR_RESETS_AT'], '2025-01-15T18:00:00Z')

    @patch('usage_monitor_for_claude.app.ON_RESET_COMMAND', ['echo reset'])
    @patch('usage_monitor_for_claude.app.run_event_command')
    @patch('usage_monitor_for_claude.app.format_tooltip', return_value='tooltip')
    @patch('usage_monitor_for_claude.app.create_icon_image')
    def test_reset_command_fires_on_7d_drop(self, _icon, _tooltip, mock_cmd):
        """Reset command fires when 7d usage drops."""
        self.app._prev_utilization = {'five_hour': 50.0, 'seven_day': 60.0}
        data = {'five_hour': {'utilization': 50.0}, 'seven_day': {'utilization': 10.0, 'resets_at': '2025-01-20T00:00:00Z'}}
        self.app.cache = MagicMock()
        self.app.cache.update.return_value = UpdateResult(data=data)

        self.app.update()

        mock_cmd.assert_called_once()
        env = mock_cmd.call_args[0][1]
        self.assertEqual(env['USAGE_MONITOR_VARIANT'], 'seven_day')
        self.assertEqual(env['USAGE_MONITOR_PREV_UTILIZATION'], '60')
        self.assertEqual(env['USAGE_MONITOR_UTILIZATION_FIVE_HOUR'], '50')
        self.assertEqual(env['USAGE_MONITOR_UTILIZATION_SEVEN_DAY'], '10')

    @patch('usage_monitor_for_claude.app.ON_RESET_COMMAND', ['echo reset'])
    @patch('usage_monitor_for_claude.app.run_event_command')
    @patch('usage_monitor_for_claude.app.format_tooltip', return_value='tooltip')
    @patch('usage_monitor_for_claude.app.create_icon_image')
    def test_reset_command_fires_on_any_drop_not_just_exhausted(self, _icon, _tooltip, mock_cmd):
        """Reset command fires on any usage drop, not just from near-exhaustion."""
        self.app._prev_utilization = {'five_hour': 30.0, 'seven_day': 10.0}
        data = {'five_hour': {'utilization': 5.0}, 'seven_day': {'utilization': 10.0}}
        self.app.cache = MagicMock()
        self.app.cache.update.return_value = UpdateResult(data=data)

        self.app.update()

        mock_cmd.assert_called_once()
        env = mock_cmd.call_args[0][1]
        self.assertEqual(env['USAGE_MONITOR_PREV_UTILIZATION'], '30')
        self.assertEqual(env['USAGE_MONITOR_UTILIZATION'], '5')

    @patch('usage_monitor_for_claude.app.ON_RESET_COMMAND', ['echo reset'])
    @patch('usage_monitor_for_claude.app.run_event_command')
    @patch('usage_monitor_for_claude.app.format_tooltip', return_value='tooltip')
    @patch('usage_monitor_for_claude.app.create_icon_image')
    def test_reset_command_missing_resets_at(self, _icon, _tooltip, mock_cmd):
        """USAGE_MONITOR_RESETS_AT is empty string when resets_at is absent from data."""
        self.app._prev_utilization = {'five_hour': 80.0, 'seven_day': 10.0}
        data = {'five_hour': {'utilization': 5.0}, 'seven_day': {'utilization': 10.0}}
        self.app.cache = MagicMock()
        self.app.cache.update.return_value = UpdateResult(data=data)

        self.app.update()

        mock_cmd.assert_called_once()
        env = mock_cmd.call_args[0][1]
        self.assertEqual(env['USAGE_MONITOR_RESETS_AT'], '')

    @patch('usage_monitor_for_claude.app.ON_RESET_COMMAND', ['echo reset'])
    @patch('usage_monitor_for_claude.app.run_event_command')
    @patch('usage_monitor_for_claude.app.format_tooltip', return_value='tooltip')
    @patch('usage_monitor_for_claude.app.create_icon_image')
    def test_reset_command_null_resets_at(self, _icon, _tooltip, mock_cmd):
        """USAGE_MONITOR_RESETS_AT is empty string when the API reports resets_at as null.

        A quota without an active window - the usual state right after a
        reset - carries ``resets_at: null``.  A None value must never reach
        the environment: the subprocess launch rejects it and the command
        silently never runs.
        """
        self.app._prev_utilization = {'five_hour': 80.0, 'seven_day': 10.0}
        data = {'five_hour': {'utilization': 0.0, 'resets_at': None}, 'seven_day': {'utilization': 10.0, 'resets_at': '2025-01-20T00:00:00Z'}}
        self.app.cache = MagicMock()
        self.app.cache.update.return_value = UpdateResult(data=data)

        self.app.update()

        mock_cmd.assert_called_once()
        env = mock_cmd.call_args[0][1]
        self.assertEqual(env['USAGE_MONITOR_VARIANT'], 'five_hour')
        self.assertEqual(env['USAGE_MONITOR_RESETS_AT'], '')
        self.assertTrue(all(isinstance(value, str) for value in env.values()))

    @patch('usage_monitor_for_claude.app.ON_RESET_COMMAND', [])
    @patch('usage_monitor_for_claude.app.run_event_command')
    @patch('usage_monitor_for_claude.app.format_tooltip', return_value='tooltip')
    @patch('usage_monitor_for_claude.app.create_icon_image')
    def test_no_command_when_setting_empty(self, _icon, _tooltip, mock_cmd):
        """No command executed when on_reset_command is empty."""
        self.app._prev_utilization = {'five_hour': 98.0, 'seven_day': 10.0}
        data = {'five_hour': {'utilization': 20.0}, 'seven_day': {'utilization': 10.0}}
        self.app.cache = MagicMock()
        self.app.cache.update.return_value = UpdateResult(data=data)

        self.app.update()

        mock_cmd.assert_not_called()

    @patch('usage_monitor_for_claude.app.ON_RESET_COMMAND', ['echo reset'])
    @patch('usage_monitor_for_claude.app.run_event_command')
    @patch('usage_monitor_for_claude.app.format_tooltip', return_value='tooltip')
    @patch('usage_monitor_for_claude.app.create_icon_image')
    def test_no_command_when_usage_increases(self, _icon, _tooltip, mock_cmd):
        """No command when usage is increasing."""
        self.app._prev_utilization = {'five_hour': 50.0, 'seven_day': 10.0}
        data = {'five_hour': {'utilization': 55.0}, 'seven_day': {'utilization': 10.0}}
        self.app.cache = MagicMock()
        self.app.cache.update.return_value = UpdateResult(data=data)

        self.app.update()

        mock_cmd.assert_not_called()

    @patch('usage_monitor_for_claude.app.ON_RESET_COMMAND', ['echo reset'])
    @patch('usage_monitor_for_claude.app.run_event_command')
    @patch('usage_monitor_for_claude.app.format_tooltip', return_value='tooltip')
    @patch('usage_monitor_for_claude.app.create_icon_image')
    def test_both_quotas_drop_fires_two_commands(self, _icon, _tooltip, mock_cmd):
        """Two commands fire when both 5h and 7d usage drop simultaneously."""
        self.app._prev_utilization = {'five_hour': 95.0, 'seven_day': 80.0}
        data = {'five_hour': {'utilization': 10.0}, 'seven_day': {'utilization': 20.0}}
        self.app.cache = MagicMock()
        self.app.cache.update.return_value = UpdateResult(data=data)

        self.app.update()

        self.assertEqual(mock_cmd.call_count, 2)
        variants = {call[0][1]['USAGE_MONITOR_VARIANT'] for call in mock_cmd.call_args_list}
        self.assertEqual(variants, {'five_hour', 'seven_day'})

    @patch('usage_monitor_for_claude.app.ON_RESET_COMMAND', ['echo reset'])
    @patch('usage_monitor_for_claude.app.run_event_command')
    @patch('usage_monitor_for_claude.app.format_tooltip', return_value='tooltip')
    @patch('usage_monitor_for_claude.app.create_icon_image')
    def test_no_command_on_first_update(self, _icon, _tooltip, mock_cmd):
        """No reset command on first update (no previous values)."""
        data = {'five_hour': {'utilization': 50.0}, 'seven_day': {'utilization': 10.0}}
        self.app.cache = MagicMock()
        self.app.cache.update.return_value = UpdateResult(data=data)

        self.app.update()

        mock_cmd.assert_not_called()

    @patch('usage_monitor_for_claude.app.ON_RESET_COMMAND', ['echo reset'])
    @patch('usage_monitor_for_claude.app.run_event_command')
    @patch('usage_monitor_for_claude.app.format_tooltip', return_value='tooltip')
    @patch('usage_monitor_for_claude.app.create_icon_image')
    def test_no_command_when_usage_stable(self, _icon, _tooltip, mock_cmd):
        """No command when usage stays the same."""
        self.app._prev_utilization = {'five_hour': 50.0, 'seven_day': 10.0}
        data = {'five_hour': {'utilization': 50.0}, 'seven_day': {'utilization': 10.0}}
        self.app.cache = MagicMock()
        self.app.cache.update.return_value = UpdateResult(data=data)

        self.app.update()

        mock_cmd.assert_not_called()

    @patch('usage_monitor_for_claude.app.ON_RESET_COMMAND', ['echo reset'])
    @patch('usage_monitor_for_claude.app.run_event_command')
    @patch('usage_monitor_for_claude.app.is_workstation_locked', return_value=True)
    @patch('usage_monitor_for_claude.app.format_tooltip', return_value='tooltip')
    @patch('usage_monitor_for_claude.app.create_icon_image')
    def test_reset_command_fires_while_notification_deferred(self, _icon, _tooltip, _locked, mock_cmd):
        """Reset command fires immediately even when notification is deferred due to idle/lock."""
        self.app._prev_utilization = {'five_hour': 97.0, 'seven_day': 50.0}
        data = {'five_hour': {'utilization': 10.0}, 'seven_day': {'utilization': 50.0}}
        self.app.cache = MagicMock()
        self.app.cache.update.return_value = UpdateResult(data=data)

        self.app.update()

        mock_cmd.assert_called_once()
        self.assertEqual(mock_cmd.call_args[0][1]['USAGE_MONITOR_EVENT'], 'reset')
        self.assertIn('reset', self.app._deferred_notifications)
        self.app.icon.notify.assert_not_called()


class TestThresholdCommand(unittest.TestCase):
    """Tests for on_threshold_command execution during threshold alerts."""

    def setUp(self):
        self.app = _make_app()
        self.app._prev_utilization = {'five_hour': 0.0}
        self.app._first_update_done = True

    def tearDown(self):
        _cleanup(self.app)

    @patch('usage_monitor_for_claude.app.ON_THRESHOLD_COMMAND', ['notify.bat'])
    @patch('usage_monitor_for_claude.app.ALERT_TIME_AWARE', False)
    @patch('usage_monitor_for_claude.app.run_event_command')
    def test_threshold_command_fires_on_crossing(self, mock_cmd):
        """Threshold command fires when usage crosses a configured threshold."""
        self.app._check_threshold_alerts({'five_hour': {'utilization': 85.0, 'resets_at': '2025-01-15T18:00:00Z'}})

        mock_cmd.assert_called_once()
        cmd, env = mock_cmd.call_args[0]
        self.assertEqual(cmd, ['notify.bat'])
        self.assertEqual(env['USAGE_MONITOR_EVENT'], 'threshold')
        self.assertEqual(env['USAGE_MONITOR_VARIANT'], 'five_hour')
        self.assertEqual(env['USAGE_MONITOR_UTILIZATION'], '85')
        self.assertEqual(env['USAGE_MONITOR_THRESHOLD'], '80')
        self.assertEqual(env['USAGE_MONITOR_RESETS_AT'], '2025-01-15T18:00:00Z')
        self.assertIn('USAGE_MONITOR_TITLE', env)
        self.assertIn('USAGE_MONITOR_MESSAGE', env)
        # Threshold crossings fire automatically, so they stay silent (no error dialog).
        self.assertFalse(mock_cmd.call_args[1].get('capture_output'))

    @patch('usage_monitor_for_claude.app.ON_THRESHOLD_COMMAND', ['notify.bat'])
    @patch('usage_monitor_for_claude.app.ALERT_TIME_AWARE', False)
    @patch('usage_monitor_for_claude.app.run_event_command')
    def test_threshold_command_null_resets_at(self, mock_cmd):
        """USAGE_MONITOR_RESETS_AT is empty string when resets_at is null, never None."""
        self.app._check_threshold_alerts({'five_hour': {'utilization': 85.0, 'resets_at': None}})

        mock_cmd.assert_called_once()
        env = mock_cmd.call_args[0][1]
        self.assertEqual(env['USAGE_MONITOR_RESETS_AT'], '')
        self.assertTrue(all(isinstance(value, str) for value in env.values()))

    @patch('usage_monitor_for_claude.app.ON_THRESHOLD_COMMAND', [])
    @patch('usage_monitor_for_claude.app.run_event_command')
    def test_no_command_when_setting_empty(self, mock_cmd):
        """No command executed when on_threshold_command is empty."""
        self.app._check_threshold_alerts({'five_hour': {'utilization': 85.0}})

        mock_cmd.assert_not_called()

    @patch('usage_monitor_for_claude.app.ON_THRESHOLD_COMMAND', ['notify.bat'])
    @patch('usage_monitor_for_claude.app.run_event_command')
    def test_no_command_below_threshold(self, mock_cmd):
        """No command when usage is below all thresholds."""
        self.app._check_threshold_alerts({'five_hour': {'utilization': 50.0}})

        mock_cmd.assert_not_called()

    @patch('usage_monitor_for_claude.app.ON_THRESHOLD_COMMAND', ['notify.bat'])
    @patch('usage_monitor_for_claude.app.run_event_command')
    def test_no_duplicate_command(self, mock_cmd):
        """No duplicate command for same threshold."""
        self.app._check_threshold_alerts({'five_hour': {'utilization': 85.0}})
        mock_cmd.reset_mock()

        self.app._check_threshold_alerts({'five_hour': {'utilization': 88.0}})

        mock_cmd.assert_not_called()

    @patch('usage_monitor_for_claude.app.ON_THRESHOLD_COMMAND', ['notify.bat'])
    @patch('usage_monitor_for_claude.app.run_event_command')
    def test_command_for_higher_threshold(self, mock_cmd):
        """Command fires again when usage crosses the next higher threshold."""
        self.app._check_threshold_alerts({'five_hour': {'utilization': 85.0}})
        mock_cmd.reset_mock()

        self.app._check_threshold_alerts({'five_hour': {'utilization': 97.0}})

        mock_cmd.assert_called_once()
        env = mock_cmd.call_args[0][1]
        self.assertEqual(env['USAGE_MONITOR_THRESHOLD'], '95')
        self.assertEqual(env['USAGE_MONITOR_UTILIZATION'], '97')

    @patch('usage_monitor_for_claude.app.ON_THRESHOLD_COMMAND', ['notify.bat'])
    @patch('usage_monitor_for_claude.app.ALERT_TIME_AWARE', True)
    @patch('usage_monitor_for_claude.app.ALERT_TIME_AWARE_BELOW', 90)
    @patch('usage_monitor_for_claude.app.run_event_command')
    def test_time_aware_suppression_suppresses_command(self, mock_cmd):
        """Time-aware suppression also suppresses the command."""
        with patch('usage_monitor_for_claude.app.elapsed_pct', return_value=90.0):
            self.app._check_threshold_alerts({'five_hour': {'utilization': 82.0, 'resets_at': '2025-01-15T18:00:00Z'}})

        mock_cmd.assert_not_called()

    @patch('usage_monitor_for_claude.app.ON_THRESHOLD_COMMAND', ['notify.bat'])
    @patch('usage_monitor_for_claude.app.ALERT_TIME_AWARE', False)
    @patch('usage_monitor_for_claude.app.run_event_command')
    def test_no_command_on_first_update(self, mock_cmd):
        """Threshold command is suppressed on first update (notification still fires)."""
        self.app._first_update_done = False

        self.app._check_threshold_alerts({'five_hour': {'utilization': 85.0, 'resets_at': '2025-01-15T18:00:00Z'}})

        # Notification fires (threshold was exceeded), but command does not
        self.app.icon.notify.assert_called_once()
        mock_cmd.assert_not_called()

    @patch('usage_monitor_for_claude.app.ON_THRESHOLD_COMMAND', ['notify.bat'])
    @patch('usage_monitor_for_claude.app.ALERT_TIME_AWARE', False)
    @patch('usage_monitor_for_claude.app.run_event_command')
    @patch('usage_monitor_for_claude.app.is_workstation_locked', return_value=True)
    @patch('usage_monitor_for_claude.app.format_tooltip', return_value='tooltip')
    @patch('usage_monitor_for_claude.app.create_icon_image')
    def test_threshold_command_fires_while_notification_deferred(self, _icon, _tooltip, _locked, mock_cmd):
        """Threshold command fires immediately even when notification is deferred due to idle/lock."""
        self.app._prev_utilization = {'five_hour': 50.0, 'seven_day': 10.0}
        data = {'five_hour': {'utilization': 85.0, 'resets_at': '2025-01-15T18:00:00Z'}, 'seven_day': {'utilization': 10.0}}
        self.app.cache = MagicMock()
        self.app.cache.update.return_value = UpdateResult(data=data)

        self.app.update()

        mock_cmd.assert_called_once()
        self.assertEqual(mock_cmd.call_args[0][1]['USAGE_MONITOR_EVENT'], 'threshold')
        self.assertIn('threshold_five_hour', self.app._deferred_notifications)
        self.app.icon.notify.assert_not_called()


class TestExtraUsageCommand(unittest.TestCase):
    """Tests for on_threshold_command with extra usage events."""

    def setUp(self):
        self.app = _make_app()
        self.app._prev_utilization = {'five_hour': 0.0}
        self.app._first_update_done = True

    def tearDown(self):
        _cleanup(self.app)

    @patch('usage_monitor_for_claude.app.ON_THRESHOLD_COMMAND', ['notify.bat'])
    @patch('usage_monitor_for_claude.app.run_event_command')
    def test_extra_usage_command_includes_amounts(self, mock_cmd):
        """Extra usage threshold command includes used and limit amounts."""
        data = {
            'extra_usage': {'is_enabled': True, 'monthly_limit': 1000, 'used_credits': 850},
        }
        self.app._check_threshold_alerts(data)

        mock_cmd.assert_called_once()
        env = mock_cmd.call_args[0][1]
        self.assertEqual(env['USAGE_MONITOR_VARIANT'], 'extra_usage')
        self.assertIn('USAGE_MONITOR_EXTRA_USED', env)
        self.assertIn('USAGE_MONITOR_EXTRA_LIMIT', env)

    @patch('usage_monitor_for_claude.app.ON_THRESHOLD_COMMAND', [])
    @patch('usage_monitor_for_claude.app.run_event_command')
    def test_extra_usage_no_command_when_empty(self, mock_cmd):
        """No command for extra usage when setting is empty."""
        data = {
            'extra_usage': {'is_enabled': True, 'monthly_limit': 1000, 'used_credits': 850},
        }
        self.app._check_threshold_alerts(data)

        mock_cmd.assert_not_called()


# ---------------------------------------------------------------------------
# Test event command handlers (tray context menu)
# ---------------------------------------------------------------------------

class TestTestEventCommands(unittest.TestCase):
    """Tests for on_test_* handlers that fire sample event commands from the tray menu."""

    def setUp(self):
        self.app = _make_app()

    def tearDown(self):
        _cleanup(self.app)

    @patch('usage_monitor_for_claude.app.ON_RESET_COMMAND', ['echo reset'])
    @patch('usage_monitor_for_claude.app.run_event_command')
    def test_reset_5h_fires_with_correct_env(self, mock_cmd):
        """Test reset 5h handler passes all required env vars with correct values."""
        self.app.on_test_reset_5h()

        mock_cmd.assert_called_once()
        cmd, env = mock_cmd.call_args[0]
        self.assertEqual(cmd, ['echo reset'])
        self.assertEqual(env['USAGE_MONITOR_EVENT'], 'reset')
        self.assertEqual(env['USAGE_MONITOR_VARIANT'], 'five_hour')
        self.assertEqual(env['USAGE_MONITOR_UTILIZATION'], '0')
        self.assertEqual(env['USAGE_MONITOR_PREV_UTILIZATION'], '95')
        self.assertEqual(env['USAGE_MONITOR_UTILIZATION_FIVE_HOUR'], '0')
        self.assertEqual(env['USAGE_MONITOR_UTILIZATION_SEVEN_DAY'], '45')
        self.assertIn('USAGE_MONITOR_RESETS_AT', env)
        self.assertIn('USAGE_MONITOR_TITLE', env)
        self.assertIn('USAGE_MONITOR_MESSAGE', env)
        # Test-menu invocations are user-driven, so failures are surfaced.
        self.assertTrue(mock_cmd.call_args[1].get('capture_output'))

    @patch('usage_monitor_for_claude.app.ON_RESET_COMMAND', ['echo reset'])
    @patch('usage_monitor_for_claude.app.run_event_command')
    def test_reset_7d_fires_with_correct_env(self, mock_cmd):
        """Test reset 7d handler passes all required env vars with correct values."""
        self.app.on_test_reset_7d()

        mock_cmd.assert_called_once()
        cmd, env = mock_cmd.call_args[0]
        self.assertEqual(cmd, ['echo reset'])
        self.assertEqual(env['USAGE_MONITOR_EVENT'], 'reset')
        self.assertEqual(env['USAGE_MONITOR_VARIANT'], 'seven_day')
        self.assertEqual(env['USAGE_MONITOR_UTILIZATION'], '0')
        self.assertEqual(env['USAGE_MONITOR_PREV_UTILIZATION'], '99')
        self.assertEqual(env['USAGE_MONITOR_UTILIZATION_FIVE_HOUR'], '12')
        self.assertEqual(env['USAGE_MONITOR_UTILIZATION_SEVEN_DAY'], '0')
        self.assertIn('USAGE_MONITOR_RESETS_AT', env)

    @patch('usage_monitor_for_claude.app.ON_THRESHOLD_COMMAND', ['notify.bat'])
    @patch('usage_monitor_for_claude.app.run_event_command')
    def test_threshold_5h_fires_with_correct_env(self, mock_cmd):
        """Test threshold 5h handler passes all required env vars with correct values."""
        self.app.on_test_threshold_5h()

        mock_cmd.assert_called_once()
        cmd, env = mock_cmd.call_args[0]
        self.assertEqual(cmd, ['notify.bat'])
        self.assertEqual(env['USAGE_MONITOR_EVENT'], 'threshold')
        self.assertEqual(env['USAGE_MONITOR_VARIANT'], 'five_hour')
        self.assertEqual(env['USAGE_MONITOR_UTILIZATION'], '82')
        self.assertEqual(env['USAGE_MONITOR_THRESHOLD'], '80')
        self.assertIn('USAGE_MONITOR_RESETS_AT', env)
        self.assertIn('USAGE_MONITOR_TITLE', env)
        self.assertIn('USAGE_MONITOR_MESSAGE', env)

    @patch('usage_monitor_for_claude.app.ON_THRESHOLD_COMMAND', ['notify.bat'])
    @patch('usage_monitor_for_claude.app.run_event_command')
    def test_threshold_7d_fires_with_correct_env(self, mock_cmd):
        """Test threshold 7d handler passes all required env vars with correct values."""
        self.app.on_test_threshold_7d()

        mock_cmd.assert_called_once()
        cmd, env = mock_cmd.call_args[0]
        self.assertEqual(cmd, ['notify.bat'])
        self.assertEqual(env['USAGE_MONITOR_EVENT'], 'threshold')
        self.assertEqual(env['USAGE_MONITOR_VARIANT'], 'seven_day')
        self.assertEqual(env['USAGE_MONITOR_UTILIZATION'], '81')
        self.assertEqual(env['USAGE_MONITOR_THRESHOLD'], '80')
        self.assertIn('USAGE_MONITOR_RESETS_AT', env)

    @patch('usage_monitor_for_claude.app.ON_RESET_COMMAND', ['echo reset'])
    @patch('usage_monitor_for_claude.app.run_event_command')
    def test_reset_5h_resets_at_is_valid_iso_timestamp(self, mock_cmd):
        """USAGE_MONITOR_RESETS_AT is a parseable ISO 8601 timestamp in the future."""
        self.app.on_test_reset_5h()

        env = mock_cmd.call_args[0][1]
        resets_at = datetime.fromisoformat(env['USAGE_MONITOR_RESETS_AT'])
        self.assertGreater(resets_at, datetime.now(timezone.utc))

    @patch('usage_monitor_for_claude.app.ON_THRESHOLD_COMMAND', ['notify.bat'])
    @patch('usage_monitor_for_claude.app.run_event_command')
    def test_threshold_5h_resets_at_is_valid_iso_timestamp(self, mock_cmd):
        """USAGE_MONITOR_RESETS_AT is a parseable ISO 8601 timestamp in the future."""
        self.app.on_test_threshold_5h()

        env = mock_cmd.call_args[0][1]
        resets_at = datetime.fromisoformat(env['USAGE_MONITOR_RESETS_AT'])
        self.assertGreater(resets_at, datetime.now(timezone.utc))

    @patch('usage_monitor_for_claude.app.ON_THRESHOLD_COMMAND', ['notify.bat'])
    @patch('usage_monitor_for_claude.app.run_event_command')
    def test_threshold_message_contains_utilization_pct(self, mock_cmd):
        """USAGE_MONITOR_MESSAGE includes the utilization percentage."""
        self.app.on_test_threshold_5h()

        env = mock_cmd.call_args[0][1]
        self.assertIn('82', env['USAGE_MONITOR_MESSAGE'])


# ---------------------------------------------------------------------------
# poll_loop away cadence
# ---------------------------------------------------------------------------

class TestPollLoopAwayCadence(unittest.TestCase):
    """Tests for poll_loop continuing on a reduced cadence while the user is away."""

    def setUp(self):
        self.app = _make_app()
        self.app.cache = MagicMock()
        self.app.cache.ensure_profile = MagicMock()
        self.app.cache.last_success_time = 0.0

    def tearDown(self):
        _cleanup(self.app)

    @patch('usage_monitor_for_claude.app.time.sleep')
    @patch('usage_monitor_for_claude.app.time.time', return_value=1000.0)
    def test_away_keeps_polling(self, _mock_time, _mock_sleep):
        """Polling continues while the user stays away - the loop never blocks."""
        self.app.cache.last_success_time = 1000.0
        update_count = [0]

        def update_side_effect(force=False):
            update_count[0] += 1
            if update_count[0] >= 2:
                self.app.running = False

        # Each pass reaches its target immediately, so a still-away user gets
        # another poll instead of the loop waiting for activity.
        with patch.object(self.app, 'update', side_effect=update_side_effect), \
             patch.object(self.app, '_calculate_poll_interval', return_value=0), \
             patch.object(self.app, '_polling_throttled', return_value=True), \
             patch.object(self.app, '_seconds_until_next_reset', return_value=None):
            self.app.poll_loop()

        self.assertEqual(update_count[0], 2)

    @patch('usage_monitor_for_claude.app.time.sleep')
    @patch('usage_monitor_for_claude.app.time.time', return_value=1000.0)
    def test_return_polls_once_normal_interval_elapsed(self, _mock_time, _mock_sleep):
        """Coming back polls right away when the normal interval has already elapsed."""
        self.app.cache.last_success_time = 500.0
        # The away interval applies until the user returns, the normal one after.
        intervals = [900, 180]
        polls = []

        with patch.object(self.app, 'update', side_effect=lambda force=False: polls.append(force)), \
             patch.object(self.app, '_calculate_poll_interval', side_effect=lambda: intervals.pop(0) if intervals else 180), \
             patch.object(self.app, '_polling_throttled', side_effect=_returns_from_away(self.app)), \
             patch.object(self.app, '_seconds_until_next_reset', return_value=None):
            self.app.poll_loop()

        # The away target (1000 + 900) was pulled back to last_success + 180,
        # which is already in the past, so a second poll ran immediately.
        self.assertEqual(len(polls), 2)

    @patch('usage_monitor_for_claude.app.time.sleep')
    @patch('usage_monitor_for_claude.app.time.time', return_value=1000.0)
    def test_return_waits_out_remaining_interval(self, _mock_time, _mock_sleep):
        """Coming back shortly after a fetch waits out the rest of the normal interval."""
        self.app.cache.last_success_time = 950.0
        # The away interval applies until the user returns, the normal one after.
        intervals = [900, 180]
        polls = []

        with patch.object(self.app, 'update', side_effect=lambda force=False: polls.append(force)), \
             patch.object(self.app, '_calculate_poll_interval', side_effect=lambda: intervals.pop(0) if intervals else 180), \
             patch.object(self.app, '_polling_throttled', side_effect=_returns_from_away(self.app)), \
             patch.object(self.app, '_seconds_until_next_reset', return_value=None):
            self.app.poll_loop()

        self.assertEqual(len(polls), 1)
        self.assertEqual(self.app._next_poll_time, 950.0 + 180)

    @patch('usage_monitor_for_claude.app.time.sleep')
    @patch('usage_monitor_for_claude.app.time.time', return_value=1000.0)
    def test_return_near_reset_realigns_instead_of_polling(self, _mock_time, _mock_sleep):
        """Returning within the cooldown before a reset defers the poll to just after it."""
        self.app.cache.last_success_time = 1000.0
        # The away interval applies until the user returns, the normal one after.
        intervals = [900, 180]
        polls = []

        with patch.object(self.app, 'update', side_effect=lambda force=False: polls.append(force)), \
             patch.object(self.app, '_calculate_poll_interval', side_effect=lambda: intervals.pop(0) if intervals else 180), \
             patch.object(self.app, '_polling_throttled', side_effect=_returns_from_away(self.app)), \
             patch.object(self.app, '_seconds_until_next_reset', return_value=30.0):
            self.app.poll_loop()

        # Poll deferred just after the reset: max(now + 30 + RESET_BUFFER, cooldown).
        self.assertEqual(len(polls), 1)
        self.assertEqual(self.app._next_poll_time, 1000.0 + 30.0 + RESET_BUFFER)

    @patch('usage_monitor_for_claude.app.time.sleep')
    @patch('usage_monitor_for_claude.app.time.time', return_value=100.0)
    def test_account_switch_detected_while_away(self, _mock_time, _mock_sleep):
        """A token change is picked up on a locked machine, not only when present."""
        force_calls = []

        def update_side_effect(force=False):
            force_calls.append(force)
            if len(force_calls) >= 2:
                self.app.running = False

        with patch.object(self.app, 'update', side_effect=update_side_effect), \
             patch.object(self.app, '_calculate_poll_interval', return_value=900), \
             patch.object(self.app, '_account_switched', return_value=True), \
             patch.object(self.app, '_polling_throttled', return_value=True), \
             patch.object(self.app, '_seconds_until_next_reset', return_value=None), \
             patch('usage_monitor_for_claude.app.read_access_token', side_effect=['tok-a', 'tok-b', 'tok-b', 'tok-b']):
            self.app.poll_loop()

        self.assertEqual(force_calls, [False, True])

    @patch('usage_monitor_for_claude.app.ON_RESET_COMMAND', [])
    @patch('usage_monitor_for_claude.app.time.time', return_value=1000.0)
    def test_midwait_fetch_near_reset_capped_to_reset_slot(self, mock_time):
        """A concurrent fetch near a reset must not push the poll a full interval past it."""
        self.app.cache.last_success_time = 900.0

        def advance_success(_seconds):
            # Simulate a popup fetch completing mid-wait.
            self.app.cache.last_success_time = 1000.0

        with patch.object(self.app, 'update'), \
             patch.object(self.app, '_calculate_poll_interval', return_value=180), \
             patch.object(self.app, '_polling_throttled', side_effect=_stop_after_one_pass(self.app)), \
             patch.object(self.app, '_seconds_until_next_reset', return_value=30.0), \
             patch('usage_monitor_for_claude.app.time.sleep', side_effect=advance_success):
            self.app.poll_loop()

        # Capped to the reset-aligned slot (1000 + 30 + RESET_BUFFER = 1035).
        self.assertEqual(self.app._next_poll_time, 1000.0 + 30.0 + RESET_BUFFER)

    @patch('usage_monitor_for_claude.app.ON_RESET_COMMAND', [])
    @patch('usage_monitor_for_claude.app.time.time', return_value=1000.0)
    def test_midwait_fetch_without_reset_not_capped(self, mock_time):
        """With no reset nearby, the push-forward is not clamped to a reset slot."""
        self.app.cache.last_success_time = 900.0

        def advance_success(_seconds):
            self.app.cache.last_success_time = 1000.0

        with patch.object(self.app, 'update'), \
             patch.object(self.app, '_calculate_poll_interval', return_value=180), \
             patch.object(self.app, '_polling_throttled', side_effect=_stop_after_one_pass(self.app)), \
             patch.object(self.app, '_seconds_until_next_reset', return_value=None), \
             patch('usage_monitor_for_claude.app.time.sleep', side_effect=advance_success):
            self.app.poll_loop()

        # No reset: poll stays at last_success + interval (1000 + 180 = 1180).
        self.assertEqual(self.app._next_poll_time, 1000.0 + 180)

    @patch('usage_monitor_for_claude.app.ON_RESET_COMMAND', [])
    @patch('usage_monitor_for_claude.app.time.time', return_value=1000.0)
    def test_midwait_fetch_never_lands_in_danger_window(self, mock_time):
        """A pushed-forward poll must not land in the danger window (the last
        POLL_FAST - RESET_BUFFER seconds before a reset), from where the
        reset-confirming poll would overshoot the reset by up to a cooldown."""
        self.app.cache.last_success_time = 900.0

        def advance_success(_seconds):
            self.app.cache.last_success_time = 1000.0

        # Reset in 204 s: the pushed target 1000 + 180 = 1180 is inside
        # the danger window immediately before the reset.
        with patch.object(self.app, 'update'), \
             patch.object(self.app, '_calculate_poll_interval', return_value=180), \
             patch.object(self.app, '_polling_throttled', side_effect=_stop_after_one_pass(self.app)), \
             patch.object(self.app, '_seconds_until_next_reset', return_value=204.0), \
             patch('usage_monitor_for_claude.app.time.sleep', side_effect=advance_success):
            self.app.poll_loop()

        # Deferred to the reset-aligned slot just after the reset.
        self.assertEqual(self.app._next_poll_time, 1000.0 + 204.0 + RESET_BUFFER)

    @patch('usage_monitor_for_claude.app.ON_RESET_COMMAND', [])
    def test_backward_clock_jump_reanchors_poll_target(self):
        """A backward clock jump must not leave the next poll stuck at a target
        that is now hours in the future - the wait re-anchors to the interval."""
        self.app.cache.last_success_time = 9000.0
        clock = {'now': 10000.0}

        def jump_back(_seconds):
            clock['now'] = 5000.0

        with patch.object(self.app, 'update'), \
             patch.object(self.app, '_calculate_poll_interval', return_value=180), \
             patch.object(self.app, '_polling_throttled', side_effect=_stop_after_one_pass(self.app)), \
             patch.object(self.app, '_seconds_until_next_reset', return_value=None), \
             patch('usage_monitor_for_claude.app.time.time', side_effect=lambda: clock['now']), \
             patch('usage_monitor_for_claude.app.time.sleep', side_effect=jump_back):
            self.app.poll_loop()

        self.assertEqual(self.app._next_poll_time, 5000.0 + 180)


class TestPollLoopAccountSwitch(unittest.TestCase):
    """Tests for the poll loop's reaction to a credentials token change."""

    def setUp(self):
        self.app = _make_app()
        self.app.cache = MagicMock()
        self.app.cache.last_success_time = 0.0

    def tearDown(self):
        _cleanup(self.app)

    @patch('usage_monitor_for_claude.app.time.sleep')
    @patch('usage_monitor_for_claude.app.time.time', return_value=1000.0)
    def test_deferred_notifications_flushed_when_user_present(self, _mock_time, _mock_sleep):
        """Notifications deferred while away are shown once the user is present,
        even when the poll loop's away branch is never entered (the user
        returned in the gap between the deferral and the next away check)."""
        self.app._deferred_notifications = {'reset': ('msg', 'title')}

        with patch.object(self.app, 'update'), \
             patch.object(self.app, '_calculate_poll_interval', return_value=180), \
             patch.object(self.app, '_is_user_away', return_value=False), \
             patch.object(self.app, '_polling_throttled', side_effect=_stop_after_one_pass(self.app)), \
             patch.object(self.app, '_seconds_until_next_reset', return_value=None):
            self.app.poll_loop()

        self.app.icon.notify.assert_called_once_with('msg', 'title')
        self.assertEqual(self.app._deferred_notifications, {})

    def test_flush_tolerates_concurrent_deferral(self):
        """A deferral landing while the queue is being flushed (popup thread vs
        poll thread) must neither crash the flush nor get lost."""
        self.app._deferred_notifications = {'a': ('m1', 't1'), 'b': ('m2', 't2')}

        def add_during_notify(*_args):
            self.app._deferred_notifications['c'] = ('m3', 't3')

        self.app.icon.notify.side_effect = add_during_notify

        self.app._flush_deferred_notifications()

        self.assertEqual(self.app.icon.notify.call_count, 2)
        self.assertEqual(self.app._deferred_notifications, {'c': ('m3', 't3')})

    @patch('usage_monitor_for_claude.app.time.sleep')
    @patch('usage_monitor_for_claude.app.time.time', return_value=100.0)
    def test_token_change_to_other_account_forces_update(self, _mock_time, _mock_sleep):
        """A token change confirmed as a different account triggers a forced update."""
        force_calls = []

        def update_side_effect(force=False):
            force_calls.append(force)
            if len(force_calls) >= 2:
                self.app.running = False

        with patch.object(self.app, 'update', side_effect=update_side_effect), \
             patch.object(self.app, '_calculate_poll_interval', return_value=180), \
             patch.object(self.app, '_account_switched', return_value=True), \
             patch.object(self.app, '_is_user_away', return_value=False), \
             patch.object(self.app, '_seconds_until_next_reset', return_value=None), \
             patch('usage_monitor_for_claude.app.read_access_token', side_effect=['tok-a', 'tok-b', 'tok-b', 'tok-b']):
            self.app.poll_loop()

        # First poll is the normal cadence, the second is forced by the switch.
        self.assertEqual(force_calls, [False, True])

    @patch('usage_monitor_for_claude.app.time.time', return_value=100.0)
    def test_token_refresh_same_account_does_not_force(self, _mock_time):
        """A token change that is only a refresh of the same account does not force a poll."""
        force_calls = []

        def sleep_side_effect(_seconds):
            # End the loop after one inner tick so the test terminates.
            self.app.running = False

        with patch.object(self.app, 'update', side_effect=lambda force=False: force_calls.append(force)), \
             patch.object(self.app, '_calculate_poll_interval', return_value=180), \
             patch.object(self.app, '_account_switched', return_value=False), \
             patch.object(self.app, '_is_user_away', return_value=False), \
             patch.object(self.app, '_seconds_until_next_reset', return_value=None), \
             patch('usage_monitor_for_claude.app.time.sleep', side_effect=sleep_side_effect), \
             patch('usage_monitor_for_claude.app.read_access_token', side_effect=['tok-a', 'tok-b', 'tok-b']):
            self.app.poll_loop()

        # Only the initial cadence poll ran; the same-account token change forced nothing.
        self.assertEqual(force_calls, [False])

    @patch('usage_monitor_for_claude.app.time.sleep')
    @patch('usage_monitor_for_claude.app.time.time', return_value=100.0)
    def test_token_change_retries_after_auth_error(self, _mock_time, _mock_sleep):
        """A token change while the last fetch failed auth triggers an immediate retry."""
        self.app._last_response = {'error': 'expired', 'auth_error': True}
        force_calls = []

        def update_side_effect(force=False):
            force_calls.append(force)
            if len(force_calls) >= 2:
                self.app.running = False

        with patch.object(self.app, 'update', side_effect=update_side_effect), \
             patch.object(self.app, '_calculate_poll_interval', return_value=30), \
             patch.object(self.app, '_account_switched', return_value=False), \
             patch.object(self.app, '_is_user_away', return_value=False), \
             patch.object(self.app, '_seconds_until_next_reset', return_value=None), \
             patch('usage_monitor_for_claude.app.read_access_token', side_effect=['tok-a', 'tok-b', 'tok-b', 'tok-b']):
            self.app.poll_loop()

        # Initial error poll, then an immediate (non-forced) retry on the new token.
        self.assertEqual(force_calls, [False, False])

    @patch('usage_monitor_for_claude.app.time.time', return_value=100.0)
    def test_switch_during_update_forces_next_poll(self, _mock_time):
        """A switch landing while the fetch is in flight still forces an immediate update.

        The token baseline is read before update(), so a switch that happens
        during the request is not already part of the baseline.
        """
        force_calls = []
        ticks = []
        switched = False

        def update_side_effect(force=False):
            nonlocal switched
            force_calls.append(force)
            # The user switches accounts while this request is in flight.
            switched = True
            if len(force_calls) >= 2:
                self.app.running = False

        def sleep_side_effect(_seconds):
            ticks.append(1)
            if len(ticks) >= 3:
                self.app.running = False

        with patch.object(self.app, 'update', side_effect=update_side_effect), \
             patch.object(self.app, '_calculate_poll_interval', return_value=180), \
             patch.object(self.app, '_account_switched', return_value=True), \
             patch.object(self.app, '_is_user_away', return_value=False), \
             patch.object(self.app, '_seconds_until_next_reset', return_value=None), \
             patch('usage_monitor_for_claude.app.time.sleep', side_effect=sleep_side_effect), \
             patch('usage_monitor_for_claude.app.read_access_token', side_effect=lambda: 'tok-b' if switched else 'tok-a'):
            self.app.poll_loop()

        self.assertEqual(force_calls, [False, True])


# ---------------------------------------------------------------------------
# Account switch detection
# ---------------------------------------------------------------------------

class TestAccountSwitchDetection(unittest.TestCase):
    """Tests for account switch detection and notification in update()."""

    def setUp(self):
        self.app = _make_app()
        self._cmd_patch = patch('usage_monitor_for_claude.app.run_event_command')
        self._cmd_patch.start()

    def tearDown(self):
        self._cmd_patch.stop()
        _cleanup(self.app)

    def _make_cache_mock(self, uuid, email, data):
        """Return a configured cache mock with given profile and usage data."""
        mock = MagicMock()
        mock.update.return_value = UpdateResult(data=data)
        mock.profile = {'account': {'uuid': uuid, 'email': email}}
        return mock

    @patch('usage_monitor_for_claude.app.format_tooltip', return_value='tooltip')
    @patch('usage_monitor_for_claude.app.create_icon_image')
    def test_account_switch_shows_notification(self, _icon, _tooltip):
        """Notification fires when account UUID changes between updates."""
        data = {'five_hour': {'utilization': 10.0}}
        self.app._prev_account_uuid = 'uuid-old'
        self.app.cache = self._make_cache_mock('uuid-new', 'new@example.com', data)

        self.app.update()

        self.app.icon.notify.assert_called_once()
        args = self.app.icon.notify.call_args[0]
        self.assertIn('new@example.com', args[0])

    @patch('usage_monitor_for_claude.app.format_tooltip', return_value='tooltip')
    @patch('usage_monitor_for_claude.app.create_icon_image')
    def test_no_notification_on_first_update(self, _icon, _tooltip):
        """No account switch notification on first update (_prev_account_uuid is None)."""
        data = {'five_hour': {'utilization': 10.0}}
        self.app.cache = self._make_cache_mock('uuid-1', 'user@example.com', data)

        self.app.update()

        self.app.icon.notify.assert_not_called()

    @patch('usage_monitor_for_claude.app.format_tooltip', return_value='tooltip')
    @patch('usage_monitor_for_claude.app.create_icon_image')
    def test_account_switch_clears_prev_utilization(self, _icon, _tooltip):
        """Account switch resets _prev_utilization to prevent false reset notifications."""
        data = {'five_hour': {'utilization': 5.0}, 'seven_day': {'utilization': 5.0}}
        self.app._prev_utilization = {'five_hour': 97.0, 'seven_day': 99.0}
        self.app._prev_account_uuid = 'uuid-old'
        self.app.cache = self._make_cache_mock('uuid-new', 'new@example.com', data)

        self.app.update()

        # prev_utilization must be cleared so reset detection cannot fire on next cycle
        self.assertEqual(self.app._prev_utilization, {})

    @patch('usage_monitor_for_claude.app.format_tooltip', return_value='tooltip')
    @patch('usage_monitor_for_claude.app.create_icon_image')
    def test_account_switch_clears_notified_thresholds(self, _icon, _tooltip):
        """Account switch resets _notified_thresholds so threshold alerts re-arm for new account."""
        data = {'five_hour': {'utilization': 85.0}}
        self.app._notified_thresholds = {'five_hour': 80.0}
        self.app._prev_account_uuid = 'uuid-old'
        self.app.cache = self._make_cache_mock('uuid-new', 'new@example.com', data)

        self.app.update()

        self.assertEqual(self.app._notified_thresholds, {})

    @patch('usage_monitor_for_claude.app.format_tooltip', return_value='tooltip')
    @patch('usage_monitor_for_claude.app.create_icon_image')
    def test_account_switch_no_reset_notification(self, _icon, _tooltip):
        """No quota reset notification fires when account switches (even if utilization dropped from high)."""
        # Old account was near limit; new account has low utilization
        data = {'five_hour': {'utilization': 5.0}, 'seven_day': {'utilization': 5.0}}
        self.app._prev_utilization = {'five_hour': 97.0, 'seven_day': 99.0}
        self.app._prev_account_uuid = 'uuid-old'
        self.app.cache = self._make_cache_mock('uuid-new', 'new@example.com', data)

        self.app.update()

        # Only the account switch notification - no reset notification
        self.assertEqual(self.app.icon.notify.call_count, 1)
        title_arg = self.app.icon.notify.call_args[0][1]
        self.assertNotIn('Reset', title_arg)

    @patch('usage_monitor_for_claude.app.format_tooltip', return_value='tooltip')
    @patch('usage_monitor_for_claude.app.create_icon_image')
    def test_same_account_no_notification(self, _icon, _tooltip):
        """No account switch notification when UUID is unchanged."""
        data = {'five_hour': {'utilization': 50.0}}
        self.app._prev_account_uuid = 'uuid-same'
        self.app.cache = self._make_cache_mock('uuid-same', 'user@example.com', data)

        with patch.object(self.app, '_check_threshold_alerts'):
            self.app.update()

        self.app.icon.notify.assert_not_called()

    @patch('usage_monitor_for_claude.app.is_workstation_locked', return_value=True)
    @patch('usage_monitor_for_claude.app.format_tooltip', return_value='tooltip')
    @patch('usage_monitor_for_claude.app.create_icon_image')
    def test_account_switch_notification_deferred_while_idle(self, _icon, _tooltip, _locked):
        """Account switch notification is deferred when user is away."""
        data = {'five_hour': {'utilization': 10.0}}
        self.app._prev_account_uuid = 'uuid-old'
        self.app.cache = self._make_cache_mock('uuid-new', 'new@example.com', data)

        self.app.update()

        self.app.icon.notify.assert_not_called()
        self.assertIn('account_switched', self.app._deferred_notifications)

    @patch('usage_monitor_for_claude.app.format_tooltip', return_value='tooltip')
    @patch('usage_monitor_for_claude.app.create_icon_image')
    def test_account_switch_updates_prev_account_uuid(self, _icon, _tooltip):
        """After account switch, _prev_account_uuid is updated to the new UUID."""
        data = {'five_hour': {'utilization': 10.0}}
        self.app._prev_account_uuid = 'uuid-old'
        self.app.cache = self._make_cache_mock('uuid-new', 'new@example.com', data)

        self.app.update()

        self.assertEqual(self.app._prev_account_uuid, 'uuid-new')

    @patch('usage_monitor_for_claude.app.format_tooltip', return_value='tooltip')
    @patch('usage_monitor_for_claude.app.create_icon_image')
    def test_no_notification_when_profile_unavailable(self, _icon, _tooltip):
        """No account switch notification when profile could not be loaded (UUID unknown)."""
        data = {'five_hour': {'utilization': 10.0}}
        self.app._prev_account_uuid = 'uuid-old'
        mock = MagicMock()
        mock.update.return_value = UpdateResult(data=data)
        mock.profile = None
        self.app.cache = mock

        with patch.object(self.app, '_check_threshold_alerts'):
            self.app.update()

        self.app.icon.notify.assert_not_called()

    @patch('usage_monitor_for_claude.app.format_tooltip', return_value='tooltip')
    @patch('usage_monitor_for_claude.app.create_icon_image')
    def test_profile_failure_keeps_account_baseline(self, _icon, _tooltip):
        """A failed profile fetch must not wipe the known account UUID baseline."""
        data = {'five_hour': {'utilization': 10.0}}
        self.app._prev_account_uuid = 'uuid-old'
        mock = MagicMock()
        mock.update.return_value = UpdateResult(data=data)
        mock.profile = None
        self.app.cache = mock

        self.app.update()

        self.assertEqual(self.app._prev_account_uuid, 'uuid-old')

    @patch('usage_monitor_for_claude.app.format_tooltip', return_value='tooltip')
    @patch('usage_monitor_for_claude.app.create_icon_image')
    def test_switch_detected_after_transient_profile_failure(self, _icon, _tooltip):
        """An account switch is still detected when the profile fetch failed once in between."""
        self.app._prev_account_uuid = 'uuid-old'
        self.app._prev_utilization = {'five_hour': 97.0}

        # Poll during the switch: usage OK, profile fetch failed
        mock = MagicMock()
        mock.update.return_value = UpdateResult(data={'five_hour': {'utilization': 5.0}})
        mock.profile = None
        self.app.cache = mock
        self.app.update()

        # Next poll: profile is back and reports the new account
        self.app.cache = self._make_cache_mock('uuid-new', 'new@example.com', {'five_hour': {'utilization': 5.0}})
        self.app.update()

        self.app.icon.notify.assert_called_once()
        self.assertIn('new@example.com', self.app.icon.notify.call_args[0][0])
        self.assertEqual(self.app._prev_account_uuid, 'uuid-new')
        self.assertEqual(self.app._prev_utilization, {})

    @patch('usage_monitor_for_claude.app.format_tooltip', return_value='tooltip')
    @patch('usage_monitor_for_claude.app.create_icon_image')
    def test_null_account_in_profile_does_not_crash(self, _icon, _tooltip):
        """A profile response with account: null must not crash the poll thread."""
        data = {'five_hour': {'utilization': 10.0}}
        self.app._prev_account_uuid = 'uuid-old'
        mock = MagicMock()
        mock.update.return_value = UpdateResult(data=data)
        mock.profile = {'account': None, 'organization': None}
        self.app.cache = mock

        self.app.update()

        self.app.icon.notify.assert_not_called()
        self.assertEqual(self.app._prev_account_uuid, 'uuid-old')

    @patch('usage_monitor_for_claude.app.format_tooltip', return_value='tooltip')
    @patch('usage_monitor_for_claude.app.create_icon_image')
    def test_no_reset_notification_while_account_identity_unknown(self, _icon, _tooltip):
        """A usage drop is not reported as a quota reset while the profile is unknown -
        the data may already belong to a different account."""
        data = {'five_hour': {'utilization': 5.0}}
        self.app._prev_utilization = {'five_hour': 97.0}
        self.app._prev_account_uuid = 'uuid-old'
        mock = MagicMock()
        mock.update.return_value = UpdateResult(data=data)
        mock.profile = None
        self.app.cache = mock

        self.app.update()

        self.app.icon.notify.assert_not_called()
        # The old baseline is kept so the comparison can resume once the
        # account identity is known again.
        self.assertEqual(self.app._prev_utilization, {'five_hour': 97.0})

    @patch('usage_monitor_for_claude.app.read_access_token', return_value='tok-new')
    @patch('usage_monitor_for_claude.app.format_tooltip', return_value='tooltip')
    @patch('usage_monitor_for_claude.app.create_icon_image')
    def test_switch_during_fetch_defers_detection(self, _icon, _tooltip, _token):
        """Usage fetched before the switch is not paired with the new account's profile.

        Announcing the switch here would show the new account's name next to
        the old account's numbers and burn the UUID baseline, so the stale
        data would survive until the next regular poll.
        """
        data = {'five_hour': {'utilization': 10.0}}
        self.app._prev_account_uuid = 'uuid-old'
        self.app._prev_utilization = {'five_hour': 97.0}
        mock = self._make_cache_mock('uuid-new', 'new@example.com', data)
        mock.update.return_value = UpdateResult(data=data, token='tok-old')
        self.app.cache = mock

        self.app.update()

        self.app.icon.notify.assert_not_called()
        mock.ensure_profile.assert_not_called()
        # Baselines stay untouched so the refetch on the new token detects the switch.
        self.assertEqual(self.app._prev_account_uuid, 'uuid-old')
        self.assertEqual(self.app._prev_utilization, {'five_hour': 97.0})

    @patch('usage_monitor_for_claude.app.read_access_token', return_value='tok-new')
    @patch('usage_monitor_for_claude.app.format_tooltip', return_value='tooltip')
    @patch('usage_monitor_for_claude.app.create_icon_image')
    def test_switch_reported_once_refetched_on_new_token(self, _icon, _tooltip, _token):
        """The refetch on the new token reports the switch together with its usage data."""
        self.app._prev_account_uuid = 'uuid-old'

        # Poll whose fetch still used the old token - deferred.
        stale = self._make_cache_mock('uuid-new', 'new@example.com', {'five_hour': {'utilization': 90.0}})
        stale.update.return_value = UpdateResult(data={'five_hour': {'utilization': 90.0}}, token='tok-old')
        self.app.cache = stale
        self.app.update()

        # Forced refetch on the new token.
        fresh_data = {'five_hour': {'utilization': 3.0}}
        fresh = self._make_cache_mock('uuid-new', 'new@example.com', fresh_data)
        fresh.update.return_value = UpdateResult(data=fresh_data, token='tok-new')
        self.app.cache = fresh
        self.app.update(force=True)

        self.app.icon.notify.assert_called_once()
        self.assertIn('new@example.com', self.app.icon.notify.call_args[0][0])
        self.assertEqual(self.app._last_response, fresh_data)
        self.assertEqual(self.app._prev_account_uuid, 'uuid-new')

    @patch('usage_monitor_for_claude.app.read_access_token', return_value=None)
    @patch('usage_monitor_for_claude.app.format_tooltip', return_value='tooltip')
    @patch('usage_monitor_for_claude.app.create_icon_image')
    def test_unreadable_credentials_defer_detection(self, _icon, _tooltip, _token):
        """An unreadable credentials file defers the comparison instead of guessing.

        The file is rewritten during an account switch, so a read landing in
        that window returns no token - which account the data belongs to is
        unknown until the next poll.
        """
        data = {'five_hour': {'utilization': 10.0}}
        self.app._prev_account_uuid = 'uuid-old'
        mock = self._make_cache_mock('uuid-new', 'new@example.com', data)
        mock.update.return_value = UpdateResult(data=data, token='tok-old')
        self.app.cache = mock

        self.app.update()

        self.app.icon.notify.assert_not_called()
        self.assertEqual(self.app._prev_account_uuid, 'uuid-old')

    @patch('usage_monitor_for_claude.app.read_access_token', return_value='tok-a')
    @patch('usage_monitor_for_claude.app.format_tooltip', return_value='tooltip')
    @patch('usage_monitor_for_claude.app.create_icon_image')
    def test_unchanged_token_is_evaluated_normally(self, _icon, _tooltip, _token):
        """A fetch on the token still in the credentials file is compared as usual."""
        data = {'five_hour': {'utilization': 10.0}}
        self.app._prev_account_uuid = 'uuid-old'
        mock = self._make_cache_mock('uuid-new', 'new@example.com', data)
        mock.update.return_value = UpdateResult(data=data, token='tok-a')
        self.app.cache = mock

        self.app.update()

        self.app.icon.notify.assert_called_once()
        self.assertEqual(self.app._prev_account_uuid, 'uuid-new')


# ---------------------------------------------------------------------------
# _account_switched (immediate switch detection)
# ---------------------------------------------------------------------------

class TestAccountSwitchedProbe(unittest.TestCase):
    """Tests for _account_switched() used by the poll loop's token watcher."""

    def setUp(self):
        self.app = _make_app()
        self.app.cache = MagicMock()

    def tearDown(self):
        _cleanup(self.app)

    def test_no_baseline_returns_false(self):
        """Before the first account UUID is known, no switch is reported."""
        self.app._prev_account_uuid = None
        self.app.cache.profile = {'account': {'uuid': 'uuid-new'}}

        self.assertFalse(self.app._account_switched())
        self.app.cache.ensure_profile.assert_not_called()

    def test_different_uuid_returns_true(self):
        """A profile UUID differing from the baseline reports a switch."""
        self.app._prev_account_uuid = 'uuid-old'
        self.app.cache.profile = {'account': {'uuid': 'uuid-new'}}

        self.assertTrue(self.app._account_switched())

    def test_same_uuid_returns_false(self):
        """An unchanged profile UUID (mere token refresh) is not a switch."""
        self.app._prev_account_uuid = 'uuid-same'
        self.app.cache.profile = {'account': {'uuid': 'uuid-same'}}

        self.assertFalse(self.app._account_switched())

    def test_missing_profile_returns_false(self):
        """When the profile could not be loaded, no switch is reported."""
        self.app._prev_account_uuid = 'uuid-old'
        self.app.cache.profile = None

        self.assertFalse(self.app._account_switched())

    def test_null_account_returns_false(self):
        """A profile response with account: null must not crash the token watcher."""
        self.app._prev_account_uuid = 'uuid-old'
        self.app.cache.profile = {'account': None}

        self.assertFalse(self.app._account_switched())

    def test_probes_profile_bypassing_backoff(self):
        """The profile probe bypasses the rate-limit backoff so a switch is caught mid-backoff."""
        self.app._prev_account_uuid = 'uuid-old'
        self.app.cache.profile = {'account': {'uuid': 'uuid-new'}}

        self.app._account_switched()

        self.app.cache.ensure_profile.assert_called_once_with(bypass_rate_limit=True)


# ---------------------------------------------------------------------------
# update(force=...)
# ---------------------------------------------------------------------------

class TestUpdateForce(unittest.TestCase):
    """Tests that update(force=...) forwards the flag to the cache."""

    def setUp(self):
        self.app = _make_app()
        self.app.cache = MagicMock()
        self.app.cache.update.return_value = UpdateResult(data=None)

    def tearDown(self):
        _cleanup(self.app)

    def test_force_forwarded_to_cache_update(self):
        self.app.update(force=True)
        self.app.cache.update.assert_called_once_with(force=True)

    def test_default_not_forced(self):
        self.app.update()
        self.app.cache.update.assert_called_once_with(force=False)


# ---------------------------------------------------------------------------
# on_startup_command
# ---------------------------------------------------------------------------

class TestStartupCommand(unittest.TestCase):
    """Tests for on_startup_command firing on the first successful update."""

    def setUp(self):
        self.app = _make_app()
        self.app.cache = MagicMock()
        self.app.cache.profile = {'account': {'uuid': 'uuid-1', 'email': 'a@b'}}

    def tearDown(self):
        _cleanup(self.app)

    @patch('usage_monitor_for_claude.app.ON_STARTUP_COMMAND', ['echo startup'])
    @patch('usage_monitor_for_claude.app.run_event_command')
    @patch('usage_monitor_for_claude.app.format_tooltip', return_value='tooltip')
    @patch('usage_monitor_for_claude.app.create_icon_image')
    def test_fires_on_first_successful_update(self, _icon, _tooltip, mock_cmd):
        """Startup command fires once on the first successful update."""
        data = {
            'five_hour': {'utilization': 0.0, 'resets_at': None},
            'seven_day': {'utilization': 45.0, 'resets_at': '2025-01-20T12:00:00Z'},
        }
        self.app.cache.update.return_value = UpdateResult(data=data)

        self.app.update()

        mock_cmd.assert_called_once()
        cmd, env = mock_cmd.call_args[0]
        self.assertEqual(cmd, ['echo startup'])
        self.assertEqual(env['USAGE_MONITOR_EVENT'], 'startup')
        self.assertEqual(env['USAGE_MONITOR_UTILIZATION_FIVE_HOUR'], '0')
        self.assertEqual(env['USAGE_MONITOR_RESETS_AT_FIVE_HOUR'], '')
        self.assertEqual(env['USAGE_MONITOR_UTILIZATION_SEVEN_DAY'], '45')
        self.assertEqual(env['USAGE_MONITOR_RESETS_AT_SEVEN_DAY'], '2025-01-20T12:00:00Z')
        # Startup fires automatically (not user-driven), so it stays silent.
        self.assertFalse(mock_cmd.call_args[1].get('capture_output'))

    @patch('usage_monitor_for_claude.app.ON_STARTUP_COMMAND', ['echo startup'])
    @patch('usage_monitor_for_claude.app.run_event_command')
    @patch('usage_monitor_for_claude.app.format_tooltip', return_value='tooltip')
    @patch('usage_monitor_for_claude.app.create_icon_image')
    def test_fires_only_once_across_multiple_updates(self, _icon, _tooltip, mock_cmd):
        """Startup command does not fire again on subsequent updates."""
        data = {'five_hour': {'utilization': 0.0}, 'seven_day': {'utilization': 10.0}}
        self.app.cache.update.return_value = UpdateResult(data=data)

        self.app.update()
        self.app.update()
        self.app.update()

        mock_cmd.assert_called_once()

    @patch('usage_monitor_for_claude.app.ON_STARTUP_COMMAND', [])
    @patch('usage_monitor_for_claude.app.run_event_command')
    @patch('usage_monitor_for_claude.app.format_tooltip', return_value='tooltip')
    @patch('usage_monitor_for_claude.app.create_icon_image')
    def test_no_fire_when_command_unset(self, _icon, _tooltip, mock_cmd):
        """Startup command is not invoked when ON_STARTUP_COMMAND is empty."""
        data = {'five_hour': {'utilization': 0.0}, 'seven_day': {'utilization': 10.0}}
        self.app.cache.update.return_value = UpdateResult(data=data)

        self.app.update()

        mock_cmd.assert_not_called()

    @patch('usage_monitor_for_claude.app.ON_STARTUP_COMMAND', ['echo startup'])
    @patch('usage_monitor_for_claude.app.run_event_command')
    @patch('usage_monitor_for_claude.app.format_tooltip', return_value='tooltip')
    @patch('usage_monitor_for_claude.app.create_icon_image')
    def test_no_fire_on_error_response(self, _icon, _tooltip, mock_cmd):
        """Startup command is skipped when the first update returns an error."""
        self.app.cache.update.return_value = UpdateResult(data={'error': 'connection failed'})

        self.app.update()

        mock_cmd.assert_not_called()
        self.assertFalse(self.app._first_update_done)

    @patch('usage_monitor_for_claude.app.ON_STARTUP_COMMAND', ['echo startup'])
    @patch('usage_monitor_for_claude.app.run_event_command')
    @patch('usage_monitor_for_claude.app.format_tooltip', return_value='tooltip')
    @patch('usage_monitor_for_claude.app.create_icon_image')
    def test_fires_after_initial_error_then_success(self, _icon, _tooltip, mock_cmd):
        """Startup command fires on the first SUCCESSFUL update, even after errors."""
        ok_data = {'five_hour': {'utilization': 0.0}, 'seven_day': {'utilization': 10.0}}
        self.app.cache.update.side_effect = [
            UpdateResult(data={'error': 'offline'}),
            UpdateResult(data=ok_data),
        ]

        self.app.update()
        self.assertEqual(mock_cmd.call_count, 0)

        self.app.update()
        self.assertEqual(mock_cmd.call_count, 1)
        self.assertEqual(mock_cmd.call_args[0][1]['USAGE_MONITOR_EVENT'], 'startup')

    @patch('usage_monitor_for_claude.app.ON_STARTUP_COMMAND', ['echo startup'])
    @patch('usage_monitor_for_claude.app.run_event_command')
    @patch('usage_monitor_for_claude.app.format_tooltip', return_value='tooltip')
    @patch('usage_monitor_for_claude.app.create_icon_image')
    def test_extra_usage_env_vars_when_enabled(self, _icon, _tooltip, mock_cmd):
        """Extra usage env vars are emitted when extra_usage is enabled."""
        data = {
            'five_hour': {'utilization': 10.0, 'resets_at': '2025-01-15T18:00:00Z'},
            'extra_usage': {'is_enabled': True, 'used_credits': 8.20, 'monthly_limit': 10.0},
        }
        self.app.cache.update.return_value = UpdateResult(data=data)

        self.app.update()

        env = mock_cmd.call_args[0][1]
        self.assertIn('USAGE_MONITOR_EXTRA_USED', env)
        self.assertIn('USAGE_MONITOR_EXTRA_LIMIT', env)
        self.assertNotIn('USAGE_MONITOR_UTILIZATION_EXTRA_USAGE', env)

    @patch('usage_monitor_for_claude.app.ON_STARTUP_COMMAND', ['echo startup'])
    @patch('usage_monitor_for_claude.app.run_event_command')
    @patch('usage_monitor_for_claude.app.format_tooltip', return_value='tooltip')
    @patch('usage_monitor_for_claude.app.create_icon_image')
    def test_extra_usage_env_omits_limit_when_uncapped(self, _icon, _tooltip, mock_cmd):
        """USAGE_MONITOR_EXTRA_LIMIT is omitted when extra usage has no monthly limit."""
        data = {
            'five_hour': {'utilization': 10.0, 'resets_at': '2025-01-15T18:00:00Z'},
            'extra_usage': {'is_enabled': True, 'used_credits': 8.20, 'monthly_limit': None},
        }
        self.app.cache.update.return_value = UpdateResult(data=data)

        self.app.update()

        env = mock_cmd.call_args[0][1]
        self.assertIn('USAGE_MONITOR_EXTRA_USED', env)
        self.assertNotIn('USAGE_MONITOR_EXTRA_LIMIT', env)

    @patch('usage_monitor_for_claude.app.ON_STARTUP_COMMAND', ['echo startup'])
    @patch('usage_monitor_for_claude.app.run_event_command')
    @patch('usage_monitor_for_claude.app.format_tooltip', return_value='tooltip')
    @patch('usage_monitor_for_claude.app.create_icon_image')
    def test_no_extra_usage_env_vars_when_disabled(self, _icon, _tooltip, mock_cmd):
        """Extra usage env vars are not emitted when extra_usage is disabled."""
        data = {
            'five_hour': {'utilization': 10.0},
            'extra_usage': {'is_enabled': False, 'used_credits': 0, 'monthly_limit': 0},
        }
        self.app.cache.update.return_value = UpdateResult(data=data)

        self.app.update()

        env = mock_cmd.call_args[0][1]
        self.assertNotIn('USAGE_MONITOR_EXTRA_USED', env)
        self.assertNotIn('USAGE_MONITOR_EXTRA_LIMIT', env)

    @patch('usage_monitor_for_claude.app.ON_STARTUP_COMMAND', ['echo startup'])
    @patch('usage_monitor_for_claude.app.run_event_command')
    @patch('usage_monitor_for_claude.app.format_tooltip', return_value='tooltip')
    @patch('usage_monitor_for_claude.app.create_icon_image')
    def test_handles_null_quota_field(self, _icon, _tooltip, mock_cmd):
        """Quota fields with value None (feature not enabled) are skipped without error."""
        data = {'five_hour': {'utilization': 10.0}, 'seven_day': None}
        self.app.cache.update.return_value = UpdateResult(data=data)

        self.app.update()

        mock_cmd.assert_called_once()
        env = mock_cmd.call_args[0][1]
        self.assertIn('USAGE_MONITOR_UTILIZATION_FIVE_HOUR', env)
        self.assertNotIn('USAGE_MONITOR_UTILIZATION_SEVEN_DAY', env)

    @patch('usage_monitor_for_claude.app.ON_STARTUP_COMMAND', ['echo startup'])
    @patch('usage_monitor_for_claude.app.run_event_command')
    def test_test_menu_handler_passes_expected_env(self, mock_cmd):
        """on_test_startup passes the documented env vars."""
        self.app.on_test_startup()

        mock_cmd.assert_called_once()
        cmd, env = mock_cmd.call_args[0]
        self.assertEqual(cmd, ['echo startup'])
        self.assertEqual(env['USAGE_MONITOR_EVENT'], 'startup')
        self.assertEqual(env['USAGE_MONITOR_RESETS_AT_FIVE_HOUR'], '')
        self.assertEqual(env['USAGE_MONITOR_UTILIZATION_FIVE_HOUR'], '0')
        self.assertNotEqual(env['USAGE_MONITOR_RESETS_AT_SEVEN_DAY'], '')


# ---------------------------------------------------------------------------
# Double-click command
# ---------------------------------------------------------------------------

class TestDoubleClickCommand(unittest.TestCase):
    """Tests for quick-action execution and its env snapshot."""

    def setUp(self):
        self.app = _make_app()

    def tearDown(self):
        _cleanup(self.app)

    @patch('usage_monitor_for_claude.app.QUICK_ACTION_COMMAND', ['run.exe'])
    @patch('usage_monitor_for_claude.app.run_event_command')
    def test_fires_with_current_quota_snapshot(self, mock_cmd):
        """Double-click command fires with env vars from the latest response."""
        self.app._last_response = {
            'five_hour': {'utilization': 30.0, 'resets_at': '2025-01-15T18:00:00Z'},
            'seven_day': {'utilization': 55.0, 'resets_at': '2025-01-20T12:00:00Z'},
        }

        self.app._run_quick_action()

        mock_cmd.assert_called_once()
        cmd, env = mock_cmd.call_args[0]
        self.assertEqual(cmd, ['run.exe'])
        self.assertEqual(env['USAGE_MONITOR_EVENT'], 'quick_action')
        self.assertEqual(env['USAGE_MONITOR_UTILIZATION_FIVE_HOUR'], '30')
        self.assertEqual(env['USAGE_MONITOR_RESETS_AT_FIVE_HOUR'], '2025-01-15T18:00:00Z')
        self.assertEqual(env['USAGE_MONITOR_UTILIZATION_SEVEN_DAY'], '55')

    @patch('usage_monitor_for_claude.app.QUICK_ACTION_COMMAND', ['run.exe'])
    @patch('usage_monitor_for_claude.app.run_event_command')
    def test_captures_output_so_failures_surface(self, mock_cmd):
        """A double-click is user-driven, so it requests output capture (error dialog on failure)."""
        self.app._last_response = {'five_hour': {'utilization': 10.0}}

        self.app._run_quick_action()

        self.assertTrue(mock_cmd.call_args[1].get('capture_output'))

    @patch('usage_monitor_for_claude.app.QUICK_ACTION_COMMAND', ['run.exe'])
    @patch('usage_monitor_for_claude.app.run_event_command')
    def test_only_startup_failures_are_reported(self, mock_cmd):
        """A late non-zero exit (crash, kill, replaced instance) must not raise a dialog long after the click."""
        self.app._last_response = {'five_hour': {'utilization': 10.0}}

        self.app._run_quick_action()

        self.assertFalse(mock_cmd.call_args[1].get('report_late_failures', True))

    @patch('usage_monitor_for_claude.app.QUICK_ACTION_COMMAND', ['run.exe'])
    @patch('usage_monitor_for_claude.app.run_event_command')
    def test_test_menu_handler_reports_late_failures(self, mock_cmd):
        """The 'Test event commands' menu keeps full reporting - there the exit code is the point."""
        self.app.on_test_quick_action()

        self.assertTrue(mock_cmd.call_args[1].get('report_late_failures', True))

    @patch('usage_monitor_for_claude.app.QUICK_ACTION_COMMAND', [])
    @patch('usage_monitor_for_claude.app.run_event_command')
    def test_no_fire_when_command_unset(self, mock_cmd):
        """No command runs when QUICK_ACTION_COMMAND is empty."""
        self.app._last_response = {'five_hour': {'utilization': 30.0}}

        self.app._run_quick_action()

        mock_cmd.assert_not_called()

    @patch('usage_monitor_for_claude.app.QUICK_ACTION_COMMAND', ['run.exe'])
    @patch('usage_monitor_for_claude.app.run_event_command')
    def test_empty_response_emits_only_event(self, mock_cmd):
        """Double-clicking before any data yields only the event var."""
        self.app._last_response = {}

        self.app._run_quick_action()

        env = mock_cmd.call_args[0][1]
        self.assertEqual(env['USAGE_MONITOR_EVENT'], 'quick_action')
        self.assertNotIn('USAGE_MONITOR_UTILIZATION_FIVE_HOUR', env)

    @patch('usage_monitor_for_claude.app.QUICK_ACTION_COMMAND', ['run.exe'])
    @patch('usage_monitor_for_claude.app.run_event_command')
    def test_error_response_emits_only_event(self, mock_cmd):
        """An error response contributes no quota vars."""
        self.app._last_response = {'error': 'server down', 'auth_error': True}

        self.app._run_quick_action()

        env = mock_cmd.call_args[0][1]
        self.assertEqual(env['USAGE_MONITOR_EVENT'], 'quick_action')
        self.assertFalse([k for k in env if k.startswith('USAGE_MONITOR_UTILIZATION')])

    @patch('usage_monitor_for_claude.app.QUICK_ACTION_COMMAND', ['run.exe'])
    @patch('usage_monitor_for_claude.app.run_event_command')
    def test_extra_usage_env_vars_when_enabled(self, mock_cmd):
        """Extra usage credit vars are included when enabled."""
        self.app._last_response = {
            'five_hour': {'utilization': 10.0},
            'extra_usage': {'is_enabled': True, 'used_credits': 8.20, 'monthly_limit': 10.0},
        }

        self.app._run_quick_action()

        env = mock_cmd.call_args[0][1]
        self.assertIn('USAGE_MONITOR_EXTRA_USED', env)
        self.assertIn('USAGE_MONITOR_EXTRA_LIMIT', env)
        self.assertNotIn('USAGE_MONITOR_UTILIZATION_EXTRA_USAGE', env)

    @patch('usage_monitor_for_claude.app.QUICK_ACTION_COMMAND', ['run.exe'])
    @patch('usage_monitor_for_claude.app.run_event_command')
    def test_test_menu_handler_passes_expected_env(self, mock_cmd):
        """on_test_quick_action passes the documented sample env vars."""
        self.app.on_test_quick_action()

        mock_cmd.assert_called_once()
        cmd, env = mock_cmd.call_args[0]
        self.assertEqual(cmd, ['run.exe'])
        self.assertEqual(env['USAGE_MONITOR_EVENT'], 'quick_action')
        self.assertEqual(env['USAGE_MONITOR_UTILIZATION_FIVE_HOUR'], '30')
        self.assertEqual(env['USAGE_MONITOR_UTILIZATION_SEVEN_DAY'], '55')
        self.assertNotEqual(env['USAGE_MONITOR_RESETS_AT_FIVE_HOUR'], '')


# ---------------------------------------------------------------------------
# Tray double-click wiring
# ---------------------------------------------------------------------------

class TestAutostartWiring(unittest.TestCase):
    """Tests for offering and maintaining the autostart entry."""

    def _ready(self, supported):
        """Run the tray-ready hook with autostart support forced either way."""
        app = _make_app()
        self.addCleanup(_cleanup, app)

        icon = MagicMock()
        with patch('usage_monitor_for_claude.app.autostart_supported', return_value=supported), \
             patch('usage_monitor_for_claude.app.sync_autostart_path') as mock_sync, \
             patch('usage_monitor_for_claude.app.api_headers', return_value={'x': 'y'}), \
             patch('usage_monitor_for_claude.app.threading.Thread'), \
             patch.object(app, 'poll_loop'):
            app._on_icon_ready(icon)

        return mock_sync

    def test_moved_installation_is_synced_where_autostart_works(self):
        """A stale entry - moved executable, recreated environment - is rewritten on start."""
        self._ready(supported=True).assert_called_once()

    def test_no_sync_where_autostart_is_unavailable(self):
        """Without a usable autostart entry there is nothing to keep in sync."""
        self._ready(supported=False).assert_not_called()


class TestDoubleClickWiring(unittest.TestCase):
    """Tests for wiring the optional double-click command to the tray icon."""

    def _build(self, command, installed):
        """Construct an app with the given configuration.

        The settings patch stays active for the whole test: the menu
        resolves its visibility when it opens, not when it is built.
        """
        command_patch = patch('usage_monitor_for_claude.app.QUICK_ACTION_COMMAND', command)
        command_patch.start()
        self.addCleanup(command_patch.stop)

        with patch('usage_monitor_for_claude.app.pystray'), \
             patch('usage_monitor_for_claude.app.create_icon_image'), \
             patch('usage_monitor_for_claude.app.taskbar_uses_light_theme', return_value=False), \
             patch('usage_monitor_for_claude.app.install_tray_click_handler', return_value=installed) as mock_install, \
             patch('builtins.print') as mock_print:
            app = UsageMonitorForClaude()

        return app, mock_install, mock_print

    def test_no_command_leaves_default_click_untouched(self):
        """Without a configured command the platform's own single click must stay as it is."""
        app, mock_install, _print = self._build(command=[], installed=True)

        mock_install.assert_not_called()
        self.assertFalse(app.double_click_installed)

    def test_command_wires_the_handler(self):
        """A configured command installs the handler and records that it worked."""
        app, mock_install, mock_print = self._build(command=['notepad'], installed=True)

        mock_install.assert_called_once()
        self.assertTrue(app.double_click_installed)
        mock_print.assert_not_called()

    def test_unsupported_platform_is_reported(self):
        """A command that can never fire must not fail silently."""
        app, _install, mock_print = self._build(command=['xdg-open .'], installed=False)

        self.assertFalse(app.double_click_installed)
        mock_print.assert_called_once()
        self.assertIn('double-click', mock_print.call_args[0][0])

    def test_menu_entry_hidden_when_double_click_works(self):
        """A working double-click makes the menu entry redundant."""
        app, _install, _print = self._build(command=['notepad'], installed=True)

        self.assertFalse(app._quick_action_menu_visible())

    def test_menu_entry_shown_when_double_click_unavailable(self):
        """The command stays reachable where the tray cannot report a double-click."""
        app, _install, _print = self._build(command=['xdg-open .'], installed=False)

        self.assertTrue(app._quick_action_menu_visible())

    def test_menu_entry_hidden_without_a_command(self):
        """Nothing configured means nothing to offer."""
        app, _install, _print = self._build(command=[], installed=False)

        self.assertFalse(app._quick_action_menu_visible())

    def test_menu_entry_runs_the_configured_command(self):
        """The menu entry runs the same command a double-click would."""
        app, _install, _print = self._build(command=['xdg-open .'], installed=False)

        with patch.object(app, '_run_quick_action') as mock_run:
            app.on_run_quick_action()

        mock_run.assert_called_once()

    def test_handler_receives_both_actions(self):
        """The single click opens the popup, the double click runs the command."""
        app, mock_install, _print = self._build(command=['notepad'], installed=True)

        _icon, on_single, on_double = mock_install.call_args[0]
        self.assertEqual(on_single, app.on_show_popup)
        self.assertEqual(on_double, app._run_quick_action)


class TestDualTrayIcons(unittest.TestCase):
    """Tests for the Windows-only secondary Codex tray icon."""

    class _FakeIcon:
        def __init__(self, name, icon=None, title='', menu=None):
            self.name = name
            self.icon = icon
            self.title = title
            self.menu = menu
            self.run_calls = 0
            self.stop_calls = 0

        def run(self, setup=None):
            self.run_calls += 1
            if setup:
                setup(self)

        def stop(self):
            self.stop_calls += 1

    def _build(self):
        fake_pystray = MagicMock()
        fake_pystray.Icon.side_effect = self._FakeIcon
        fake_pystray.Menu.SEPARATOR = object()
        with patch('usage_monitor_for_claude.app.pystray', fake_pystray), \
             patch('usage_monitor_for_claude.app.dual_tray_supported', return_value=True), \
             patch('usage_monitor_for_claude.app.create_icon_image'), \
             patch('usage_monitor_for_claude.app.create_codex_icon_image'):
            app = UsageMonitorForClaude()
        return app, fake_pystray

    def test_constructs_distinct_claude_and_codex_icons(self):
        """Windows creates separate named icons while retaining the Claude icon as ``icon``."""
        app, fake_pystray = self._build()

        self.assertEqual(app.icon.name, 'usage_monitor')
        # The constructor calls the factory twice; names identify each shell icon.
        names = [call.args[0] for call in fake_pystray.Icon.call_args_list]
        self.assertEqual(names, ['usage_monitor', 'usage_monitor_codex'])
        self.assertIsNotNone(app.codex_icon)
        self.assertNotEqual(app.icon, app.codex_icon)
        self.assertTrue(app.icon.title.startswith('Claude Usage'))
        self.assertEqual(app.codex_icon.title, 'Codex Usage')

    @patch('usage_monitor_for_claude.app.format_codex_tooltip', return_value='Codex Usage\n5h: 42%\n7d: 17%')
    @patch('usage_monitor_for_claude.app.elapsed_pct', return_value=None)
    @patch('usage_monitor_for_claude.app.create_codex_icon_image')
    def test_codex_icon_renders_snapshot_usage(self, mock_image, _elapsed, _tooltip):
        """The Codex icon reads both percentages from the shared cache snapshot."""
        app, _fake_pystray = self._build()
        app.cache._codex_usage = {
            'five_hour': {'utilization': 42.0},
            'seven_day': {'utilization': 17.0},
        }

        app._render_codex_tray()

        mock_image.assert_called_once_with(42.0, 17.0, False, time_pct_top=None, time_pct_bottom=None)
        self.assertEqual(app.codex_icon.title, 'Codex Usage\n5h: 42%\n7d: 17%')

    def test_quit_stops_both_icons(self):
        """Quitting from either menu stops both tray loops."""
        app, _fake_pystray = self._build()

        app.on_quit()

        self.assertEqual(app.icon.stop_calls, 1)
        self.assertEqual(app.codex_icon.stop_calls, 1)
        self.assertFalse(app.running)

    def test_run_starts_codex_loop_without_serially_blocking(self):
        """The Codex loop starts in a thread before the Claude loop is run."""
        app, _fake_pystray = self._build()

        with patch('usage_monitor_for_claude.app.threading.Thread') as mock_thread, \
             patch.object(app, '_on_icon_ready'):
            app.run()

        mock_thread.assert_called_once_with(target=app._run_codex_icon, daemon=True)
        mock_thread.return_value.start.assert_called_once()
        self.assertEqual(app.icon.run_calls, 1)


if __name__ == '__main__':
    unittest.main()

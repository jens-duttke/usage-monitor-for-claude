"""
Forecast Tests
===============

Unit tests for record_samples() and usage_forecast().
"""
from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from usage_monitor_for_claude.forecast import MAX_SAMPLES, record_samples, usage_forecast
from usage_monitor_for_claude.formatting import _format_clock
from usage_monitor_for_claude.i18n import T
from usage_monitor_for_claude.settings import TIME_FORMAT

RESET = datetime(2026, 11, 5, 19, 0, tzinfo=timezone.utc)
START = RESET - timedelta(days=7)
RESET_ISO = RESET.isoformat()


def _weekly(pct: float, resets_at: str = RESET_ISO) -> dict:
    return {'utilization': pct, 'resets_at': resets_at}


def _at(days: float) -> datetime:
    """Instant *days* into the weekly window."""
    return START + timedelta(days=days)


# ---------------------------------------------------------------------------
# record_samples
# ---------------------------------------------------------------------------

class TestRecordSamples(unittest.TestCase):
    """Tests for record_samples()."""

    def test_first_reading_starts_log(self):
        """A quota seen for the first time gets a one-reading log."""
        history = record_samples({}, {'seven_day': _weekly(10.0)}, 100.0)
        self.assertEqual(history, {'seven_day': {'resets_at': RESET_ISO, 'samples': ((100.0, 10.0),)}})

    def test_rising_readings_append(self):
        """Readings of the same window are appended in order."""
        history = record_samples({}, {'seven_day': _weekly(10.0)}, 100.0)
        history = record_samples(history, {'seven_day': _weekly(12.0)}, 200.0)
        self.assertEqual(history['seven_day']['samples'], ((100.0, 10.0), (200.0, 12.0)))

    def test_flat_run_keeps_only_its_ends(self):
        """Unchanged readings move the end of the flat run instead of piling up."""
        history = {}
        for timestamp in (100.0, 200.0, 300.0, 400.0):
            history = record_samples(history, {'seven_day': _weekly(10.0)}, timestamp)
        self.assertEqual(history['seven_day']['samples'], ((100.0, 10.0), (400.0, 10.0)))

    def test_flat_run_after_rise_keeps_rise(self):
        """The reading where usage changed stays, only the flat tail collapses."""
        history = {}
        for timestamp, pct in ((100.0, 5.0), (200.0, 10.0), (300.0, 10.0), (400.0, 10.0)):
            history = record_samples(history, {'seven_day': _weekly(pct)}, timestamp)
        self.assertEqual(history['seven_day']['samples'], ((100.0, 5.0), (200.0, 10.0), (400.0, 10.0)))

    def test_drop_starts_over(self):
        """A lower utilization (a reset or another account) starts a new log."""
        history = record_samples({}, {'seven_day': _weekly(80.0)}, 100.0)
        history = record_samples(history, {'seven_day': _weekly(3.0)}, 200.0)
        self.assertEqual(history['seven_day']['samples'], ((200.0, 3.0),))

    def test_moved_reset_starts_over(self):
        """A reset time more than an hour away from the logged one is a new window."""
        history = record_samples({}, {'seven_day': _weekly(10.0)}, 100.0)
        moved = (RESET + timedelta(days=7)).isoformat()
        history = record_samples(history, {'seven_day': _weekly(20.0, moved)}, 200.0)
        self.assertEqual(history['seven_day'], {'resets_at': moved, 'samples': ((200.0, 20.0),)})

    def test_reset_jitter_keeps_log(self):
        """Sub-second differences in the reported reset time are the same window."""
        history = record_samples({}, {'seven_day': _weekly(10.0, '2026-11-05T18:59:59.738715+00:00')}, 100.0)
        history = record_samples(history, {'seven_day': _weekly(12.0, '2026-11-05T19:00:00+00:00')}, 200.0)
        self.assertEqual(len(history['seven_day']['samples']), 2)

    def test_skips_entries_without_reset_window(self):
        """Null fields, quotas without a reset window and extra_usage are not logged."""
        usage = {
            'five_hour': None,
            'seven_day_sonnet': {'utilization': 0.0, 'resets_at': None},
            'extra_usage': {'utilization': 12.0, 'resets_at': RESET_ISO},
            'seven_day': _weekly(10.0),
        }
        self.assertEqual(list(record_samples({}, usage, 100.0)), ['seven_day'])

    def test_quota_gone_from_response_is_dropped(self):
        """A quota no longer reported loses its log."""
        history = record_samples({}, {'seven_day': _weekly(10.0)}, 100.0)
        self.assertEqual(record_samples(history, {'five_hour': None}, 200.0), {})

    def test_input_history_not_modified(self):
        """The previous history is left untouched, so existing snapshots stay valid."""
        history = record_samples({}, {'seven_day': _weekly(10.0)}, 100.0)
        before = dict(history)
        record_samples(history, {'seven_day': _weekly(20.0)}, 200.0)
        self.assertEqual(history, before)

    def test_log_is_capped(self):
        """The oldest readings are dropped beyond MAX_SAMPLES."""
        history = {}
        for index in range(MAX_SAMPLES + 5):
            history = record_samples(history, {'seven_day': _weekly(float(index))}, float(index))
        samples = history['seven_day']['samples']
        self.assertEqual(len(samples), MAX_SAMPLES)
        self.assertEqual(samples[-1], (float(MAX_SAMPLES + 4), float(MAX_SAMPLES + 4)))


# ---------------------------------------------------------------------------
# usage_forecast
# ---------------------------------------------------------------------------

class TestUsageForecast(unittest.TestCase):
    """Tests for usage_forecast()."""

    def test_runs_out_before_reset(self):
        """50% after a quarter of the window runs out halfway, 3.5 days before the reset."""
        now = _at(1.75)
        forecast = usage_forecast('seven_day', {'seven_day': _weekly(50.0)}, {}, now)

        self.assertTrue(forecast['runs_out'])
        end_x, end_y = forecast['projection'][-1]
        self.assertAlmostEqual(end_x, 0.5, places=4)
        self.assertAlmostEqual(end_y, 100.0, places=2)
        run_out = _at(3.5).astimezone()
        expected = T['forecast_runs_out'].format(
            day=T['weekdays'][run_out.weekday()], clock=_format_clock(run_out, TIME_FORMAT == '24h'),
            lead=T['duration_dh'].format(d=3, h=12),
        )
        self.assertEqual(forecast['summary'], expected)

    def test_stays_below_limit(self):
        """20% at half the window projects 40% at the reset."""
        forecast = usage_forecast('seven_day', {'seven_day': _weekly(20.0)}, {}, _at(3.5))

        self.assertFalse(forecast['runs_out'])
        self.assertEqual(forecast['summary'], T['forecast_at_reset'].format(pct='40'))
        end_x, end_y = forecast['projection'][-1]
        self.assertAlmostEqual(end_x, 1.0)
        self.assertAlmostEqual(end_y, 40.0)

    def test_projection_starts_at_current_reading(self):
        """The dashed line continues from the current point."""
        forecast = usage_forecast('seven_day', {'seven_day': _weekly(20.0)}, {}, _at(3.5))
        self.assertEqual(forecast['projection'][0], [0.5, 20.0])
        self.assertEqual(forecast['now_x'], 0.5)

    def test_exhausted_quota_runs_out_now(self):
        """A quota already at 100% is reported as run out, with the time left to the reset."""
        forecast = usage_forecast('seven_day', {'seven_day': _weekly(100.0)}, {}, _at(6.0))
        self.assertTrue(forecast['runs_out'])
        self.assertIn(T['duration_dh'].format(d=1, h=0), forecast['summary'])

    def test_too_early_has_no_projection(self):
        """Right after a reset the pace is not projected."""
        forecast = usage_forecast('seven_day', {'seven_day': _weekly(5.0)}, {}, _at(0.1))
        self.assertEqual(forecast['projection'], [])
        self.assertEqual(forecast['summary'], '')
        self.assertFalse(forecast['runs_out'])

    def test_zero_usage_projects_zero(self):
        """No usage so far projects 0% at the reset."""
        forecast = usage_forecast('seven_day', {'seven_day': _weekly(0.0)}, {}, _at(3.5))
        self.assertEqual(forecast['summary'], T['forecast_at_reset'].format(pct='0'))

    def test_actual_line_uses_logged_readings(self):
        """Readings of this window become the measured line after the 0% start."""
        history = {'seven_day': {'resets_at': RESET_ISO, 'samples': (
            (_at(1.0).timestamp(), 10.0),
            (_at(2.0).timestamp(), 30.0),
            (_at(3.5).timestamp(), 40.0),
        )}}
        forecast = usage_forecast('seven_day', {'seven_day': _weekly(40.0)}, history, _at(3.5))

        expected = [[0.0, 0.0], [1 / 7, 10.0], [2 / 7, 30.0], [0.5, 40.0]]
        self.assertEqual(len(forecast['actual']), len(expected))
        for point, (x, y) in zip(forecast['actual'], expected):
            self.assertAlmostEqual(point[0], x)
            self.assertAlmostEqual(point[1], y)
        self.assertAlmostEqual(forecast['measured_from'], 1 / 7)

    def test_actual_line_without_log(self):
        """Without readings the line runs from the window start to the current reading."""
        forecast = usage_forecast('seven_day', {'seven_day': _weekly(40.0)}, {}, _at(3.5))
        self.assertEqual(forecast['actual'], [[0.0, 0.0], [0.5, 40.0]])
        self.assertEqual(forecast['measured_from'], 0.5)

    def test_log_of_other_window_ignored(self):
        """Readings logged for a previous window are not drawn."""
        previous = (RESET - timedelta(days=7)).isoformat()
        history = {'seven_day': {'resets_at': previous, 'samples': ((_at(-1.0).timestamp(), 90.0),)}}
        forecast = usage_forecast('seven_day', {'seven_day': _weekly(40.0)}, history, _at(3.5))
        self.assertEqual(forecast['actual'], [[0.0, 0.0], [0.5, 40.0]])

    def test_flat_off_hours_in_projection(self):
        """With working-time pacing the projection stays flat while no working time passes."""
        def scheduled_fraction(at):
            # Working time only during the first and last third of the window.
            days = (at - START).total_seconds() / 86400
            return min(days, 7 / 3) / 7 * 1.5 + max(0.0, days - 14 / 3) / 7 * 1.5

        with patch('usage_monitor_for_claude.forecast.elapsed_fraction', return_value=scheduled_fraction):
            forecast = usage_forecast('seven_day', {'seven_day': _weekly(25.0)}, {}, _at(7 / 3))

        middle = [y for x, y in forecast['projection'] if 1 / 3 < x < 2 / 3]
        self.assertTrue(middle)
        self.assertTrue(all(abs(y - 25.0) < 1e-9 for y in middle))
        self.assertAlmostEqual(forecast['projection'][-1][1], 50.0)

    def test_run_out_lands_at_end_of_working_time(self):
        """A quota that runs out on a scheduled pace runs out while working time passes."""
        def scheduled_fraction(at):
            days = (at - START).total_seconds() / 86400
            return min(days, 7 / 3) / 7 * 1.5 + max(0.0, days - 14 / 3) / 7 * 1.5

        with patch('usage_monitor_for_claude.forecast.elapsed_fraction', return_value=scheduled_fraction):
            forecast = usage_forecast('seven_day', {'seven_day': _weekly(60.0)}, {}, _at(7 / 3))

        # 60% at half the working time reaches 100% at five sixths of it,
        # which falls in the last third of the window (day 6 2/9).
        self.assertTrue(forecast['runs_out'])
        end_x = forecast['projection'][-1][0]
        self.assertAlmostEqual(end_x, (14 / 3 + 14 / 9) / 7, places=4)

    def test_weekly_ticks_labelled_with_weekdays(self):
        """A weekly window gets a labelled tick at every local midnight inside it."""
        forecast = usage_forecast('seven_day', {'seven_day': _weekly(20.0)}, {}, _at(3.5))
        self.assertIn(len(forecast['ticks']), (6, 7, 8))
        self.assertTrue(all(tick['label'] in T['weekdays'] for tick in forecast['ticks']))

    def test_session_ticks_unlabelled(self):
        """A five-hour window gets hourly ticks without labels."""
        reset = datetime(2026, 11, 1, 15, 0, tzinfo=timezone.utc)
        usage = {'five_hour': {'utilization': 30.0, 'resets_at': reset.isoformat()}}
        forecast = usage_forecast('five_hour', usage, {}, reset - timedelta(hours=2))
        self.assertEqual([tick['label'] for tick in forecast['ticks']], ['', '', '', ''])

    def test_label_from_field_name(self):
        forecast = usage_forecast('seven_day', {'seven_day': _weekly(20.0)}, {}, _at(3.5))
        self.assertEqual(forecast['key'], 'seven_day')
        self.assertTrue(forecast['label'])

    def test_none_without_period(self):
        """A code-named quota has no known period to forecast over."""
        self.assertIsNone(usage_forecast('iguana_necktie', {'iguana_necktie': _weekly(20.0)}, {}, _at(3.5)))

    def test_none_for_null_or_missing_field(self):
        self.assertIsNone(usage_forecast('five_hour', {'five_hour': None}, {}, _at(3.5)))
        self.assertIsNone(usage_forecast('seven_day', {}, {}, _at(3.5)))

    def test_none_without_reset_window(self):
        """A quota between windows (no reset time) has nothing to project."""
        self.assertIsNone(usage_forecast('seven_day', {'seven_day': {'utilization': 0.0, 'resets_at': None}}, {}, _at(3.5)))

    def test_none_outside_window(self):
        """A stale reset time in the past is not forecast."""
        self.assertIsNone(usage_forecast('seven_day', {'seven_day': _weekly(20.0)}, {}, RESET + timedelta(minutes=1)))

    def test_none_for_unparseable_reset(self):
        self.assertIsNone(usage_forecast('seven_day', {'seven_day': _weekly(20.0, 'not a date')}, {}, _at(3.5)))


if __name__ == '__main__':
    unittest.main()

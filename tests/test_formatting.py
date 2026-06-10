"""
Formatting Tests
=================

Unit tests for parse_field_name(), tooltip_label(), elapsed_pct(),
time_until(), format_tooltip(), and format_credits().
"""
from __future__ import annotations

import json
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

from usage_monitor_for_claude.formatting import (
    PERIOD_5H, PERIOD_7D,
    divider_positions, elapsed_pct, expand_popup_fields, field_period, format_credits,
    format_tooltip, parse_field_name, popup_label, time_until, tooltip_label,
)
from usage_monitor_for_claude.i18n import LOCALE_DIR

EN = json.loads((LOCALE_DIR / 'en.json').read_text(encoding='utf-8'))


# ---------------------------------------------------------------------------
# parse_field_name
# ---------------------------------------------------------------------------

class TestParseFieldName(unittest.TestCase):
    """Tests for parse_field_name()."""

    def test_five_hour(self):
        self.assertEqual(parse_field_name('five_hour'), (5, 'hour', None))

    def test_seven_day(self):
        self.assertEqual(parse_field_name('seven_day'), (7, 'day', None))

    def test_seven_day_sonnet(self):
        self.assertEqual(parse_field_name('seven_day_sonnet'), (7, 'day', 'sonnet'))

    def test_seven_day_opus(self):
        self.assertEqual(parse_field_name('seven_day_opus'), (7, 'day', 'opus'))

    def test_three_day_cowork(self):
        self.assertEqual(parse_field_name('three_day_cowork'), (3, 'day', 'cowork'))

    def test_variant_with_underscores(self):
        """Multi-word variant is preserved as a single string."""
        self.assertEqual(parse_field_name('seven_day_oauth_apps'), (7, 'day', 'oauth_apps'))

    def test_one_hour(self):
        self.assertEqual(parse_field_name('one_hour'), (1, 'hour', None))

    def test_twelve_day(self):
        self.assertEqual(parse_field_name('twelve_day'), (12, 'day', None))

    def test_unknown_number_word(self):
        """Unrecognized number word returns None."""
        self.assertIsNone(parse_field_name('iguana_day'))

    def test_unknown_unit(self):
        """Unrecognized unit returns None."""
        self.assertIsNone(parse_field_name('one_year_cowork'))

    def test_no_underscore(self):
        """Word without underscore returns None."""
        self.assertIsNone(parse_field_name('foobar'))

    def test_unknown_number_and_unit(self):
        """Both number word and unit unrecognized returns None."""
        self.assertIsNone(parse_field_name('extra_usage'))

    def test_empty_string(self):
        self.assertIsNone(parse_field_name(''))


# ---------------------------------------------------------------------------
# tooltip_label
# ---------------------------------------------------------------------------

class TestTooltipLabel(unittest.TestCase):
    """Tests for tooltip_label()."""

    def test_five_hour(self):
        self.assertEqual(tooltip_label('five_hour'), '5h')

    def test_seven_day(self):
        self.assertEqual(tooltip_label('seven_day'), '7d')

    def test_seven_day_sonnet(self):
        self.assertEqual(tooltip_label('seven_day_sonnet'), '7d Sonnet')

    def test_seven_day_opus(self):
        self.assertEqual(tooltip_label('seven_day_opus'), '7d Opus')

    def test_three_day_cowork(self):
        self.assertEqual(tooltip_label('three_day_cowork'), '3d Cowork')

    def test_five_hour_something(self):
        self.assertEqual(tooltip_label('five_hour_something'), '5h Something')

    def test_multi_word_variant(self):
        self.assertEqual(tooltip_label('seven_day_oauth_apps'), '7d OAuth Apps')

    def test_unknown_number_fallback(self):
        """Unrecognized number word falls back to title case."""
        self.assertEqual(tooltip_label('iguana_necktie'), 'Iguana Necktie')

    def test_unknown_unit_fallback(self):
        """Unrecognized unit falls back to title case."""
        self.assertEqual(tooltip_label('one_year_cowork'), 'One Year Cowork')

    def test_no_underscore_fallback(self):
        """Word without underscore falls back to title case."""
        self.assertEqual(tooltip_label('foobar'), 'Foobar')

    def test_unknown_number_and_unit_fallback(self):
        self.assertEqual(tooltip_label('extra_usage'), 'Extra Usage')

    def test_abbreviation_oauth(self):
        """oauth is title-cased as OAuth."""
        self.assertEqual(tooltip_label('seven_day_oauth'), '7d OAuth')

    def test_abbreviation_api(self):
        """api is title-cased as API."""
        self.assertEqual(tooltip_label('seven_day_api'), '7d API')


# ---------------------------------------------------------------------------
# popup_label
# ---------------------------------------------------------------------------

@patch('usage_monitor_for_claude.formatting.T', EN)
class TestPopupLabel(unittest.TestCase):
    """Tests for popup_label()."""

    def test_five_hour(self):
        self.assertEqual(popup_label('five_hour'), 'Session (5hr)')

    def test_seven_day(self):
        self.assertEqual(popup_label('seven_day'), 'Weekly (7 day)')

    def test_seven_day_sonnet(self):
        self.assertEqual(popup_label('seven_day_sonnet'), 'Weekly (Sonnet)')

    def test_seven_day_opus(self):
        self.assertEqual(popup_label('seven_day_opus'), 'Weekly (Opus)')

    def test_seven_day_cowork(self):
        self.assertEqual(popup_label('seven_day_cowork'), 'Weekly (Cowork)')

    def test_seven_day_oauth_apps(self):
        self.assertEqual(popup_label('seven_day_oauth_apps'), 'Weekly (OAuth Apps)')

    def test_three_day_foo(self):
        self.assertEqual(popup_label('three_day_foo'), 'Weekly (Foo)')

    def test_unknown_fallback(self):
        self.assertEqual(popup_label('extra_usage'), 'Extra Usage')

    def test_unknown_with_abbreviation(self):
        self.assertEqual(popup_label('some_api_thing'), 'Some API Thing')


# ---------------------------------------------------------------------------
# field_period
# ---------------------------------------------------------------------------

class TestFieldPeriod(unittest.TestCase):
    """Tests for field_period()."""

    def test_five_hour(self):
        self.assertEqual(field_period('five_hour'), 5 * 3600)

    def test_seven_day(self):
        self.assertEqual(field_period('seven_day'), 7 * 24 * 3600)

    def test_seven_day_sonnet(self):
        self.assertEqual(field_period('seven_day_sonnet'), 7 * 24 * 3600)

    def test_three_day(self):
        self.assertEqual(field_period('three_day'), 3 * 24 * 3600)

    def test_unknown_returns_none(self):
        self.assertIsNone(field_period('extra_usage'))

    def test_unknown_unit_returns_none(self):
        self.assertIsNone(field_period('one_year'))


# ---------------------------------------------------------------------------
# expand_popup_fields
# ---------------------------------------------------------------------------

class TestExpandPopupFields(unittest.TestCase):
    """Tests for expand_popup_fields()."""

    def _usage(self, **kwargs):
        """Build a usage dict with given field names as active quota fields."""
        return {k: {'utilization': v, 'resets_at': ''} for k, v in kwargs.items()}

    def test_wildcard_only(self):
        """Wildcard returns all fields in default order."""
        usage = self._usage(seven_day=20, five_hour=10, seven_day_sonnet=30)
        result = expand_popup_fields(['*'], usage)
        self.assertEqual(result, ['five_hour', 'seven_day', 'seven_day_sonnet'])

    def test_explicit_fields(self):
        """Explicit field names shown in listed order."""
        usage = self._usage(five_hour=10, seven_day=20, seven_day_sonnet=30)
        result = expand_popup_fields(['seven_day_sonnet', 'five_hour'], usage)
        self.assertEqual(result, ['seven_day_sonnet', 'five_hour'])

    def test_wildcard_after_explicit(self):
        """Wildcard fills in remaining fields after explicit ones."""
        usage = self._usage(five_hour=10, seven_day=20, seven_day_sonnet=30)
        result = expand_popup_fields(['seven_day_sonnet', '*'], usage)
        self.assertEqual(result, ['seven_day_sonnet', 'five_hour', 'seven_day'])

    def test_null_fields_skipped(self):
        """Fields with None utilization are skipped."""
        usage = {'five_hour': {'utilization': 10, 'resets_at': ''}, 'seven_day': {'utilization': None, 'resets_at': ''}}
        result = expand_popup_fields(['*'], usage)
        self.assertEqual(result, ['five_hour'])

    def test_missing_fields_skipped(self):
        """Explicitly listed fields missing from API are skipped."""
        usage = self._usage(five_hour=10)
        result = expand_popup_fields(['five_hour', 'seven_day_sonnet'], usage)
        self.assertEqual(result, ['five_hour'])

    def test_duplicates_removed(self):
        """Explicit field followed by wildcard does not duplicate."""
        usage = self._usage(five_hour=10, seven_day=20)
        result = expand_popup_fields(['five_hour', '*'], usage)
        self.assertEqual(result, ['five_hour', 'seven_day'])

    def test_empty_setting(self):
        """Empty field list returns nothing."""
        usage = self._usage(five_hour=10)
        result = expand_popup_fields([], usage)
        self.assertEqual(result, [])

    def test_default_order_hour_before_day(self):
        """Default order puts hour fields before day fields."""
        usage = self._usage(seven_day=20, five_hour=10)
        result = expand_popup_fields(['*'], usage)
        self.assertEqual(result[0], 'five_hour')
        self.assertEqual(result[1], 'seven_day')

    def test_default_order_base_before_variant(self):
        """Default order puts base field before variants."""
        usage = self._usage(seven_day_sonnet=30, seven_day=20)
        result = expand_popup_fields(['*'], usage)
        self.assertEqual(result, ['seven_day', 'seven_day_sonnet'])

    def test_default_order_variants_alphabetical(self):
        """Default order sorts variants alphabetically."""
        usage = self._usage(seven_day_opus=30, seven_day_cowork=20, seven_day_sonnet=10)
        result = expand_popup_fields(['*'], usage)
        self.assertEqual(result, ['seven_day_cowork', 'seven_day_opus', 'seven_day_sonnet'])

    def test_misspelled_field_skipped(self):
        """Misspelled field names are silently skipped."""
        usage = self._usage(five_hour=10, seven_day=20)
        result = expand_popup_fields(['fve_hour', 'seven_day'], usage)
        self.assertEqual(result, ['seven_day'])

    def test_non_dict_values_ignored(self):
        """Non-dict values in usage data (e.g. error strings) are ignored."""
        usage = {'error': 'Connection failed', 'five_hour': {'utilization': 42, 'resets_at': ''}}
        result = expand_popup_fields(['*'], usage)
        self.assertEqual(result, ['five_hour'])

    def test_extra_usage_excluded(self):
        """extra_usage is excluded (no resets_at key, different structure)."""
        usage = {
            'five_hour': {'utilization': 10, 'resets_at': ''},
            'extra_usage': {'is_enabled': True, 'monthly_limit': 1000, 'used_credits': 500, 'utilization': 50},
        }
        result = expand_popup_fields(['*'], usage)
        self.assertEqual(result, ['five_hour'])

    def test_field_with_utilization_none_skipped(self):
        """Fields where utilization is None are skipped even when explicitly listed."""
        usage = {'five_hour': {'utilization': None, 'resets_at': '2026-01-01T00:00:00Z'}}
        result = expand_popup_fields(['five_hour'], usage)
        self.assertEqual(result, [])

    def test_field_without_resets_at_skipped(self):
        """Fields without resets_at key are skipped (not a quota bar)."""
        usage = {'five_hour': {'utilization': 42}}
        result = expand_popup_fields(['*'], usage)
        self.assertEqual(result, [])

    def test_all_fields_null(self):
        """All quota fields null returns empty list."""
        usage = {'five_hour': None, 'seven_day': None, 'seven_day_sonnet': None}
        result = expand_popup_fields(['*'], usage)
        self.assertEqual(result, [])

    def test_wildcard_with_all_misspelled(self):
        """Wildcard with no matching fields returns empty list."""
        usage = self._usage(five_hour=10)
        result = expand_popup_fields(['typo_field', 'another_typo'], usage)
        self.assertEqual(result, [])

    def test_empty_usage_data(self):
        """Empty usage data returns empty list."""
        result = expand_popup_fields(['*'], {})
        self.assertEqual(result, [])

    def test_utilization_zero_included(self):
        """Fields with utilization 0 are included (0 is a valid value, not null)."""
        usage = {'five_hour': {'utilization': 0, 'resets_at': ''}}
        result = expand_popup_fields(['*'], usage)
        self.assertEqual(result, ['five_hour'])


# ---------------------------------------------------------------------------
# elapsed_pct
# ---------------------------------------------------------------------------

@patch('usage_monitor_for_claude.formatting.datetime')
class TestElapsedPct(unittest.TestCase):
    """Tests for elapsed_pct()."""

    def _setup(self, mock_dt, utc_now):
        mock_dt.now.return_value = utc_now
        mock_dt.fromisoformat.side_effect = datetime.fromisoformat

    def test_empty_resets_at(self, mock_dt):
        """Empty resets_at returns None."""
        self.assertIsNone(elapsed_pct('', PERIOD_5H))

    def test_zero_period(self, mock_dt):
        """period_seconds=0 returns None."""
        self.assertIsNone(elapsed_pct('2025-01-15T12:00:00+00:00', 0))

    def test_negative_period(self, mock_dt):
        """Negative period_seconds returns None."""
        self.assertIsNone(elapsed_pct('2025-01-15T12:00:00+00:00', -100))

    def test_invalid_iso_string(self, mock_dt):
        """Invalid ISO string returns None."""
        self._setup(mock_dt, datetime(2025, 1, 15, 12, 0, 0, tzinfo=timezone.utc))
        self.assertIsNone(elapsed_pct('not-a-date', PERIOD_5H))

    def test_naive_datetime_returns_none(self, mock_dt):
        """Timezone-naive ISO string causes subtraction error, returns None."""
        self._setup(mock_dt, datetime(2025, 1, 15, 12, 0, 0, tzinfo=timezone.utc))
        self.assertIsNone(elapsed_pct('2025-01-15T14:00:00', PERIOD_5H))

    def test_just_started_zero_percent(self, mock_dt):
        """Reset is exactly period_seconds away, 0% elapsed."""
        utc_now = datetime(2025, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
        self._setup(mock_dt, utc_now)
        reset = utc_now + timedelta(seconds=PERIOD_5H)
        result = elapsed_pct(reset.isoformat(), PERIOD_5H)

        assert result is not None
        self.assertAlmostEqual(result, 0.0)

    def test_half_elapsed(self, mock_dt):
        """Half of period elapsed, 50%."""
        utc_now = datetime(2025, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
        self._setup(mock_dt, utc_now)
        reset = utc_now + timedelta(seconds=PERIOD_5H / 2)
        result = elapsed_pct(reset.isoformat(), PERIOD_5H)

        assert result is not None
        self.assertAlmostEqual(result, 50.0)

    def test_fully_elapsed_hundred_percent(self, mock_dt):
        """Reset is now, 100% elapsed."""
        utc_now = datetime(2025, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
        self._setup(mock_dt, utc_now)
        result = elapsed_pct(utc_now.isoformat(), PERIOD_5H)

        assert result is not None
        self.assertAlmostEqual(result, 100.0)

    def test_past_reset_clamped_to_100(self, mock_dt):
        """Reset already passed, clamped to 100%."""
        utc_now = datetime(2025, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
        self._setup(mock_dt, utc_now)
        reset = utc_now - timedelta(hours=1)
        result = elapsed_pct(reset.isoformat(), PERIOD_5H)

        assert result is not None
        self.assertAlmostEqual(result, 100.0)

    def test_far_future_clamped_to_0(self, mock_dt):
        """Reset much further out than period duration, clamped to 0%."""
        utc_now = datetime(2025, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
        self._setup(mock_dt, utc_now)
        reset = utc_now + timedelta(seconds=PERIOD_5H * 3)
        result = elapsed_pct(reset.isoformat(), PERIOD_5H)

        assert result is not None
        self.assertAlmostEqual(result, 0.0)

    def test_7day_period(self, mock_dt):
        """7-day period, 3.5 days elapsed, 50%."""
        utc_now = datetime(2025, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
        self._setup(mock_dt, utc_now)
        reset = utc_now + timedelta(seconds=PERIOD_7D / 2)
        result = elapsed_pct(reset.isoformat(), PERIOD_7D)

        assert result is not None
        self.assertAlmostEqual(result, 50.0)


# ---------------------------------------------------------------------------
# divider_positions
# ---------------------------------------------------------------------------

class TestDividerPositions(unittest.TestCase):
    """Tests for divider_positions().

    Because divider_positions converts to local time via astimezone(), tests
    construct inputs relative to the system timezone so results are predictable
    on any machine.
    """

    @staticmethod
    def _local_to_utc_iso(naive_local: datetime) -> str:
        """Convert a naive local datetime to a UTC ISO string."""
        return naive_local.astimezone(timezone.utc).isoformat()

    def test_empty_resets_at(self):
        """Empty resets_at returns empty list."""
        self.assertEqual(divider_positions('', PERIOD_7D), [])

    def test_zero_period(self):
        """period_seconds=0 returns empty list."""
        self.assertEqual(divider_positions('2025-01-15T12:00:00+00:00', 0), [])

    def test_negative_period(self):
        """Negative period_seconds returns empty list."""
        self.assertEqual(divider_positions('2025-01-15T12:00:00+00:00', -100), [])

    def test_invalid_iso_string(self):
        """Invalid ISO string returns empty list."""
        self.assertEqual(divider_positions('not-a-date', PERIOD_7D), [])
        self.assertEqual(divider_positions('not-a-date', PERIOD_5H), [])

    def test_5h_period_has_four_hour_dividers(self):
        """5h period is split into five equal sections by four dividers."""
        reset_iso = self._local_to_utc_iso(datetime(2025, 1, 15, 15, 0, 0))
        result = divider_positions(reset_iso, PERIOD_5H)
        self.assertEqual(len(result), 4)
        for pos, expected in zip(result, (0.2, 0.4, 0.6, 0.8)):
            self.assertAlmostEqual(pos, expected, places=4)

    def test_5h_dividers_independent_of_window_start(self):
        """Sub-day dividers do not shift with the window's clock alignment."""
        # Hour-aligned (15:00), mid-hour (15:30), and midnight-spanning (04:00) windows split identically
        for reset_local in (datetime(2025, 1, 15, 15, 0, 0), datetime(2025, 1, 15, 15, 30, 0), datetime(2025, 1, 16, 4, 0, 0)):
            result = divider_positions(self._local_to_utc_iso(reset_local), PERIOD_5H)
            self.assertEqual(len(result), 4)
            self.assertAlmostEqual(result[0], 0.2, places=4)

    def test_other_subday_periods_have_no_dividers(self):
        """Sub-day periods other than five hours are not subdivided."""
        reset_iso = self._local_to_utc_iso(datetime(2025, 1, 15, 15, 0, 0))
        for period_seconds in (3600, 3 * 3600, 23 * 3600):
            self.assertEqual(divider_positions(reset_iso, period_seconds), [])

    def test_7d_period_has_seven_midnights(self):
        """7-day period from noon to noon has exactly 7 internal midnight boundaries."""
        # Period: Jan 15 12:00 to Jan 22 12:00 local - midnights on Jan 16-22
        reset_iso = self._local_to_utc_iso(datetime(2025, 1, 22, 12, 0, 0))
        result = divider_positions(reset_iso, PERIOD_7D)
        self.assertEqual(len(result), 7)

    def test_exactly_one_day_period_uses_midnights(self):
        """A period of exactly 24h subdivides at midnights, not hours."""
        # Period: Jan 15 12:00 to Jan 16 12:00 local - single midnight at 0.5
        reset_iso = self._local_to_utc_iso(datetime(2025, 1, 16, 12, 0, 0))
        result = divider_positions(reset_iso, 24 * 3600)
        self.assertEqual(len(result), 1)
        self.assertAlmostEqual(result[0], 0.5, places=4)

    def test_positions_are_sorted_ascending(self):
        """Positions must be in ascending order."""
        reset_iso = self._local_to_utc_iso(datetime(2025, 1, 22, 12, 0, 0))
        result = divider_positions(reset_iso, PERIOD_7D)
        self.assertEqual(result, sorted(result))

    def test_positions_in_valid_range(self):
        """All positions must be in the range (0.0, 1.0) exclusive."""
        reset_iso = self._local_to_utc_iso(datetime(2025, 1, 22, 12, 0, 0))
        result = divider_positions(reset_iso, PERIOD_7D)
        for pos in result:
            self.assertGreater(pos, 0.0)
            self.assertLess(pos, 1.0)

    def test_near_zero_positions_filtered(self):
        """Midnight positions very close to 0.0 (< 0.003) are filtered out."""
        # Period: Jan 15 23:59:50 to Jan 22 23:59:50 local. First midnight is 10s in ≈ 0.0000165, filtered.
        reset_iso = self._local_to_utc_iso(datetime(2025, 1, 22, 23, 59, 50))
        result = divider_positions(reset_iso, PERIOD_7D)
        self.assertEqual(len(result), 6)
        for pos in result:
            self.assertGreater(pos, 0.003)

    def test_7d_first_position_approximately_correct(self):
        """First midnight in a 7d period starting at noon is at roughly 1/14 of the bar."""
        # Period: Jan 15 12:00 to Jan 22 12:00 local. First midnight is 12h into 168h = 1/14
        reset_iso = self._local_to_utc_iso(datetime(2025, 1, 22, 12, 0, 0))
        result = divider_positions(reset_iso, PERIOD_7D)
        self.assertGreater(len(result), 0)
        self.assertAlmostEqual(result[0], 12 / 168, places=2)


# ---------------------------------------------------------------------------
# time_until
# ---------------------------------------------------------------------------

@patch('usage_monitor_for_claude.formatting.T', EN)
@patch('usage_monitor_for_claude.formatting.datetime')
class TestTimeUntil(unittest.TestCase):
    """Tests for time_until().

    Uses MagicMock for fromisoformat's return value so that
    astimezone() returns a controlled local datetime, making
    tests timezone-independent.
    """

    def _setup(self, mock_dt, utc_now, local_now, reset_local, remaining):
        mock_dt.now.side_effect = lambda tz=None: utc_now if tz else local_now

        mock_reset = MagicMock()
        mock_reset.__sub__.return_value = remaining
        mock_reset.astimezone.return_value = reset_local
        mock_dt.fromisoformat.return_value = mock_reset

    def test_empty_string(self, mock_dt):
        mock_dt.fromisoformat.side_effect = datetime.fromisoformat
        self.assertEqual(time_until(''), '')

    def test_invalid_string(self, mock_dt):
        mock_dt.fromisoformat.side_effect = datetime.fromisoformat
        self.assertEqual(time_until('not-a-date'), '')

    def test_past_reset_returns_empty(self, mock_dt):
        """Reset in the past (0 remaining minutes) returns empty string."""
        utc_now = datetime(2025, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
        local_now = datetime(2025, 1, 15, 12, 0, 0)
        self._setup(mock_dt, utc_now, local_now,
            datetime(2025, 1, 15, 11, 0, 0), timedelta(seconds=-3600))
        self.assertEqual(time_until('ignored'), '')

    def test_same_day_hours_and_minutes(self, mock_dt):
        """Reset today with >60 min remaining."""
        utc_now = datetime(2025, 1, 15, 10, 0, 0, tzinfo=timezone.utc)
        local_now = datetime(2025, 1, 15, 10, 0, 0)
        self._setup(mock_dt, utc_now, local_now,
            datetime(2025, 1, 15, 12, 30, 0), timedelta(hours=2, minutes=30))
        self.assertEqual(time_until('ignored'), 'Resets in 2h 30m (12:30)')

    def test_same_day_minutes_only(self, mock_dt):
        """Reset today with <60 min remaining."""
        utc_now = datetime(2025, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
        local_now = datetime(2025, 1, 15, 12, 0, 0)
        self._setup(mock_dt, utc_now, local_now,
            datetime(2025, 1, 15, 12, 45, 0), timedelta(minutes=45))
        self.assertEqual(time_until('ignored'), 'Resets in 45m (12:45)')

    def test_same_day_exactly_60_minutes(self, mock_dt):
        """Exactly 60 minutes uses hours+minutes format."""
        utc_now = datetime(2025, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
        local_now = datetime(2025, 1, 15, 12, 0, 0)
        self._setup(mock_dt, utc_now, local_now,
            datetime(2025, 1, 15, 13, 0, 0), timedelta(hours=1))
        self.assertEqual(time_until('ignored'), 'Resets in 1h 0m (13:00)')

    def test_same_day_one_minute(self, mock_dt):
        """One minute remaining."""
        utc_now = datetime(2025, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
        local_now = datetime(2025, 1, 15, 12, 0, 0)
        self._setup(mock_dt, utc_now, local_now,
            datetime(2025, 1, 15, 12, 1, 0), timedelta(minutes=1))
        self.assertEqual(time_until('ignored'), 'Resets in 1m (12:01)')

    def test_tomorrow(self, mock_dt):
        """Reset tomorrow."""
        utc_now = datetime(2025, 1, 15, 22, 0, 0, tzinfo=timezone.utc)
        local_now = datetime(2025, 1, 15, 22, 0, 0)
        self._setup(mock_dt, utc_now, local_now,
            datetime(2025, 1, 16, 10, 0, 0), timedelta(hours=12))
        self.assertEqual(time_until('ignored'), 'Resets tomorrow, 10:00')

    def test_future_weekday(self, mock_dt):
        """Reset in a few days shows weekday name."""
        # 2025-01-18 is Saturday (weekday index 5)
        utc_now = datetime(2025, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
        local_now = datetime(2025, 1, 15, 12, 0, 0)
        self._setup(mock_dt, utc_now, local_now,
            datetime(2025, 1, 18, 14, 0, 0), timedelta(days=3, hours=2))
        self.assertEqual(time_until('ignored'), 'Resets on Sat, 14:00')

    def test_seconds_rounded_up(self, mock_dt):
        """Seconds >= 30 round up the displayed minute."""
        utc_now = datetime(2025, 1, 15, 10, 0, 0, tzinfo=timezone.utc)
        local_now = datetime(2025, 1, 15, 10, 0, 0)
        self._setup(mock_dt, utc_now, local_now,
            datetime(2025, 1, 15, 12, 30, 45), timedelta(hours=2, minutes=30, seconds=45))
        self.assertEqual(time_until('ignored'), 'Resets in 2h 30m (12:31)')

    def test_seconds_rounded_down(self, mock_dt):
        """Seconds < 30 keep the displayed minute unchanged."""
        utc_now = datetime(2025, 1, 15, 10, 0, 0, tzinfo=timezone.utc)
        local_now = datetime(2025, 1, 15, 10, 0, 0)
        self._setup(mock_dt, utc_now, local_now,
            datetime(2025, 1, 15, 12, 30, 15), timedelta(hours=2, minutes=30, seconds=15))
        self.assertEqual(time_until('ignored'), 'Resets in 2h 30m (12:30)')

    def test_seconds_exactly_30_rounds_up(self, mock_dt):
        """Exactly 30 seconds rounds up (>= boundary)."""
        utc_now = datetime(2025, 1, 15, 10, 0, 0, tzinfo=timezone.utc)
        local_now = datetime(2025, 1, 15, 10, 0, 0)
        self._setup(mock_dt, utc_now, local_now,
            datetime(2025, 1, 15, 12, 30, 30), timedelta(hours=2, minutes=30, seconds=30))
        self.assertIn('12:31', time_until('ignored'))

    def test_less_than_60_seconds_returns_empty(self, mock_dt):
        """Less than 60 seconds remaining rounds to 0 minutes, returns empty."""
        utc_now = datetime(2025, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
        local_now = datetime(2025, 1, 15, 12, 0, 0)
        self._setup(mock_dt, utc_now, local_now,
            datetime(2025, 1, 15, 12, 0, 59), timedelta(seconds=59))
        self.assertEqual(time_until('ignored'), '')

    def test_exactly_60_seconds_shows_one_minute(self, mock_dt):
        """Exactly 60 seconds remaining rounds to 1 minute, shown."""
        utc_now = datetime(2025, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
        local_now = datetime(2025, 1, 15, 12, 0, 0)
        self._setup(mock_dt, utc_now, local_now,
            datetime(2025, 1, 15, 12, 1, 0), timedelta(seconds=60))
        self.assertIn('1m', time_until('ignored'))

    def test_rounding_crosses_midnight_changes_branch(self, mock_dt):
        """Second rounding at 23:59:45 rolls over to 00:00 next day, changing branch."""
        utc_now = datetime(2025, 1, 15, 21, 0, 0, tzinfo=timezone.utc)
        local_now = datetime(2025, 1, 15, 21, 0, 0)
        self._setup(mock_dt, utc_now, local_now,
            datetime(2025, 1, 15, 23, 59, 45), timedelta(hours=2, minutes=59, seconds=45))

        result = time_until('ignored')

        self.assertIn('00:00', result)
        self.assertIn('tomorrow', result)


# ---------------------------------------------------------------------------
# format_tooltip
# ---------------------------------------------------------------------------

@patch('usage_monitor_for_claude.formatting.T', EN)
class TestFormatTooltip(unittest.TestCase):
    """Tests for format_tooltip()."""

    def test_error(self):
        result = format_tooltip({'error': 'Connection failed'})
        self.assertEqual(result, 'Usage Monitor: Error\nConnection failed')

    def test_auth_error(self):
        data = {'error': 'Unauthorized', 'auth_error': True}
        result = format_tooltip(data)
        self.assertEqual(result, 'Claude Session Expired\nPlease open Claude Code to refresh your session.')

    def test_error_with_server_message(self):
        data = {'error': 'API request failed (HTTP 429).', 'server_message': 'Rate limited.'}
        result = format_tooltip(data)
        self.assertEqual(result, 'Usage Monitor: Error\nAPI request failed (HTTP 429). Rate limited.')

    def test_error_with_server_message_truncated_to_80_chars(self):
        data = {'error': 'API request failed (HTTP 429).', 'server_message': 'x' * 200}
        error_line = format_tooltip(data).split('\n')[1]
        self.assertEqual(len(error_line), 80)

    def test_error_message_truncated_to_80_chars(self):
        result = format_tooltip({'error': 'x' * 200})
        error_line = result.split('\n')[1]
        self.assertEqual(len(error_line), 80)

    @patch('usage_monitor_for_claude.formatting.time_until', return_value='')
    def test_both_periods(self, _mock_tu):
        data = {
            'five_hour': {'utilization': 42.0, 'resets_at': ''},
            'seven_day': {'utilization': 15.0, 'resets_at': ''},
        }
        self.assertEqual(format_tooltip(data), 'Claude Usage\n5h: 42%\n7d: 15%')

    @patch('usage_monitor_for_claude.formatting.time_until', return_value='Resets in 2h 30m (14:30)')
    def test_with_reset_info(self, _mock_tu):
        data = {'five_hour': {'utilization': 42.0, 'resets_at': '2025-01-15T14:30:00+00:00'}}
        self.assertEqual(format_tooltip(data), 'Claude Usage\n5h: 42% (Resets in 2h 30m (14:30))')

    @patch('usage_monitor_for_claude.formatting.time_until', return_value='')
    def test_utilization_none_skipped(self, _mock_tu):
        data = {
            'five_hour': {'utilization': None, 'resets_at': ''},
            'seven_day': {'utilization': 80.0, 'resets_at': ''},
        }
        self.assertEqual(format_tooltip(data), 'Claude Usage\n7d: 80%')

    @patch('usage_monitor_for_claude.formatting.time_until', return_value='')
    def test_empty_data_shows_title_only(self, _mock_tu):
        self.assertEqual(format_tooltip({}), 'Claude Usage')

    @patch('usage_monitor_for_claude.formatting.time_until', return_value='')
    def test_zero_percent(self, _mock_tu):
        data = {'five_hour': {'utilization': 0.0, 'resets_at': ''}}
        self.assertEqual(format_tooltip(data), 'Claude Usage\n5h: 0%')

    @patch('usage_monitor_for_claude.formatting.time_until', return_value='')
    def test_hundred_percent(self, _mock_tu):
        data = {'five_hour': {'utilization': 100.0, 'resets_at': ''}}
        self.assertEqual(format_tooltip(data), 'Claude Usage\n5h: 100%')

    @patch('usage_monitor_for_claude.formatting.time_until', return_value='')
    def test_entry_none_skipped(self, _mock_tu):
        """Entry that is None is skipped by the guard clause."""
        data = {'five_hour': None, 'seven_day': {'utilization': 50.0, 'resets_at': ''}}
        self.assertEqual(format_tooltip(data), 'Claude Usage\n7d: 50%')

    @patch('usage_monitor_for_claude.formatting.time_until', return_value='')
    def test_entry_empty_dict_skipped(self, _mock_tu):
        """Entry with no utilization key is skipped."""
        data = {'five_hour': {}, 'seven_day': {'utilization': 50.0, 'resets_at': ''}}
        self.assertEqual(format_tooltip(data), 'Claude Usage\n7d: 50%')

    @patch('usage_monitor_for_claude.formatting.time_until', return_value='')
    def test_only_seven_day(self, _mock_tu):
        """Only seven_day present, five_hour absent."""
        data = {'seven_day': {'utilization': 25.0, 'resets_at': ''}}
        self.assertEqual(format_tooltip(data), 'Claude Usage\n7d: 25%')

    def test_auth_error_false_shows_normal_error(self):
        """auth_error=False with error shows normal error, not auth message."""
        data = {'error': 'Something broke', 'auth_error': False}
        result = format_tooltip(data)
        self.assertEqual(result, 'Usage Monitor: Error\nSomething broke')

    @patch('usage_monitor_for_claude.formatting.time_until', return_value='')
    def test_extra_usage_ignored(self, _mock_tu):
        """Extra usage data is not shown in tooltip."""
        data = {
            'five_hour': {'utilization': 26.0, 'resets_at': ''},
            'extra_usage': {'is_enabled': True, 'monthly_limit': 1000, 'used_credits': 420.0},
        }
        self.assertEqual(format_tooltip(data), 'Claude Usage\n5h: 26%')

    @patch('usage_monitor_for_claude.formatting.time_until', return_value='')
    @patch('usage_monitor_for_claude.formatting.TOOLTIP_FIELDS', ['seven_day_sonnet', 'five_hour'])
    def test_custom_fields_and_order(self, _mock_tu):
        """Custom tooltip_fields controls which fields appear and in what order."""
        data = {
            'five_hour': {'utilization': 10.0, 'resets_at': ''},
            'seven_day': {'utilization': 20.0, 'resets_at': ''},
            'seven_day_sonnet': {'utilization': 30.0, 'resets_at': ''},
        }
        self.assertEqual(format_tooltip(data), 'Claude Usage\n7d Sonnet: 30%\n5h: 10%')

    @patch('usage_monitor_for_claude.formatting.time_until', return_value='')
    @patch('usage_monitor_for_claude.formatting.TOOLTIP_FIELDS', ['seven_day_sonnet'])
    def test_custom_field_null_skipped(self, _mock_tu):
        """Configured field that is null in API response is skipped."""
        data = {'seven_day_sonnet': None, 'five_hour': {'utilization': 50.0, 'resets_at': ''}}
        self.assertEqual(format_tooltip(data), 'Claude Usage')

    @patch('usage_monitor_for_claude.formatting.time_until', return_value='')
    @patch('usage_monitor_for_claude.formatting.TOOLTIP_FIELDS', ['nonexistent_field'])
    def test_custom_field_missing_from_response_skipped(self, _mock_tu):
        """Configured field not present in API response is skipped."""
        data = {'five_hour': {'utilization': 50.0, 'resets_at': ''}}
        self.assertEqual(format_tooltip(data), 'Claude Usage')

    @patch('usage_monitor_for_claude.formatting.time_until', return_value='')
    @patch('usage_monitor_for_claude.formatting.TOOLTIP_FIELDS', [])
    def test_empty_fields_shows_title_only(self, _mock_tu):
        """Empty tooltip_fields shows only the title."""
        data = {'five_hour': {'utilization': 50.0, 'resets_at': ''}}
        self.assertEqual(format_tooltip(data), 'Claude Usage')


# ---------------------------------------------------------------------------
# tooltip length - Windows limits tooltip text to 127 characters
# ---------------------------------------------------------------------------

class TestTooltipMaxLength(unittest.TestCase):
    """Verify tooltip text stays within Windows' 127-char limit for all locales."""

    TOOLTIP_MAX = 127

    def _longest_reset(self, t: dict, max_hours: int) -> str:
        """Return the longest possible reset text for a given max-hour value."""
        dur = t['duration_hm'].format(h=max_hours, m=59)
        candidates = [
            t['resets_in'].format(duration=dur, clock='23:59'),
            t['resets_tomorrow'].format(clock='23:59'),
        ]
        for wd in t['weekdays']:
            candidates.append(t['resets_weekday'].format(day=wd, clock='23:59'))

        return max(candidates, key=len)

    def _worst_case_tooltip(self, t: dict) -> str:
        """Build the longest possible tooltip from a locale dict.

        Worst case: both 5h and 7d visible at 100%, each with the longest
        possible reset text (same-day, tomorrow, or weekday).
        """
        reset_5h = self._longest_reset(t, max_hours=4)
        reset_7d = self._longest_reset(t, max_hours=23)

        return f"{t['tooltip_title']}\n5h: 100% ({reset_5h})\n7d: 100% ({reset_7d})"

    def test_all_locales_fit_tooltip(self):
        """Every locale's worst-case tooltip must fit in 127 characters."""
        for locale_file in sorted(LOCALE_DIR.glob('*.json')):
            with self.subTest(locale=locale_file.stem):
                t = json.loads(locale_file.read_text(encoding='utf-8'))
                tooltip = self._worst_case_tooltip(t)
                self.assertLessEqual(
                    len(tooltip), self.TOOLTIP_MAX,
                    f"Locale '{locale_file.stem}' tooltip is {len(tooltip)} chars "
                    f"(max {self.TOOLTIP_MAX}):\n{tooltip}",
                )


# ---------------------------------------------------------------------------
# format_credits
# ---------------------------------------------------------------------------

class TestFormatCredits(unittest.TestCase):
    """Tests for format_credits()."""

    @patch('usage_monitor_for_claude.formatting._SYSTEM_CURRENCY_SYMBOL', '$')
    @patch('usage_monitor_for_claude.formatting.CURRENCY_SYMBOL', '$')
    @patch('usage_monitor_for_claude.formatting._locale.currency', return_value='$4.20')
    def test_uses_locale_currency(self, mock_currency):
        """Uses locale.currency() for formatting."""
        self.assertEqual(format_credits(420.0), '$4.20')
        mock_currency.assert_called_once_with(4.2, grouping=True)

    @patch('usage_monitor_for_claude.formatting._SYSTEM_CURRENCY_SYMBOL', '€')
    @patch('usage_monitor_for_claude.formatting.CURRENCY_SYMBOL', '$')
    @patch('usage_monitor_for_claude.formatting._locale.currency', return_value='10,00 €')
    def test_symbol_override_replaces(self, mock_currency):
        """Settings override replaces system symbol in formatted output."""
        self.assertEqual(format_credits(1000.0), '10,00 $')

    @patch('usage_monitor_for_claude.formatting._SYSTEM_CURRENCY_SYMBOL', '')
    @patch('usage_monitor_for_claude.formatting.CURRENCY_SYMBOL', '')
    @patch('usage_monitor_for_claude.formatting._locale.currency', side_effect=ValueError)
    def test_no_symbol_plain_number(self, mock_currency):
        """No currency symbol falls back to plain number."""
        self.assertEqual(format_credits(420.0), '4.20')

    @patch('usage_monitor_for_claude.formatting._SYSTEM_CURRENCY_SYMBOL', '')
    @patch('usage_monitor_for_claude.formatting.CURRENCY_SYMBOL', '¥')
    @patch('usage_monitor_for_claude.formatting._locale.currency', side_effect=ValueError)
    def test_locale_error_uses_symbol_fallback(self, mock_currency):
        """Locale error falls back to manual formatting with symbol."""
        self.assertEqual(format_credits(420.0), '¥\u00a04.20')

    @patch('usage_monitor_for_claude.formatting._SYSTEM_CURRENCY_SYMBOL', '$')
    @patch('usage_monitor_for_claude.formatting.CURRENCY_SYMBOL', '$')
    @patch('usage_monitor_for_claude.formatting._locale.currency', return_value='$0.00')
    def test_zero_cents(self, mock_currency):
        """Zero cents formats correctly."""
        self.assertEqual(format_credits(0.0), '$0.00')


if __name__ == '__main__':
    unittest.main()

"""
Popup Tests
=============

Unit tests for popup data helpers: _usage_entries, _snapshot_to_dict,
and _init_config.
"""
from __future__ import annotations

import threading
import time
import unittest
from unittest.mock import MagicMock, patch

from usage_monitor_for_claude.cache import CacheSnapshot
from usage_monitor_for_claude.popup import UsagePopup, _init_config, _snapshot_to_dict, _usage_entries


def _snap(
    usage=None, profile=None, prepaid=None, last_success_time=None,
    refreshing=False, last_error=None, version=1,
) -> CacheSnapshot:
    """Build a CacheSnapshot with convenient defaults."""
    return CacheSnapshot(
        usage=usage or {},
        profile=profile,
        prepaid=prepaid,
        last_success_time=last_success_time,
        refreshing=refreshing,
        last_error=last_error,
        version=version,
    )


# ---------------------------------------------------------------------------
# _usage_entries
# ---------------------------------------------------------------------------

class TestUsageEntries(unittest.TestCase):
    """Tests for _usage_entries - extracts labelled tuples from usage dict."""

    def test_returns_entries_for_active_fields(self):
        """Returns entries only for non-null fields with utilization."""
        usage = {
            'five_hour': {'utilization': 42, 'resets_at': '2026-01-01T00:00:00Z'},
            'seven_day': {'utilization': 10, 'resets_at': '2026-01-07T00:00:00Z'},
            'seven_day_sonnet': None,
        }
        entries = _usage_entries(usage)
        self.assertEqual(len(entries), 2)

    def test_labels_use_popup_label(self):
        """Each entry's label is generated via popup_label."""
        from usage_monitor_for_claude.formatting import popup_label

        usage = {
            'five_hour': {'utilization': 42, 'resets_at': '2026-01-01T00:00:00Z'},
            'seven_day': {'utilization': 10, 'resets_at': '2026-01-07T00:00:00Z'},
        }
        entries = _usage_entries(usage)
        labels = [e[0] for e in entries]
        self.assertEqual(labels, [popup_label('five_hour'), popup_label('seven_day')])

    def test_periods_derived_from_field_name(self):
        """Period is derived from the field name via field_period."""
        usage = {
            'five_hour': {'utilization': 42, 'resets_at': '2026-01-01T00:00:00Z'},
            'seven_day': {'utilization': 10, 'resets_at': '2026-01-07T00:00:00Z'},
        }
        entries = _usage_entries(usage)
        periods = [e[2] for e in entries]
        self.assertEqual(periods, [5 * 3600, 7 * 24 * 3600])

    def test_data_extraction(self):
        """Entry data is pulled from the correct usage dict keys."""
        five_hour = {'utilization': 42, 'resets_at': '2026-01-01T00:00:00Z'}
        seven_day = {'utilization': 10, 'resets_at': '2026-01-07T00:00:00Z'}
        usage = {'five_hour': five_hour, 'seven_day': seven_day}

        entries = _usage_entries(usage)
        self.assertEqual(len(entries), 2)
        self.assertIs(entries[0][1], five_hour)
        self.assertIs(entries[1][1], seven_day)

    def test_entry_includes_field_key(self):
        """Each entry's 4th element is the raw API field name."""
        usage = {
            'five_hour': {'utilization': 42, 'resets_at': '2026-01-01T00:00:00Z'},
            'seven_day_opus': {'utilization': 10, 'resets_at': '2026-01-07T00:00:00Z'},
        }
        entries = _usage_entries(usage)
        keys = [e[3] for e in entries]
        self.assertEqual(keys, ['five_hour', 'seven_day_opus'])

    def test_empty_usage_returns_empty(self):
        """Empty usage dict returns no entries."""
        self.assertEqual(_usage_entries({}), [])

    def test_all_null_fields_returns_empty(self):
        """All-null fields return no entries."""
        usage = {'five_hour': None, 'seven_day': None, 'seven_day_sonnet': None}
        self.assertEqual(_usage_entries(usage), [])

    def test_null_utilization_skipped(self):
        """Fields with utilization None are skipped."""
        usage = {
            'five_hour': {'utilization': None, 'resets_at': '2026-01-01T05:00:00Z'},
            'seven_day': {'utilization': 20, 'resets_at': '2026-01-07T00:00:00Z'},
        }
        entries = _usage_entries(usage)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0][1]['utilization'], 20)

    @patch('usage_monitor_for_claude.popup.POPUP_FIELDS', ['fve_hour', 'seven_day'])
    def test_misspelled_popup_field_skipped(self):
        """Misspelled popup_fields entry is skipped, valid one shown."""
        usage = {
            'five_hour': {'utilization': 42, 'resets_at': '2026-01-01T05:00:00Z'},
            'seven_day': {'utilization': 20, 'resets_at': '2026-01-07T00:00:00Z'},
        }
        entries = _usage_entries(usage)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0][1]['utilization'], 20)

    @patch('usage_monitor_for_claude.popup.POPUP_FIELDS', ['seven_day_sonnet'])
    def test_popup_field_pointing_to_null_skipped(self):
        """popup_fields entry pointing to a null field produces no entries."""
        usage = {'seven_day_sonnet': None, 'five_hour': {'utilization': 42, 'resets_at': ''}}
        entries = _usage_entries(usage)
        self.assertEqual(entries, [])

    def test_non_dict_values_in_usage_ignored(self):
        """Non-dict values (like error strings) in usage are ignored."""
        usage = {
            'error': 'server down',
            'five_hour': {'utilization': 42, 'resets_at': '2026-01-01T05:00:00Z'},
        }
        entries = _usage_entries(usage)
        self.assertEqual(len(entries), 1)

    def test_extra_usage_not_shown_as_bar(self):
        """extra_usage is excluded from dynamic bars (different structure)."""
        usage = {
            'five_hour': {'utilization': 42, 'resets_at': '2026-01-01T05:00:00Z'},
            'extra_usage': {'is_enabled': True, 'monthly_limit': 1000, 'used_credits': 500, 'utilization': 50},
        }
        entries = _usage_entries(usage)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0][1]['utilization'], 42)


# ---------------------------------------------------------------------------
# _snapshot_to_dict
# ---------------------------------------------------------------------------

class TestSnapshotToDict(unittest.TestCase):
    """Tests for _snapshot_to_dict - converts CacheSnapshot to popup JSON."""

    # -- profile --

    def test_no_profile(self):
        """Profile is None when snapshot has no profile."""
        result = _snapshot_to_dict(_snap(), installations=[])
        self.assertIsNone(result['profile'])

    def test_profile_extraction(self):
        """Email and plan are extracted from nested account/organization dicts."""
        profile = {
            'account': {'email': 'test@example.com'},
            'organization': {'organization_type': 'pro_team'},
        }
        result = _snapshot_to_dict(_snap(profile=profile), installations=[])
        self.assertEqual(result['profile']['email'], 'test@example.com')
        self.assertEqual(result['profile']['plan'], 'Pro Team')

    def test_empty_profile_hidden(self):
        """Empty profile dict from API is treated as absent (no broken UI)."""
        result = _snapshot_to_dict(_snap(profile={}), installations=[])
        self.assertIsNone(result['profile'])

    def test_profile_missing_nested_keys(self):
        """Present but incomplete profile defaults missing fields to empty strings."""
        result = _snapshot_to_dict(_snap(profile={'account': {}}), installations=[])
        self.assertEqual(result['profile']['email'], '')
        self.assertEqual(result['profile']['plan'], '')

    def test_profile_with_null_account_and_organization(self):
        """A profile carrying account/organization as null must not crash the popup."""
        result = _snapshot_to_dict(_snap(profile={'account': None, 'organization': None}), installations=[])
        self.assertEqual(result['profile']['email'], '')
        self.assertEqual(result['profile']['plan'], '')

    # -- usage bars --

    def test_no_usage_data(self):
        """Empty usage dict produces empty usage list."""
        result = _snapshot_to_dict(_snap(), installations=[])
        self.assertEqual(result['usage'], [])

    def test_skips_entries_without_utilization(self):
        """Entries with None utilization are omitted."""
        usage = {'five_hour': {'utilization': None}}
        result = _snapshot_to_dict(_snap(usage=usage), installations=[])
        self.assertEqual(result['usage'], [])

    def test_skips_missing_entries(self):
        """Missing usage keys produce no bar entries."""
        usage = {'five_hour': None}
        result = _snapshot_to_dict(_snap(usage=usage), installations=[])
        self.assertEqual(result['usage'], [])

    @patch('usage_monitor_for_claude.popup.elapsed_pct', return_value=None)
    @patch('usage_monitor_for_claude.popup.time_until', return_value='5h 0m')
    @patch('usage_monitor_for_claude.popup.divider_positions', return_value=[])
    def test_usage_bar_fields(self, _mock_dividers, _mock_time_until, _mock_elapsed):
        """Each usage bar dict has all required fields with correct types."""
        usage = {'five_hour': {'utilization': 42, 'resets_at': '2026-01-01T05:00:00Z'}}
        result = _snapshot_to_dict(_snap(usage=usage), installations=[])

        self.assertEqual(len(result['usage']), 1)
        bar = result['usage'][0]
        self.assertEqual(bar['pct_text'], '42%')
        self.assertAlmostEqual(bar['fill_pct'], 0.42)
        self.assertFalse(bar['warn'])
        self.assertIsNone(bar['marker_rel'])
        self.assertEqual(bar['reset_text'], '5h 0m')
        self.assertEqual(bar['dividers'], [])

    def test_field_with_null_resets_at(self):
        """An inactive scoped limit (resets_at None) renders a 0% bar with no reset text."""
        usage = {'seven_day_fable': {'utilization': 0.0, 'resets_at': None}}
        result = _snapshot_to_dict(_snap(usage=usage), installations=[])

        self.assertEqual(len(result['usage']), 1)
        bar = result['usage'][0]
        self.assertEqual(bar['key'], 'seven_day_fable')
        self.assertEqual(bar['pct_text'], '0%')
        self.assertEqual(bar['fill_pct'], 0.0)
        self.assertEqual(bar['reset_text'], '')
        self.assertEqual(bar['dividers'], [])
        self.assertIsNone(bar['marker_rel'])
        self.assertFalse(bar['warn'])

    @patch('usage_monitor_for_claude.popup.elapsed_pct', return_value=30.0)
    @patch('usage_monitor_for_claude.popup.time_until', return_value='3h 30m')
    @patch('usage_monitor_for_claude.popup.divider_positions', return_value=[0.5])
    def test_warn_when_usage_ahead_of_time(self, _mock_dividers, _mock_time_until, _mock_elapsed):
        """Bar is marked warn when utilization exceeds elapsed percentage."""
        usage = {'five_hour': {'utilization': 60, 'resets_at': '2026-01-01T05:00:00Z'}}
        result = _snapshot_to_dict(_snap(usage=usage), installations=[])

        bar = result['usage'][0]
        self.assertTrue(bar['warn'])
        self.assertAlmostEqual(bar['marker_rel'], 0.3)

    @patch('usage_monitor_for_claude.popup.elapsed_pct', return_value=80.0)
    @patch('usage_monitor_for_claude.popup.time_until', return_value='1h 0m')
    @patch('usage_monitor_for_claude.popup.divider_positions', return_value=[])
    def test_no_warn_when_usage_behind_time(self, _mock_dividers, _mock_time_until, _mock_elapsed):
        """Bar is not warn when utilization is below elapsed percentage."""
        usage = {'five_hour': {'utilization': 40, 'resets_at': '2026-01-01T05:00:00Z'}}
        result = _snapshot_to_dict(_snap(usage=usage), installations=[])

        bar = result['usage'][0]
        self.assertFalse(bar['warn'])

    @patch('usage_monitor_for_claude.popup.elapsed_pct', return_value=50.0)
    @patch('usage_monitor_for_claude.popup.time_until', return_value='2h 30m')
    @patch('usage_monitor_for_claude.popup.divider_positions', return_value=[])
    def test_no_warn_when_equal(self, _mock_dividers, _mock_time_until, _mock_elapsed):
        """Exactly equal usage and elapsed is not a warning (strictly greater)."""
        usage = {'five_hour': {'utilization': 50, 'resets_at': '2026-01-01T05:00:00Z'}}
        result = _snapshot_to_dict(_snap(usage=usage), installations=[])
        self.assertFalse(result['usage'][0]['warn'])

    @patch('usage_monitor_for_claude.popup.elapsed_pct', return_value=None)
    @patch('usage_monitor_for_claude.popup.time_until', return_value='')
    @patch('usage_monitor_for_claude.popup.divider_positions', return_value=[])
    def test_warn_at_100_without_time_period(self, _mock_dividers, _mock_time_until, _mock_elapsed):
        """Bar at 100% is warn even when no time period (time_pct is None)."""
        usage = {'five_hour': {'utilization': 100, 'resets_at': ''}}
        result = _snapshot_to_dict(_snap(usage=usage), installations=[])
        self.assertTrue(result['usage'][0]['warn'])

    @patch('usage_monitor_for_claude.popup.elapsed_pct', return_value=100.0)
    @patch('usage_monitor_for_claude.popup.time_until', return_value='')
    @patch('usage_monitor_for_claude.popup.divider_positions', return_value=[])
    def test_warn_at_100_when_time_also_100(self, _mock_dividers, _mock_time_until, _mock_elapsed):
        """Bar at 100% is warn even when elapsed time is also 100% (strict > would miss this)."""
        usage = {'five_hour': {'utilization': 100, 'resets_at': '2026-01-01T05:00:00Z'}}
        result = _snapshot_to_dict(_snap(usage=usage), installations=[])
        self.assertTrue(result['usage'][0]['warn'])

    @patch('usage_monitor_for_claude.popup.elapsed_pct', return_value=None)
    @patch('usage_monitor_for_claude.popup.time_until', return_value='')
    @patch('usage_monitor_for_claude.popup.divider_positions', return_value=[])
    def test_fill_pct_clamped_to_0_1(self, _mock_dividers, _mock_time_until, _mock_elapsed):
        """Fill percentage is clamped between 0.0 and 1.0, and over-quota is always warn."""
        usage = {'five_hour': {'utilization': 150, 'resets_at': '2026-01-01T05:00:00Z'}}
        result = _snapshot_to_dict(_snap(usage=usage), installations=[])
        self.assertEqual(result['usage'][0]['fill_pct'], 1.0)
        self.assertTrue(result['usage'][0]['warn'])

    @patch('usage_monitor_for_claude.popup.elapsed_pct', return_value=None)
    @patch('usage_monitor_for_claude.popup.time_until', return_value='')
    @patch('usage_monitor_for_claude.popup.divider_positions', return_value=[])
    def test_zero_utilization(self, _mock_dividers, _mock_time_until, _mock_elapsed):
        """Zero utilization produces 0% text and 0.0 fill."""
        usage = {'five_hour': {'utilization': 0, 'resets_at': '2026-01-01T05:00:00Z'}}
        result = _snapshot_to_dict(_snap(usage=usage), installations=[])
        # utilization 0 is falsy, so `or 0` kicks in - entry is still shown
        bar = result['usage'][0]
        self.assertEqual(bar['pct_text'], '0%')
        self.assertAlmostEqual(bar['fill_pct'], 0.0)

    @patch('usage_monitor_for_claude.popup.elapsed_pct', return_value=None)
    @patch('usage_monitor_for_claude.popup.time_until', return_value='')
    @patch('usage_monitor_for_claude.popup.divider_positions', return_value=[])
    def test_multiple_usage_entries(self, _mock_dividers, _mock_time_until, _mock_elapsed):
        """Multiple usage types each produce a bar entry."""
        usage = {
            'five_hour': {'utilization': 10, 'resets_at': '2026-01-01T05:00:00Z'},
            'seven_day': {'utilization': 20, 'resets_at': '2026-01-07T00:00:00Z'},
            'seven_day_sonnet': {'utilization': 30, 'resets_at': '2026-01-07T00:00:00Z'},
        }
        result = _snapshot_to_dict(_snap(usage=usage), installations=[])
        self.assertEqual(len(result['usage']), 3)
        pcts = [b['pct_text'] for b in result['usage']]
        self.assertEqual(pcts, ['10%', '20%', '30%'])

    @patch('usage_monitor_for_claude.popup.elapsed_pct', return_value=None)
    @patch('usage_monitor_for_claude.popup.time_until', return_value='')
    @patch('usage_monitor_for_claude.popup.divider_positions', return_value=[])
    def test_usage_bar_includes_field_key(self, _mock_div, _mock_tu, _mock_ep):
        """Each usage bar dict carries its API field name for compact hiding."""
        usage = {
            'five_hour': {'utilization': 10, 'resets_at': '2026-01-01T05:00:00Z'},
            'seven_day_opus': {'utilization': 30, 'resets_at': '2026-01-07T00:00:00Z'},
        }
        result = _snapshot_to_dict(_snap(usage=usage), installations=[])
        keys = [bar['key'] for bar in result['usage']]
        self.assertEqual(keys, ['five_hour', 'seven_day_opus'])

    @patch('usage_monitor_for_claude.popup.POPUP_FIELDS', ['typo_field', 'seven_day'])
    @patch('usage_monitor_for_claude.popup.elapsed_pct', return_value=None)
    @patch('usage_monitor_for_claude.popup.time_until', return_value='')
    @patch('usage_monitor_for_claude.popup.divider_positions', return_value=[])
    def test_misspelled_popup_field_skipped_in_dict(self, _mock_div, _mock_tu, _mock_ep):
        """Misspelled popup_fields entry produces no bar, valid one shown."""
        usage = {
            'five_hour': {'utilization': 42, 'resets_at': '2026-01-01T05:00:00Z'},
            'seven_day': {'utilization': 20, 'resets_at': '2026-01-07T00:00:00Z'},
        }
        result = _snapshot_to_dict(_snap(usage=usage), installations=[])
        self.assertEqual(len(result['usage']), 1)
        self.assertEqual(result['usage'][0]['pct_text'], '20%')

    def test_all_null_fields_no_bars(self):
        """All-null quota fields produce no usage bars."""
        usage = {'five_hour': None, 'seven_day': None, 'seven_day_sonnet': None}
        result = _snapshot_to_dict(_snap(usage=usage), installations=[])
        self.assertEqual(result['usage'], [])

    @patch('usage_monitor_for_claude.popup.elapsed_pct', return_value=None)
    @patch('usage_monitor_for_claude.popup.time_until', return_value='')
    @patch('usage_monitor_for_claude.popup.divider_positions', return_value=[])
    def test_non_dict_values_in_response_ignored(self, _mock_div, _mock_tu, _mock_ep):
        """Non-dict values in the API response are not shown as bars."""
        usage = {
            'error': 'temporary',
            'rate_limited': True,
            'five_hour': {'utilization': 42, 'resets_at': '2026-01-01T05:00:00Z'},
        }
        result = _snapshot_to_dict(_snap(usage=usage), installations=[])
        self.assertEqual(len(result['usage']), 1)
        self.assertEqual(result['usage'][0]['pct_text'], '42%')

    # -- extra usage --

    def test_no_extra_usage(self):
        """Extra is None when no extra_usage key in usage dict."""
        result = _snapshot_to_dict(_snap(), installations=[])
        self.assertIsNone(result['extra'])

    def test_extra_usage_disabled(self):
        """Extra is None when extra usage is not enabled."""
        usage = {'extra_usage': {'is_enabled': False, 'monthly_limit': 1000, 'used_credits': 500}}
        result = _snapshot_to_dict(_snap(usage=usage), installations=[])
        self.assertIsNone(result['extra'])

    def test_extra_usage_enabled_no_used_credits_key(self):
        """Extra is None when used_credits is absent, even if enabled."""
        usage = {'extra_usage': {'is_enabled': True, 'monthly_limit': 1000}}
        result = _snapshot_to_dict(_snap(usage=usage), installations=[])
        self.assertIsNone(result['extra'])

    @patch('usage_monitor_for_claude.popup.format_credits', side_effect=lambda c, *_: f'${c / 100:.2f}')
    def test_extra_usage_zero_limit_shows_no_cap_variant(self, _mock_credits):
        """A zero monthly limit shows the no-cap spent text instead of hiding the section."""
        usage = {'extra_usage': {'is_enabled': True, 'monthly_limit': 0, 'used_credits': 0}}
        result = _snapshot_to_dict(_snap(usage=usage), installations=[])
        extra = result['extra']
        self.assertIsNotNone(extra)
        self.assertFalse(extra['has_limit'])
        self.assertEqual(extra['pct_text'], '')
        self.assertIn('$0.00', extra['spent_text'])

    @patch('usage_monitor_for_claude.popup.format_credits', side_effect=lambda c, *_: f'${c / 100:.2f}')
    def test_extra_usage_null_limit_shows_no_cap_variant(self, _mock_credits):
        """A null monthly_limit (uncapped pay-as-you-go credits) shows what has been spent."""
        usage = {'extra_usage': {'is_enabled': True, 'monthly_limit': None, 'used_credits': 2981}}
        result = _snapshot_to_dict(_snap(usage=usage), installations=[])
        extra = result['extra']
        self.assertIsNotNone(extra)
        self.assertFalse(extra['has_limit'])
        self.assertIn('$29.81', extra['spent_text'])

    @patch('usage_monitor_for_claude.popup.format_credits', side_effect=lambda c, *_: f'${c / 100:.2f}')
    def test_extra_usage_calculation(self, _mock_credits):
        """Extra usage computes percentage and formatted text correctly."""
        usage = {'extra_usage': {'is_enabled': True, 'monthly_limit': 10000, 'used_credits': 2500}}
        result = _snapshot_to_dict(_snap(usage=usage), installations=[])

        extra = result['extra']
        self.assertIsNotNone(extra)
        self.assertTrue(extra['has_limit'])
        self.assertEqual(extra['pct_text'], '25%')
        self.assertAlmostEqual(extra['fill_pct'], 0.25)
        self.assertIn('$25.00', extra['spent_text'])
        self.assertIn('$100.00', extra['spent_text'])

    @patch('usage_monitor_for_claude.popup.format_credits', side_effect=lambda c, *_: f'${c / 100:.2f}')
    def test_extra_usage_fill_clamped(self, _mock_credits):
        """Extra usage fill is clamped to 1.0 when over limit."""
        usage = {'extra_usage': {'is_enabled': True, 'monthly_limit': 1000, 'used_credits': 2000}}
        result = _snapshot_to_dict(_snap(usage=usage), installations=[])
        self.assertEqual(result['extra']['fill_pct'], 1.0)

    # -- installations --

    def test_installations_passthrough(self):
        """Pre-computed installations list is passed through unchanged."""
        installs = [{'name': 'VS Code', 'version': '1.0.0'}]
        result = _snapshot_to_dict(_snap(), installations=installs)
        self.assertEqual(result['installations'], installs)

    @patch('usage_monitor_for_claude.popup.find_installations')
    def test_installations_auto_detected(self, mock_find):
        """When installations is None, find_installations() is called."""
        inst = MagicMock()
        inst.name = 'Cursor'
        inst.version = '2.0.0'
        mock_find.return_value = [inst]

        result = _snapshot_to_dict(_snap(), installations=None)
        mock_find.assert_called_once()
        self.assertEqual(result['installations'], [{'name': 'Cursor', 'version': '2.0.0'}])

    # -- status --

    def test_status_error_when_no_usage(self):
        """Shows error text when there's no usage data but there's an error."""
        result = _snapshot_to_dict(_snap(usage={}, last_error='Connection failed'), installations=[])
        self.assertEqual(result['status']['text'], 'Connection failed')
        self.assertTrue(result['status']['is_error'])

    def test_status_error_truncated(self):
        """Error messages are truncated to 120 characters."""
        long_error = 'x' * 200
        result = _snapshot_to_dict(_snap(usage={}, last_error=long_error), installations=[])
        self.assertEqual(len(result['status']['text']), 120)

    def test_status_refreshing_when_no_usage_no_error(self):
        """Shows refreshing status when no usage data and no error."""
        from usage_monitor_for_claude.i18n import T

        result = _snapshot_to_dict(_snap(usage={}, last_error=None), installations=[])
        self.assertEqual(result['status']['text'], T['status_refreshing'])
        self.assertFalse(result['status']['is_error'])

    @patch('usage_monitor_for_claude.popup.elapsed_pct', return_value=None)
    @patch('usage_monitor_for_claude.popup.time_until', return_value='')
    @patch('usage_monitor_for_claude.popup.divider_positions', return_value=[])
    def test_status_live_mode_keys(self, _mock_div, _mock_tu, _mock_ep):
        """Live mode status contains all required keys for the JS timer."""
        usage = {'five_hour': {'utilization': 50, 'resets_at': '2026-01-01T05:00:00Z'}}
        result = _snapshot_to_dict(
            _snap(usage=usage, last_success_time=1000.0, refreshing=True, last_error='Server down'),
            installations=[], next_poll_time=1180.0,
        )
        self.assertEqual(set(result['status'].keys()), {'last_success_time', 'next_poll_time', 'refreshing', 'error'})

    @patch('usage_monitor_for_claude.popup.elapsed_pct', return_value=None)
    @patch('usage_monitor_for_claude.popup.time_until', return_value='')
    @patch('usage_monitor_for_claude.popup.divider_positions', return_value=[])
    def test_status_error_truncated_in_live_mode(self, _mock_div, _mock_tu, _mock_ep):
        """Error messages are truncated to 120 characters in live mode."""
        usage = {'five_hour': {'utilization': 50, 'resets_at': '2026-01-01T05:00:00Z'}}
        long_error = 'x' * 200
        result = _snapshot_to_dict(
            _snap(usage=usage, last_error=long_error),
            installations=[],
        )
        self.assertEqual(len(result['status']['error']), 120)

    # -- top-level dict structure --

    def test_all_top_level_keys_present(self):
        """Result always has profile, usage, extra, installations, status."""
        result = _snapshot_to_dict(_snap(), installations=[])
        self.assertEqual(set(result.keys()), {'profile', 'usage', 'extra', 'installations', 'status'})


# ---------------------------------------------------------------------------
# _init_config
# ---------------------------------------------------------------------------

class TestInitConfig(unittest.TestCase):
    """Tests for _init_config - builds the JS init() config object."""

    def test_top_level_keys(self):
        """Config has colors, t (translations), app_version, compact_hide, and data."""
        config = _init_config(_snap())
        self.assertEqual(set(config.keys()), {'colors', 't', 'app_version', 'compact_hide', 'data'})

    @patch('usage_monitor_for_claude.popup.COMPACT_HIDE', ['account', 'seven_day_opus'])
    def test_compact_hide_from_settings(self):
        """compact_hide is taken from the COMPACT_HIDE setting."""
        config = _init_config(_snap())
        self.assertEqual(config['compact_hide'], ['account', 'seven_day_opus'])

    def test_colors_from_settings(self):
        """Color values come from settings module constants."""
        from usage_monitor_for_claude.settings import BAR_BG, BAR_DIVIDER, BAR_FG, BAR_FG_WARN, BAR_MARKER, BG, FG, FG_DIM, FG_HEADING, FG_LINK

        config = _init_config(_snap())
        colors = config['colors']
        self.assertEqual(colors['bg'], BG)
        self.assertEqual(colors['fg'], FG)
        self.assertEqual(colors['fg_dim'], FG_DIM)
        self.assertEqual(colors['fg_heading'], FG_HEADING)
        self.assertEqual(colors['fg_link'], FG_LINK)
        self.assertEqual(colors['bar_bg'], BAR_BG)
        self.assertEqual(colors['bar_fg'], BAR_FG)
        self.assertEqual(colors['bar_fg_warn'], BAR_FG_WARN)
        self.assertEqual(colors['bar_divider'], BAR_DIVIDER)
        self.assertEqual(colors['bar_marker'], BAR_MARKER)

    def test_translations_from_i18n(self):
        """Translation values come from the T dict."""
        from usage_monitor_for_claude.i18n import T

        config = _init_config(_snap())
        t = config['t']
        self.assertEqual(t['title'], T['popup_title'])
        self.assertEqual(t['account'], T['account'])
        self.assertEqual(t['email'], T['email'])
        self.assertEqual(t['plan'], T['plan'])
        self.assertEqual(t['usage'], T['usage'])
        self.assertEqual(t['extra_usage'], T['extra_usage'])
        self.assertEqual(t['claude_code'], T['claude_code'])
        self.assertEqual(t['changelog'], T['changelog'])
        self.assertEqual(t['pin_popup'], T['pin_popup'])
        self.assertEqual(t['unpin_popup'], T['unpin_popup'])
        self.assertEqual(t['status_updated_s'], T['status_updated_s'])
        self.assertEqual(t['status_updated'], T['status_updated'])
        self.assertEqual(t['status_refreshing'], T['status_refreshing'])
        self.assertEqual(t['status_next_update'], T['status_next_update'])
        self.assertEqual(t['duration_hm'], T['duration_hm'])
        self.assertEqual(t['duration_m'], T['duration_m'])
        self.assertEqual(t['duration_s'], T['duration_s'])

    def test_app_version(self):
        """app_version matches the package version."""
        from usage_monitor_for_claude import __version__

        config = _init_config(_snap())
        self.assertEqual(config['app_version'], __version__)

    def test_data_is_snapshot_to_dict_output(self):
        """The data key contains the output of _snapshot_to_dict."""
        snap = _snap(profile={'account': {'email': 'a@b.com'}, 'organization': {}})
        config = _init_config(snap)
        self.assertEqual(config['data']['profile']['email'], 'a@b.com')
        self.assertEqual(set(config['data'].keys()), {'profile', 'usage', 'extra', 'installations', 'status'})


# ---------------------------------------------------------------------------
# Pin state
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Height reporting and reveal
# ---------------------------------------------------------------------------

class TestReportHeight(unittest.TestCase):
    """Tests for _PopupApi.report_height - the first report must always reveal the window."""

    def _build_popup(self):
        """Run the real UsagePopup.__init__ with webview and the host mocked.

        __init__ blocks on _closed.wait(), so it runs on a worker thread; the
        _PopupApi instance is captured from the js_api argument passed to
        webview.create_window.
        """
        patcher_watch = patch.object(UsagePopup, '_dismiss_watch', lambda self: None)
        patcher_webview = patch('usage_monitor_for_claude.popup.webview')
        patcher_host = patch('usage_monitor_for_claude.popup.PopupHost')
        patcher_watch.start()
        mock_webview = patcher_webview.start()
        patcher_host.start()
        self.addCleanup(patcher_host.stop)
        self.addCleanup(patcher_webview.stop)
        self.addCleanup(patcher_watch.stop)

        app = MagicMock()
        thread = threading.Thread(target=lambda: UsagePopup(app), daemon=True)
        thread.start()

        deadline = time.time() + 2.0
        while not mock_webview.create_window.called and time.time() < deadline:
            time.sleep(0.01)
        self.assertTrue(mock_webview.create_window.called)

        api = mock_webview.create_window.call_args.kwargs['js_api']
        popup = api._popup
        self.addCleanup(popup._closed.set)

        return popup, api

    def test_first_report_at_initial_window_height_reveals_popup(self):
        """A first content height equal to the initial window height must still reveal the window."""
        popup, api = self._build_popup()

        api.report_height(UsagePopup._INITIAL_HEIGHT)

        popup._host.apply_geometry.assert_called_once_with(UsagePopup._INITIAL_HEIGHT, keep_position=False)
        popup._host.reveal.assert_called_once()

    def test_first_report_at_other_height_reveals_popup(self):
        """A first content height different from the window height reveals the window."""
        popup, api = self._build_popup()

        api.report_height(523)

        popup._host.apply_geometry.assert_called_once_with(523, keep_position=False)
        popup._host.reveal.assert_called_once()

    def test_repeated_height_is_ignored(self):
        """An unchanged height must not resize or reveal again."""
        popup, api = self._build_popup()

        api.report_height(523)
        api.report_height(523)

        popup._host.apply_geometry.assert_called_once()
        popup._host.reveal.assert_called_once()

    def test_changed_height_resizes_without_revealing_again(self):
        """A later height change resizes but does not restart the reveal path."""
        popup, api = self._build_popup()

        api.report_height(523)
        api.report_height(610)

        self.assertEqual(popup._host.apply_geometry.call_count, 2)
        popup._host.reveal.assert_called_once()

    def test_zero_height_is_ignored(self):
        """A zero height carries no layout information."""
        popup, api = self._build_popup()

        api.report_height(0)

        popup._host.apply_geometry.assert_not_called()
        popup._host.reveal.assert_not_called()

    def test_moved_pinned_popup_keeps_its_position(self):
        """A pinned popup the user dragged must not snap back to the tray."""
        popup, api = self._build_popup()
        api.report_height(400)
        popup._pinned = True
        popup._moved_while_pinned = True

        api.report_height(500)

        self.assertTrue(popup._host.apply_geometry.call_args.kwargs['keep_position'])

    def test_pinned_popup_that_was_not_moved_still_anchors(self):
        """Pinning alone does not detach the popup from the tray anchor."""
        popup, api = self._build_popup()
        api.report_height(400)
        popup._pinned = True

        api.report_height(500)

        self.assertFalse(popup._host.apply_geometry.call_args.kwargs['keep_position'])


# ---------------------------------------------------------------------------
# Dismiss-watch shutdown
# ---------------------------------------------------------------------------

class TestDismissWatchShutdown(unittest.TestCase):
    """Tests that closing the popup terminates the platform host's dismiss watch.

    The Windows host installs system-wide input hooks and only removes them
    when its message pump exits.  Closing the window must wake the watch in
    every state - especially while pinned, where the user-dismissal path
    never fires.
    """

    def _start_watch(self, pinned):
        """Build a minimal popup whose host blocks until stop_watch() is called."""
        released = threading.Event()

        host = MagicMock()
        host.watch_dismiss.side_effect = lambda should_dismiss, is_running: released.wait(2)
        host.stop_watch.side_effect = released.set

        popup = object.__new__(UsagePopup)
        popup._running = True
        popup._pinned = pinned
        popup._shown = True
        popup._closed = threading.Event()
        popup._window = MagicMock()
        popup._host = host

        thread = threading.Thread(target=popup._dismiss_watch, daemon=True)
        thread.start()

        deadline = time.time() + 1.0
        while not host.watch_dismiss.called and time.time() < deadline:
            time.sleep(0.01)

        return popup, thread

    def test_close_while_pinned_ends_watch(self):
        """_close() on a pinned popup must end the watch so hooks are removed."""
        popup, thread = self._start_watch(pinned=True)
        popup._close()
        thread.join(timeout=2)
        self.assertFalse(thread.is_alive())

    def test_close_while_unpinned_ends_watch(self):
        """_close() must end the watch immediately, not on the next outside click."""
        popup, thread = self._start_watch(pinned=False)
        popup._close()
        thread.join(timeout=2)
        self.assertFalse(thread.is_alive())

    def test_window_closed_event_ends_watch(self):
        """The pywebview closed event must end the watch even while pinned."""
        popup, thread = self._start_watch(pinned=True)
        popup._on_window_closed()
        thread.join(timeout=2)
        self.assertFalse(thread.is_alive())

    def test_watch_return_closes_the_popup(self):
        """When the host reports a dismissal, the popup closes itself."""
        popup, thread = self._start_watch(pinned=False)
        popup._host.stop_watch()
        thread.join(timeout=2)
        self.assertTrue(popup._closed.is_set())


class TestShouldDismiss(unittest.TestCase):
    """Tests for the predicate the platform host asks before dismissing."""

    def _popup(self, shown, pinned):
        popup = object.__new__(UsagePopup)
        popup._shown = shown
        popup._pinned = pinned
        return popup

    def test_shown_and_unpinned_dismisses(self):
        self.assertTrue(self._popup(shown=True, pinned=False)._should_dismiss())

    def test_pinned_does_not_dismiss(self):
        """A pinned popup stays open until the user unpins or closes it."""
        self.assertFalse(self._popup(shown=True, pinned=True)._should_dismiss())

    def test_not_yet_shown_does_not_dismiss(self):
        """A window still being measured must not be closed by a stray event."""
        self.assertFalse(self._popup(shown=False, pinned=False)._should_dismiss())


# ---------------------------------------------------------------------------
# Pin state
# ---------------------------------------------------------------------------

class TestPinState(unittest.TestCase):
    """Tests for pinning and the drag it enables."""

    def _popup(self, pinned=False):
        popup = object.__new__(UsagePopup)
        popup._pinned = pinned
        popup._moved_while_pinned = False
        popup._last_height = 500
        popup._geometry_lock = threading.Lock()
        popup._host = MagicMock()
        return popup

    def test_set_pinned_reports_what_was_applied(self):
        """popup.js assigns the result back to its own state."""
        popup = self._popup()
        self.assertTrue(popup._set_pinned(True))
        self.assertFalse(popup._set_pinned(False))

    def test_unpinning_clears_the_moved_flag(self):
        """After unpinning, the popup anchors to the tray again."""
        popup = self._popup(pinned=True)
        popup._moved_while_pinned = True
        popup._set_pinned(False)
        self.assertFalse(popup._moved_while_pinned)

    def test_drag_requires_pinning(self):
        """An unpinned popup is positioned by the app, not the user."""
        popup = self._popup(pinned=False)
        self.assertFalse(popup._begin_drag())
        self.assertFalse(popup._drag())
        popup._host.begin_drag.assert_not_called()

    def test_begin_drag_delegates_to_host(self):
        popup = self._popup(pinned=True)
        popup._host.begin_drag.return_value = True
        self.assertTrue(popup._begin_drag())

    def test_drag_marks_the_popup_as_moved(self):
        """A successful drag detaches the popup from the tray anchor."""
        popup = self._popup(pinned=True)
        popup._host.drag.return_value = True
        self.assertTrue(popup._drag())
        self.assertTrue(popup._moved_while_pinned)

    def test_refused_drag_does_not_mark_as_moved(self):
        """A drag the host refused must not detach the anchor."""
        popup = self._popup(pinned=True)
        popup._host.drag.return_value = False
        self.assertFalse(popup._drag())
        self.assertFalse(popup._moved_while_pinned)

    def test_end_drag_passes_the_current_height(self):
        """The host may need the height to re-assert the size after the drag."""
        popup = self._popup(pinned=True)
        popup._end_drag()
        popup._host.end_drag.assert_called_once_with(500)


# ---------------------------------------------------------------------------
# _update_loop resilience
# ---------------------------------------------------------------------------

class TestUpdateLoopResilience(unittest.TestCase):
    """Tests that a transient failure does not end the popup's update stream."""

    def test_transient_failure_does_not_end_update_loop(self):
        """One failing evaluate_js (or snapshot conversion) must not stop updates -
        a pinned popup can live for days and would show stale bars forever."""
        popup = object.__new__(UsagePopup)
        popup._running = True
        popup._last_version = 0
        popup._window = MagicMock()

        class FakeCache:
            def __init__(self):
                self.version_counter = 0

            @property
            def snapshot(self):
                self.version_counter += 1
                snap = MagicMock()
                snap.version = self.version_counter
                return snap

        popup.app = MagicMock()
        popup.app.cache = FakeCache()
        popup.app._next_poll_time = 100.0

        def eval_js(_script):
            if popup._window.evaluate_js.call_count == 1:
                raise RuntimeError('transient WebView2 hiccup')
            popup._running = False

        popup._window.evaluate_js.side_effect = eval_js

        iterations = [0]

        def guarded_sleep(_seconds):
            iterations[0] += 1
            if iterations[0] > 10:
                popup._running = False

        with patch('usage_monitor_for_claude.popup.time.sleep', side_effect=guarded_sleep), \
             patch('usage_monitor_for_claude.popup.find_installations', return_value=[]), \
             patch('usage_monitor_for_claude.popup._snapshot_to_dict', return_value={}):
            popup._update_loop()

        self.assertEqual(popup._window.evaluate_js.call_count, 2)

    def test_failed_update_is_retried_on_next_tick(self):
        """An update that failed to push is retried even when the data did not
        change again - the version marker advances only on success."""
        popup = object.__new__(UsagePopup)
        popup._running = True
        popup._last_version = 0
        popup._window = MagicMock()

        snap = MagicMock()
        snap.version = 1
        popup.app = MagicMock()
        popup.app.cache.snapshot = snap
        popup.app._next_poll_time = 100.0

        def eval_js(_script):
            if popup._window.evaluate_js.call_count == 1:
                raise RuntimeError('transient WebView2 hiccup')
            popup._running = False

        popup._window.evaluate_js.side_effect = eval_js

        iterations = [0]

        def guarded_sleep(_seconds):
            iterations[0] += 1
            if iterations[0] > 10:
                popup._running = False

        with patch('usage_monitor_for_claude.popup.time.sleep', side_effect=guarded_sleep), \
             patch('usage_monitor_for_claude.popup.find_installations', return_value=[]), \
             patch('usage_monitor_for_claude.popup._snapshot_to_dict', return_value={}):
            popup._update_loop()

        self.assertEqual(popup._window.evaluate_js.call_count, 2)
        self.assertEqual(popup._last_version, 1)


# ---------------------------------------------------------------------------
# _tray_position
# ---------------------------------------------------------------------------

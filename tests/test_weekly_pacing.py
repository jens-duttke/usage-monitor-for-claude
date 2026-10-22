"""Optional equal weekly sections preserve default calendar pacing."""
from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from usage_monitor_for_claude import formatting, settings


class TestWeeklyPacing(unittest.TestCase):
    def test_equal_sections(self) -> None:
        reset = '2030-06-05T14:37:00+00:00'
        self.assertEqual(formatting.divider_positions(reset, 604800, 5), [.2, .4, .6, .8])
        self.assertEqual(formatting.divider_positions(reset, 604800, 1), [])
        self.assertEqual(len(formatting.divider_positions(reset, 604800, 31)), 30)

    def test_defaults_and_other_durations_unchanged(self) -> None:
        reset = '2030-06-05T14:37:00+00:00'
        with patch.object(formatting, 'WEEKLY_BAR_PARTS', None):
            original_week = formatting.divider_positions(reset, 604800)
            original_day = formatting.divider_positions(reset, 86400)
            original_session = formatting.divider_positions(reset, 18000)
        with patch.object(formatting, 'WEEKLY_BAR_PARTS', 5):
            self.assertEqual(formatting.divider_positions(reset, 86400), original_day)
            self.assertEqual(formatting.divider_positions(reset, 18000), original_session)
        self.assertNotEqual(original_week, [.2, .4, .6, .8])
        self.assertEqual(formatting.divider_positions('', 604800, 5), [])
        self.assertEqual(formatting.divider_positions('invalid', 604800, 5), [])

    def test_settings_validation(self) -> None:
        with patch.object(settings, 'show_warning_box'):
            for value in (0, 32, True, '5', None, 2.5):
                with self.subTest(value=value):
                    self.assertNotIn('weekly_bar_parts', settings._validate({'weekly_bar_parts': value}, Path('synthetic.json')))
            for value in (1, 5, 31):
                self.assertEqual(settings._validate({'weekly_bar_parts': value}, Path('synthetic.json'))['weekly_bar_parts'], value)

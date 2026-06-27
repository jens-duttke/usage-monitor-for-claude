"""
Settings Tests
================

Unit tests for settings file loading and settings constant overrides.
"""
from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch

import usage_monitor_for_claude.settings as settings_mod


def _load(app_dir: Path, home_dir: Path) -> dict:
    """Call _load_settings with controlled app_dir and home_dir."""
    fake_file = str(app_dir / 'usage_monitor_for_claude' / 'settings.py')
    with patch.object(settings_mod, '__file__', fake_file), \
         patch.object(Path, 'home', return_value=home_dir), \
         patch.object(settings_mod, 'ctypes', MagicMock()):
        return settings_mod._load_settings()


class TestLoadSettings(unittest.TestCase):
    """Tests for _load_settings() file discovery and parsing."""

    def test_no_file_returns_empty_dict(self):
        """Missing settings file in both locations returns empty dict."""
        with TemporaryDirectory() as app_tmp, TemporaryDirectory() as home_tmp:
            result = _load(Path(app_tmp), Path(home_tmp))
        self.assertEqual(result, {})

    def test_app_dir_file_loaded(self):
        """Settings file next to the app is found and loaded."""
        with TemporaryDirectory() as app_tmp, TemporaryDirectory() as home_tmp:
            settings = {'poll_interval': 300}
            (Path(app_tmp) / settings_mod.SETTINGS_FILENAME).write_text(json.dumps(settings), encoding='utf-8')
            result = _load(Path(app_tmp), Path(home_tmp))
        self.assertEqual(result, settings)

    def test_home_dir_fallback(self):
        """Falls back to ~/.claude/ when no file next to app."""
        with TemporaryDirectory() as app_tmp, TemporaryDirectory() as home_tmp:
            claude_dir = Path(home_tmp) / '.claude'
            claude_dir.mkdir()
            settings = {'bg': '#000000'}
            (claude_dir / settings_mod.SETTINGS_FILENAME).write_text(json.dumps(settings), encoding='utf-8')
            result = _load(Path(app_tmp), Path(home_tmp))
        self.assertEqual(result, settings)

    def test_custom_config_dir_fallback(self):
        """Falls back to CLAUDE_CONFIG_DIR when no file next to app."""
        with TemporaryDirectory() as app_tmp, TemporaryDirectory() as config_tmp:
            config_dir = Path(config_tmp)
            settings = {'bg': '#111111'}
            (config_dir / settings_mod.SETTINGS_FILENAME).write_text(json.dumps(settings), encoding='utf-8')
            fake_file = str(Path(app_tmp) / 'usage_monitor_for_claude' / 'settings.py')
            with patch.object(settings_mod, '__file__', fake_file), \
                 patch.dict('os.environ', {'CLAUDE_CONFIG_DIR': config_tmp}), \
                 patch.object(settings_mod, 'ctypes', MagicMock()):
                result = settings_mod._load_settings()
        self.assertEqual(result, settings)

    def test_home_claude_fallback_with_custom_config_dir(self):
        """Falls back to ~/.claude/ when CLAUDE_CONFIG_DIR is set but has no settings file."""
        with TemporaryDirectory() as app_tmp, TemporaryDirectory() as home_tmp, TemporaryDirectory() as config_tmp:
            claude_dir = Path(home_tmp) / '.claude'
            claude_dir.mkdir()
            settings = {'bg': '#222222'}
            (claude_dir / settings_mod.SETTINGS_FILENAME).write_text(json.dumps(settings), encoding='utf-8')
            fake_file = str(Path(app_tmp) / 'usage_monitor_for_claude' / 'settings.py')
            with patch.object(settings_mod, '__file__', fake_file), \
                 patch.object(Path, 'home', return_value=Path(home_tmp)), \
                 patch.dict('os.environ', {'CLAUDE_CONFIG_DIR': config_tmp}), \
                 patch.object(settings_mod, 'ctypes', MagicMock()):
                result = settings_mod._load_settings()
        self.assertEqual(result, settings)

    def test_custom_config_dir_wins_over_home_claude(self):
        """CLAUDE_CONFIG_DIR settings file takes priority over ~/.claude/."""
        with TemporaryDirectory() as app_tmp, TemporaryDirectory() as home_tmp, TemporaryDirectory() as config_tmp:
            claude_dir = Path(home_tmp) / '.claude'
            claude_dir.mkdir()
            (claude_dir / settings_mod.SETTINGS_FILENAME).write_text(json.dumps({'bg': '#home'}), encoding='utf-8')
            (Path(config_tmp) / settings_mod.SETTINGS_FILENAME).write_text(json.dumps({'bg': '#custom'}), encoding='utf-8')
            fake_file = str(Path(app_tmp) / 'usage_monitor_for_claude' / 'settings.py')
            with patch.object(settings_mod, '__file__', fake_file), \
                 patch.object(Path, 'home', return_value=Path(home_tmp)), \
                 patch.dict('os.environ', {'CLAUDE_CONFIG_DIR': config_tmp}), \
                 patch.object(settings_mod, 'ctypes', MagicMock()):
                result = settings_mod._load_settings()
        self.assertEqual(result['bg'], '#custom')

    def test_config_dir_same_as_home_claude_no_duplicate(self):
        """When CLAUDE_CONFIG_DIR equals ~/.claude/, the path is searched only once."""
        with TemporaryDirectory() as app_tmp, TemporaryDirectory() as home_tmp:
            claude_dir = Path(home_tmp) / '.claude'
            claude_dir.mkdir()
            settings = {'bg': '#333333'}
            (claude_dir / settings_mod.SETTINGS_FILENAME).write_text(json.dumps(settings), encoding='utf-8')
            fake_file = str(Path(app_tmp) / 'usage_monitor_for_claude' / 'settings.py')
            with patch.object(settings_mod, '__file__', fake_file), \
                 patch.object(Path, 'home', return_value=Path(home_tmp)), \
                 patch.dict('os.environ', {'CLAUDE_CONFIG_DIR': str(claude_dir)}), \
                 patch.object(settings_mod, 'ctypes', MagicMock()):
                result = settings_mod._load_settings()
        self.assertEqual(result, settings)

    def test_app_dir_takes_priority(self):
        """File next to app wins over ~/.claude/ file."""
        with TemporaryDirectory() as app_tmp, TemporaryDirectory() as home_tmp:
            app_settings = {'poll_interval': 60}
            home_settings = {'poll_interval': 300}
            (Path(app_tmp) / settings_mod.SETTINGS_FILENAME).write_text(json.dumps(app_settings), encoding='utf-8')
            claude_dir = Path(home_tmp) / '.claude'
            claude_dir.mkdir()
            (claude_dir / settings_mod.SETTINGS_FILENAME).write_text(json.dumps(home_settings), encoding='utf-8')
            result = _load(Path(app_tmp), Path(home_tmp))
        self.assertEqual(result['poll_interval'], 60)

    def test_empty_json_object(self):
        """An empty JSON object is valid and returns empty dict."""
        with TemporaryDirectory() as app_tmp, TemporaryDirectory() as home_tmp:
            (Path(app_tmp) / settings_mod.SETTINGS_FILENAME).write_text('{}', encoding='utf-8')
            result = _load(Path(app_tmp), Path(home_tmp))
        self.assertEqual(result, {})

    def test_empty_file_returns_empty_dict(self):
        """A completely empty file is treated as no settings."""
        with TemporaryDirectory() as app_tmp, TemporaryDirectory() as home_tmp:
            (Path(app_tmp) / settings_mod.SETTINGS_FILENAME).write_text('', encoding='utf-8')
            result = _load(Path(app_tmp), Path(home_tmp))
        self.assertEqual(result, {})

    def test_whitespace_only_file_returns_empty_dict(self):
        """A file with only whitespace is treated as no settings."""
        with TemporaryDirectory() as app_tmp, TemporaryDirectory() as home_tmp:
            (Path(app_tmp) / settings_mod.SETTINGS_FILENAME).write_text('  \n\t\n  ', encoding='utf-8')
            result = _load(Path(app_tmp), Path(home_tmp))
        self.assertEqual(result, {})

    def test_invalid_json_returns_empty_dict(self):
        """Malformed JSON shows error and returns empty dict."""
        with TemporaryDirectory() as app_tmp, TemporaryDirectory() as home_tmp:
            (Path(app_tmp) / settings_mod.SETTINGS_FILENAME).write_text('{broken', encoding='utf-8')
            result = _load(Path(app_tmp), Path(home_tmp))
        self.assertEqual(result, {})

    def test_invalid_json_shows_message_box(self):
        """Malformed JSON triggers a Windows MessageBox."""
        with TemporaryDirectory() as app_tmp, TemporaryDirectory() as home_tmp:
            (Path(app_tmp) / settings_mod.SETTINGS_FILENAME).write_text('{broken', encoding='utf-8')
            fake_file = str(Path(app_tmp) / 'usage_monitor_for_claude' / 'settings.py')
            mock_ctypes = MagicMock()
            with patch.object(settings_mod, '__file__', fake_file), \
                 patch.object(Path, 'home', return_value=Path(home_tmp)), \
                 patch.object(settings_mod, 'ctypes', mock_ctypes):
                settings_mod._load_settings()
            mock_ctypes.windll.user32.MessageBoxW.assert_called_once()

    def test_json_array_returns_empty_dict(self):
        """JSON root that is not an object shows error and returns empty dict."""
        with TemporaryDirectory() as app_tmp, TemporaryDirectory() as home_tmp:
            (Path(app_tmp) / settings_mod.SETTINGS_FILENAME).write_text('[1, 2, 3]', encoding='utf-8')
            result = _load(Path(app_tmp), Path(home_tmp))
        self.assertEqual(result, {})

    def test_json_string_returns_empty_dict(self):
        """JSON root that is a string shows error and returns empty dict."""
        with TemporaryDirectory() as app_tmp, TemporaryDirectory() as home_tmp:
            (Path(app_tmp) / settings_mod.SETTINGS_FILENAME).write_text('"hello"', encoding='utf-8')
            result = _load(Path(app_tmp), Path(home_tmp))
        self.assertEqual(result, {})

    def test_unreadable_file_returns_empty_dict(self):
        """File that cannot be read returns empty dict."""
        with TemporaryDirectory() as app_tmp, TemporaryDirectory() as home_tmp:
            fake_file = str(Path(app_tmp) / 'usage_monitor_for_claude' / 'settings.py')
            with patch.object(settings_mod, '__file__', fake_file), \
                 patch.object(Path, 'home', return_value=Path(home_tmp)), \
                 patch.object(settings_mod, 'ctypes', MagicMock()), \
                 patch.object(Path, 'is_file', return_value=True), \
                 patch.object(Path, 'read_text', side_effect=PermissionError('access denied')):
                result = settings_mod._load_settings()
        self.assertEqual(result, {})

    def test_frozen_uses_executable_dir(self):
        """When frozen, looks next to sys.executable."""
        with TemporaryDirectory() as exe_tmp, TemporaryDirectory() as home_tmp:
            settings = {'poll_error': 10}
            (Path(exe_tmp) / settings_mod.SETTINGS_FILENAME).write_text(json.dumps(settings), encoding='utf-8')
            with patch.object(settings_mod.sys, 'frozen', True, create=True), \
                 patch.object(settings_mod.sys, 'executable', str(Path(exe_tmp) / 'app.exe')), \
                 patch.object(Path, 'home', return_value=Path(home_tmp)), \
                 patch.object(settings_mod, 'ctypes', MagicMock()):
                result = settings_mod._load_settings()
        self.assertEqual(result, settings)

    def test_invalid_value_type_dropped_during_load(self):
        """Invalid value types are dropped during loading, MessageBox shown."""
        with TemporaryDirectory() as app_tmp, TemporaryDirectory() as home_tmp:
            settings = {'poll_interval': 'not_a_number', 'poll_fast': 30}
            (Path(app_tmp) / settings_mod.SETTINGS_FILENAME).write_text(json.dumps(settings), encoding='utf-8')
            fake_file = str(Path(app_tmp) / 'usage_monitor_for_claude' / 'settings.py')
            mock_ctypes = MagicMock()
            with patch.object(settings_mod, '__file__', fake_file), \
                 patch.object(Path, 'home', return_value=Path(home_tmp)), \
                 patch.object(settings_mod, 'ctypes', mock_ctypes):
                result = settings_mod._load_settings()
            self.assertNotIn('poll_interval', result)
            self.assertEqual(result['poll_fast'], 30)
            mock_ctypes.windll.user32.MessageBoxW.assert_called_once()


class TestSettingsOverrides(unittest.TestCase):
    """Tests that settings values properly override default constants."""

    def test_unknown_keys_ignored(self):
        """Unknown keys in settings are silently ignored, overrides still applied."""
        settings = {'unknown_key': 'value', 'poll_interval': 90}
        self._assert_overrides(settings, [('poll_interval', 90)], absent=['poll_fast'])

    def test_polling_overrides(self):
        """Polling constants are overridden by settings."""
        settings = {'poll_interval': 300, 'poll_fast': 30, 'poll_fast_extra': 5, 'poll_error': 10}
        self._assert_overrides(settings, [
            ('poll_interval', 300), ('poll_fast', 30), ('poll_fast_extra', 5), ('poll_error', 10),
        ])

    def test_popup_color_overrides(self):
        """Popup color constants are overridden by settings."""
        settings = {'bg': '#000000', 'fg': '#ffffff', 'bar_fg': '#00ff00'}
        self._assert_overrides(settings, [('bg', '#000000'), ('fg', '#ffffff'), ('bar_fg', '#00ff00')])

    def test_partial_override_keeps_defaults(self):
        """Overriding one key does not affect other keys."""
        settings = {'poll_interval': 300}
        self._assert_overrides(settings, [('poll_interval', 300)], absent=['poll_fast', 'bg', 'alert_thresholds_extra_usage'])

    def test_threshold_overrides(self):
        """Alert threshold lists are overridden by settings."""
        settings = {'alert_thresholds_extra_usage': [70, 90], 'alert_thresholds_five_hour': [80]}
        self._assert_overrides(settings, [
            ('alert_thresholds_extra_usage', [70, 90]),
            ('alert_thresholds_five_hour', [80]),
        ], absent=['alert_thresholds_seven_day'])

    def test_icon_color_override(self):
        """Icon color dicts are merged, JSON arrays become tuples."""
        settings = {'icon_light': {'fg': [0, 255, 0, 255]}}
        with TemporaryDirectory() as app_tmp, TemporaryDirectory() as home_tmp:
            (Path(app_tmp) / settings_mod.SETTINGS_FILENAME).write_text(json.dumps(settings), encoding='utf-8')
            loaded = _load(Path(app_tmp), Path(home_tmp))

        original_S = settings_mod._S
        try:
            settings_mod._S = loaded
            icon_light = settings_mod._icon_colors('icon_light', {
                'fg': (255, 255, 255, 255), 'fg_half': (255, 255, 255, 80), 'fg_dim': (255, 255, 255, 140),
            })
        finally:
            settings_mod._S = original_S

        self.assertEqual(icon_light['fg'], (0, 255, 0, 255))
        self.assertEqual(icon_light['fg_half'], (255, 255, 255, 80))
        self.assertEqual(icon_light['fg_dim'], (255, 255, 255, 140))

    def _assert_overrides(self, settings: dict, expected: list[tuple[str, object]], absent: list[str] | None = None) -> None:
        """Load settings and verify overridden keys have expected values.

        Parameters
        ----------
        settings : dict
            Raw settings to write to the JSON file.
        expected : list of (key, value) tuples
            Keys that should be present in the loaded dict with exact values.
        absent : list of str or None
            Keys that should NOT be present (proving they weren't touched).
        """
        with TemporaryDirectory() as app_tmp, TemporaryDirectory() as home_tmp:
            (Path(app_tmp) / settings_mod.SETTINGS_FILENAME).write_text(json.dumps(settings), encoding='utf-8')
            loaded = _load(Path(app_tmp), Path(home_tmp))

        for key, value in expected:
            self.assertIn(key, loaded, f'{key} should be in loaded settings')
            self.assertEqual(loaded[key], value, f'{key} should be {value!r}, got {loaded[key]!r}')

        for key in (absent or []):
            self.assertNotIn(key, loaded, f'{key} should not be in loaded settings')


class TestSettingsValidation(unittest.TestCase):
    """Tests that invalid setting values are rejected with a MessageBox."""

    def test_valid_settings_no_message_box(self):
        """Valid settings pass through without MessageBox."""
        data = {'poll_interval': 300, 'bg': '#000', 'icon_light': {'fg': [0, 255, 0, 255]}}
        result, mock = self._run_validate(data)
        self.assertEqual(result, data)
        mock.windll.user32.MessageBoxW.assert_not_called()

    def test_string_for_numeric_key(self):
        """String value for numeric key is dropped."""
        result, mock = self._run_validate({'poll_interval': 'abc'})
        self.assertNotIn('poll_interval', result)
        mock.windll.user32.MessageBoxW.assert_called_once()

    def test_bool_for_numeric_key(self):
        """Boolean for numeric key is dropped (bool is subclass of int)."""
        result, _ = self._run_validate({'poll_fast': True})
        self.assertNotIn('poll_fast', result)

    def test_negative_numeric_value(self):
        """Negative numeric value is dropped."""
        result, _ = self._run_validate({'poll_error': -5})
        self.assertNotIn('poll_error', result)

    def test_zero_numeric_value(self):
        """Zero numeric value is dropped (must be > 0)."""
        result, _ = self._run_validate({'poll_interval': 0})
        self.assertNotIn('poll_interval', result)

    def test_float_numeric_value_dropped(self):
        """Float values are dropped for numeric keys (integers only)."""
        result, mock = self._run_validate({'poll_interval': 120.5})
        self.assertNotIn('poll_interval', result)
        mock.windll.user32.MessageBoxW.assert_called_once()

    def test_non_string_color(self):
        """Non-string value for color key is dropped."""
        result, _ = self._run_validate({'bg': 42})
        self.assertNotIn('bg', result)

    def test_non_dict_icon(self):
        """Non-dict value for icon key is dropped."""
        result, _ = self._run_validate({'icon_light': 'invalid'})
        self.assertNotIn('icon_light', result)

    def test_icon_invalid_rgba_length(self):
        """Icon color with wrong array length is dropped."""
        result, _ = self._run_validate({'icon_light': {'fg': [255, 255]}})
        self.assertNotIn('fg', result['icon_light'])

    def test_icon_rgba_out_of_range(self):
        """Icon color with value > 255 is dropped."""
        result, _ = self._run_validate({'icon_dark': {'fg': [0, 256, 0, 255]}})
        self.assertNotIn('fg', result['icon_dark'])

    def test_icon_rgba_negative(self):
        """Icon color with negative value is dropped."""
        result, _ = self._run_validate({'icon_dark': {'fg': [0, -1, 0, 255]}})
        self.assertNotIn('fg', result['icon_dark'])

    def test_icon_rgba_with_float(self):
        """Icon color with float values is dropped (must be int)."""
        result, _ = self._run_validate({'icon_light': {'fg': [0.0, 255, 0, 255]}})
        self.assertNotIn('fg', result['icon_light'])

    def test_icon_rgba_with_bool(self):
        """Icon color with boolean values is dropped."""
        result, _ = self._run_validate({'icon_light': {'fg': [True, 0, 0, 255]}})
        self.assertNotIn('fg', result['icon_light'])

    def test_icon_valid_and_invalid_mixed(self):
        """Valid icon sub-entries kept, invalid ones dropped."""
        data = {'icon_light': {'fg': [0, 255, 0, 255], 'fg_half': [255, 255]}}
        result, _ = self._run_validate(data)
        self.assertEqual(result['icon_light']['fg'], [0, 255, 0, 255])
        self.assertNotIn('fg_half', result['icon_light'])

    def test_unknown_keys_pass_through(self):
        """Unknown keys are not validated or removed."""
        result, mock = self._run_validate({'custom_key': [1, 2, 3]})
        self.assertEqual(result['custom_key'], [1, 2, 3])
        mock.windll.user32.MessageBoxW.assert_not_called()

    def test_multiple_errors_single_message_box(self):
        """Multiple invalid values produce exactly one MessageBox."""
        result, mock = self._run_validate({'poll_interval': 'x', 'bg': 42, 'poll_fast': -1})
        mock.windll.user32.MessageBoxW.assert_called_once()
        self.assertEqual(result, {})

    def test_valid_kept_when_invalid_dropped(self):
        """Valid values are kept when invalid ones are dropped."""
        result, _ = self._run_validate({'poll_interval': 'bad', 'poll_fast': 60, 'bg': '#000'})
        self.assertNotIn('poll_interval', result)
        self.assertEqual(result['poll_fast'], 60)
        self.assertEqual(result['bg'], '#000')

    # time_format validation

    def test_time_format_24h_valid(self):
        """time_format '24h' passes through unchanged."""
        result, mock = self._run_validate({'time_format': '24h'})
        self.assertEqual(result['time_format'], '24h')
        mock.windll.user32.MessageBoxW.assert_not_called()

    def test_time_format_12h_valid(self):
        """time_format '12h' passes through unchanged."""
        result, mock = self._run_validate({'time_format': '12h'})
        self.assertEqual(result['time_format'], '12h')
        mock.windll.user32.MessageBoxW.assert_not_called()

    def test_time_format_unknown_value_dropped(self):
        """Unknown time_format value is dropped with a MessageBox."""
        result, mock = self._run_validate({'time_format': 'military'})
        self.assertNotIn('time_format', result)
        mock.windll.user32.MessageBoxW.assert_called_once()

    def test_time_format_non_string_dropped(self):
        """Non-string time_format value is dropped."""
        result, _ = self._run_validate({'time_format': 24})
        self.assertNotIn('time_format', result)

    # Non-negative numeric validation

    def test_idle_pause_zero_valid(self):
        """Value 0 for idle_pause is valid (disables idle detection)."""
        result, mock = self._run_validate({'idle_pause': 0})
        self.assertEqual(result['idle_pause'], 0)
        mock.windll.user32.MessageBoxW.assert_not_called()

    def test_idle_pause_positive_valid(self):
        """Positive value for idle_pause is valid."""
        result, mock = self._run_validate({'idle_pause': 600})
        self.assertEqual(result['idle_pause'], 600)
        mock.windll.user32.MessageBoxW.assert_not_called()

    def test_idle_pause_negative_dropped(self):
        """Negative value for idle_pause is dropped."""
        result, mock = self._run_validate({'idle_pause': -1})
        self.assertNotIn('idle_pause', result)
        mock.windll.user32.MessageBoxW.assert_called_once()

    def test_idle_pause_string_dropped(self):
        """String value for idle_pause is dropped."""
        result, mock = self._run_validate({'idle_pause': 'five'})
        self.assertNotIn('idle_pause', result)
        mock.windll.user32.MessageBoxW.assert_called_once()

    def test_idle_pause_bool_dropped(self):
        """Boolean for idle_pause is dropped."""
        result, _ = self._run_validate({'idle_pause': True})
        self.assertNotIn('idle_pause', result)

    def test_idle_pause_float_dropped(self):
        """Float value for idle_pause is dropped (integers only)."""
        result, mock = self._run_validate({'idle_pause': 120.5})
        self.assertNotIn('idle_pause', result)
        mock.windll.user32.MessageBoxW.assert_called_once()

    # Threshold array validation

    def test_valid_threshold_array(self):
        """Valid threshold array passes through without MessageBox."""
        result, mock = self._run_validate({'alert_thresholds_five_hour': [80, 95]})
        self.assertEqual(result['alert_thresholds_five_hour'], [80, 95])
        mock.windll.user32.MessageBoxW.assert_not_called()

    def test_threshold_array_sorted_and_deduped(self):
        """Threshold values are sorted and deduplicated."""
        result, _ = self._run_validate({'alert_thresholds_five_hour': [95, 80, 50, 80]})
        self.assertEqual(result['alert_thresholds_five_hour'], [50, 80, 95])

    def test_threshold_empty_array_valid(self):
        """Empty threshold array is valid (disables alerts)."""
        result, mock = self._run_validate({'alert_thresholds_five_hour': []})
        self.assertEqual(result['alert_thresholds_five_hour'], [])
        mock.windll.user32.MessageBoxW.assert_not_called()

    def test_threshold_not_array_dropped(self):
        """Non-array value for threshold key is dropped."""
        result, mock = self._run_validate({'alert_thresholds_five_hour': 80})
        self.assertNotIn('alert_thresholds_five_hour', result)
        mock.windll.user32.MessageBoxW.assert_called_once()

    def test_threshold_string_in_array_dropped(self):
        """String element in threshold array causes the key to be dropped."""
        result, mock = self._run_validate({'alert_thresholds_five_hour': [80, 'high']})
        self.assertNotIn('alert_thresholds_five_hour', result)
        mock.windll.user32.MessageBoxW.assert_called_once()

    def test_threshold_bool_in_array_dropped(self):
        """Boolean element in threshold array causes the key to be dropped."""
        result, _ = self._run_validate({'alert_thresholds_five_hour': [True, 80]})
        self.assertNotIn('alert_thresholds_five_hour', result)

    def test_threshold_zero_dropped(self):
        """Value 0 in threshold array causes the key to be dropped (must be 1-100)."""
        result, _ = self._run_validate({'alert_thresholds_five_hour': [0, 80]})
        self.assertNotIn('alert_thresholds_five_hour', result)

    def test_threshold_over_100_dropped(self):
        """Value > 100 in threshold array causes the key to be dropped."""
        result, _ = self._run_validate({'alert_thresholds_five_hour': [80, 101]})
        self.assertNotIn('alert_thresholds_five_hour', result)

    def test_threshold_float_valid(self):
        """Float values in threshold array are valid."""
        result, mock = self._run_validate({'alert_thresholds_five_hour': [80.5, 95.0]})
        self.assertEqual(result['alert_thresholds_five_hour'], [80.5, 95.0])
        mock.windll.user32.MessageBoxW.assert_not_called()

    def test_threshold_seven_day_key_validated(self):
        """Weekly threshold key is validated the same way."""
        result, mock = self._run_validate({'alert_thresholds_seven_day': [70, 90]})
        self.assertEqual(result['alert_thresholds_seven_day'], [70, 90])
        mock.windll.user32.MessageBoxW.assert_not_called()

    def test_threshold_per_variant_invalid_dropped(self):
        """Invalid per-variant threshold is dropped."""
        result, _ = self._run_validate({'alert_thresholds_seven_day': 'bad'})
        self.assertNotIn('alert_thresholds_seven_day', result)

    # Percent key validation

    def test_alert_time_aware_below_valid(self):
        """Valid number for alert_time_aware_below passes through."""
        result, mock = self._run_validate({'alert_time_aware_below': 90})
        self.assertEqual(result['alert_time_aware_below'], 90)
        mock.windll.user32.MessageBoxW.assert_not_called()

    def test_alert_time_aware_below_float_valid(self):
        """Float value for alert_time_aware_below is valid."""
        result, mock = self._run_validate({'alert_time_aware_below': 85.5})
        self.assertEqual(result['alert_time_aware_below'], 85.5)
        mock.windll.user32.MessageBoxW.assert_not_called()

    def test_alert_time_aware_below_zero_dropped(self):
        """Value 0 for alert_time_aware_below is dropped (must be 1-100)."""
        result, mock = self._run_validate({'alert_time_aware_below': 0})
        self.assertNotIn('alert_time_aware_below', result)
        mock.windll.user32.MessageBoxW.assert_called_once()

    def test_alert_time_aware_below_over_100_dropped(self):
        """Value > 100 for alert_time_aware_below is dropped."""
        result, mock = self._run_validate({'alert_time_aware_below': 101})
        self.assertNotIn('alert_time_aware_below', result)
        mock.windll.user32.MessageBoxW.assert_called_once()

    def test_alert_time_aware_below_string_dropped(self):
        """String value for alert_time_aware_below is dropped."""
        result, mock = self._run_validate({'alert_time_aware_below': '90'})
        self.assertNotIn('alert_time_aware_below', result)
        mock.windll.user32.MessageBoxW.assert_called_once()

    def test_alert_time_aware_below_bool_dropped(self):
        """Boolean for alert_time_aware_below is dropped."""
        result, _ = self._run_validate({'alert_time_aware_below': True})
        self.assertNotIn('alert_time_aware_below', result)

    # Boolean key validation

    def test_alert_time_aware_true_valid(self):
        """Boolean true for alert_time_aware passes through."""
        result, mock = self._run_validate({'alert_time_aware': True})
        self.assertIs(result['alert_time_aware'], True)
        mock.windll.user32.MessageBoxW.assert_not_called()

    def test_alert_time_aware_false_valid(self):
        """Boolean false for alert_time_aware passes through."""
        result, mock = self._run_validate({'alert_time_aware': False})
        self.assertIs(result['alert_time_aware'], False)
        mock.windll.user32.MessageBoxW.assert_not_called()

    def test_alert_time_aware_int_dropped(self):
        """Integer 1 for alert_time_aware is dropped (must be boolean)."""
        result, mock = self._run_validate({'alert_time_aware': 1})
        self.assertNotIn('alert_time_aware', result)
        mock.windll.user32.MessageBoxW.assert_called_once()

    def test_alert_time_aware_string_dropped(self):
        """String 'true' for alert_time_aware is dropped."""
        result, mock = self._run_validate({'alert_time_aware': 'true'})
        self.assertNotIn('alert_time_aware', result)
        mock.windll.user32.MessageBoxW.assert_called_once()

    # Command validation (string or array of strings)

    def test_on_reset_command_string_normalized_to_list(self):
        """String value for on_reset_command is normalized to a single-element list."""
        result, mock = self._run_validate({'on_reset_command': 'echo hello'})
        self.assertEqual(result['on_reset_command'], ['echo hello'])
        mock.windll.user32.MessageBoxW.assert_not_called()

    def test_on_reset_command_list_valid(self):
        """Array of strings for on_reset_command passes through."""
        result, mock = self._run_validate({'on_reset_command': ['cmd1', 'cmd2']})
        self.assertEqual(result['on_reset_command'], ['cmd1', 'cmd2'])
        mock.windll.user32.MessageBoxW.assert_not_called()

    def test_on_reset_command_empty_list_valid(self):
        """Empty array for on_reset_command is valid (disables the command)."""
        result, mock = self._run_validate({'on_reset_command': []})
        self.assertEqual(result['on_reset_command'], [])
        mock.windll.user32.MessageBoxW.assert_not_called()

    def test_on_reset_command_non_string_dropped(self):
        """Non-string/non-array value for on_reset_command is dropped."""
        result, mock = self._run_validate({'on_reset_command': 42})
        self.assertNotIn('on_reset_command', result)
        mock.windll.user32.MessageBoxW.assert_called_once()

    def test_on_reset_command_list_with_non_string_dropped(self):
        """Array with non-string elements for on_reset_command is dropped."""
        result, mock = self._run_validate({'on_reset_command': ['cmd1', 42]})
        self.assertNotIn('on_reset_command', result)
        mock.windll.user32.MessageBoxW.assert_called_once()

    def test_on_threshold_command_string_normalized_to_list(self):
        """String value for on_threshold_command is normalized to a single-element list."""
        result, mock = self._run_validate({'on_threshold_command': 'powershell -File notify.ps1'})
        self.assertEqual(result['on_threshold_command'], ['powershell -File notify.ps1'])
        mock.windll.user32.MessageBoxW.assert_not_called()

    def test_on_threshold_command_list_valid(self):
        """Array of strings for on_threshold_command passes through."""
        result, mock = self._run_validate({'on_threshold_command': ['sound.bat', 'curl http://example.com']})
        self.assertEqual(result['on_threshold_command'], ['sound.bat', 'curl http://example.com'])
        mock.windll.user32.MessageBoxW.assert_not_called()

    def test_on_threshold_command_non_string_dropped(self):
        """Non-string/non-array value for on_threshold_command is dropped."""
        result, mock = self._run_validate({'on_threshold_command': True})
        self.assertNotIn('on_threshold_command', result)
        mock.windll.user32.MessageBoxW.assert_called_once()

    def _run_validate(self, data: dict) -> tuple[dict, MagicMock]:
        """Run _validate with mocked ctypes and return (result, mock_ctypes)."""
        mock_ctypes = MagicMock()
        with patch.object(settings_mod, 'ctypes', mock_ctypes):
            result = settings_mod._validate(dict(data), Path('/fake/settings.json'))
        return result, mock_ctypes


class TestIconFieldsValidation(unittest.TestCase):
    """Tests for icon_fields setting validation."""

    def _run_validate(self, data: dict) -> tuple[dict, MagicMock]:
        """Run _validate with mocked ctypes and return (result, mock_ctypes)."""
        mock_ctypes = MagicMock()
        with patch.object(settings_mod, 'ctypes', mock_ctypes):
            result = settings_mod._validate(dict(data), Path('/fake/settings.json'))
        return result, mock_ctypes

    def test_valid_two_strings(self):
        """Valid array of exactly 2 non-empty strings passes through."""
        result, mock = self._run_validate({'icon_fields': ['five_hour', 'seven_day_sonnet']})
        self.assertEqual(result['icon_fields'], ['five_hour', 'seven_day_sonnet'])
        mock.windll.user32.MessageBoxW.assert_not_called()

    def test_not_array_dropped(self):
        """Non-array value is dropped."""
        result, mock = self._run_validate({'icon_fields': 'five_hour'})
        self.assertNotIn('icon_fields', result)
        mock.windll.user32.MessageBoxW.assert_called_once()

    def test_one_entry_dropped(self):
        """Array with only one entry is dropped."""
        result, mock = self._run_validate({'icon_fields': ['five_hour']})
        self.assertNotIn('icon_fields', result)
        mock.windll.user32.MessageBoxW.assert_called_once()

    def test_three_entries_dropped(self):
        """Array with three entries is dropped."""
        result, mock = self._run_validate({'icon_fields': ['a', 'b', 'c']})
        self.assertNotIn('icon_fields', result)
        mock.windll.user32.MessageBoxW.assert_called_once()

    def test_empty_array_dropped(self):
        """Empty array is dropped."""
        result, mock = self._run_validate({'icon_fields': []})
        self.assertNotIn('icon_fields', result)
        mock.windll.user32.MessageBoxW.assert_called_once()

    def test_non_string_entry_dropped(self):
        """Array with non-string entry is dropped."""
        result, mock = self._run_validate({'icon_fields': ['five_hour', 42]})
        self.assertNotIn('icon_fields', result)
        mock.windll.user32.MessageBoxW.assert_called_once()

    def test_empty_string_entry_dropped(self):
        """Array with empty string entry is dropped."""
        result, mock = self._run_validate({'icon_fields': ['five_hour', '']})
        self.assertNotIn('icon_fields', result)
        mock.windll.user32.MessageBoxW.assert_called_once()

    def test_unknown_field_names_accepted(self):
        """Unknown field names are not rejected."""
        result, mock = self._run_validate({'icon_fields': ['future_field', 'another_field']})
        self.assertEqual(result['icon_fields'], ['future_field', 'another_field'])
        mock.windll.user32.MessageBoxW.assert_not_called()

    def test_bool_entry_dropped(self):
        """Array with boolean entry is dropped."""
        result, mock = self._run_validate({'icon_fields': [True, 'five_hour']})
        self.assertNotIn('icon_fields', result)
        mock.windll.user32.MessageBoxW.assert_called_once()

    def test_valid_mode_suffix_utilization(self):
        """Field with ':utilization' suffix is accepted."""
        result, mock = self._run_validate({'icon_fields': ['five_hour:utilization', 'seven_day']})
        self.assertEqual(result['icon_fields'], ['five_hour:utilization', 'seven_day'])
        mock.windll.user32.MessageBoxW.assert_not_called()

    def test_valid_mode_suffix_overage(self):
        """Field with ':overage' suffix is accepted."""
        result, mock = self._run_validate({'icon_fields': ['five_hour:overage', 'seven_day:overage']})
        self.assertEqual(result['icon_fields'], ['five_hour:overage', 'seven_day:overage'])
        mock.windll.user32.MessageBoxW.assert_not_called()

    def test_invalid_mode_suffix_dropped(self):
        """Field with unknown mode suffix is dropped."""
        result, mock = self._run_validate({'icon_fields': ['five_hour:bogus', 'seven_day']})
        self.assertNotIn('icon_fields', result)
        mock.windll.user32.MessageBoxW.assert_called_once()

    def test_mixed_valid_and_invalid_mode_dropped(self):
        """Any invalid mode suffix causes the entire icon_fields to be dropped."""
        result, mock = self._run_validate({'icon_fields': ['five_hour:overage', 'seven_day:invalid']})
        self.assertNotIn('icon_fields', result)
        mock.windll.user32.MessageBoxW.assert_called_once()

    def test_mode_suffix_on_unknown_field_accepted(self):
        """Valid mode suffix on unknown field name is accepted."""
        result, mock = self._run_validate({'icon_fields': ['future_field:overage', 'another:utilization']})
        self.assertEqual(result['icon_fields'], ['future_field:overage', 'another:utilization'])
        mock.windll.user32.MessageBoxW.assert_not_called()


class TestDetectSystemTimeFormat(unittest.TestCase):
    """Tests for _detect_system_time_format() Windows locale detection."""

    def _detect(self, chars: int, itime_value: int) -> str:
        """Run detection with GetLocaleInfoEx mocked to return *chars* and write *itime_value*."""
        mock_ctypes = MagicMock()
        mock_ctypes.wintypes.DWORD.return_value.value = itime_value
        mock_ctypes.windll.kernel32.GetLocaleInfoEx.return_value = chars
        with patch.object(settings_mod, 'ctypes', mock_ctypes):
            return settings_mod._detect_system_time_format()

    def test_itime_one_is_24h(self):
        """LOCALE_ITIME of 1 maps to a 24-hour clock."""
        self.assertEqual(self._detect(chars=2, itime_value=1), '24h')

    def test_itime_zero_is_12h(self):
        """LOCALE_ITIME of 0 maps to a 12-hour clock."""
        self.assertEqual(self._detect(chars=2, itime_value=0), '12h')

    def test_query_failure_falls_back_to_24h(self):
        """A failed locale query (0 chars written) falls back to 24-hour."""
        self.assertEqual(self._detect(chars=0, itime_value=0), '24h')


class TestIconFieldsDefault(unittest.TestCase):
    """Tests for ICON_FIELDS default value."""

    def test_default_without_settings(self):
        """Default icon_fields is ['five_hour', 'seven_day'] when no settings file exists."""
        with TemporaryDirectory() as app_tmp, TemporaryDirectory() as home_tmp:
            loaded = _load(Path(app_tmp), Path(home_tmp))
        self.assertNotIn('icon_fields', loaded)

    def test_override_from_settings(self):
        """icon_fields is loaded from settings file."""
        with TemporaryDirectory() as app_tmp, TemporaryDirectory() as home_tmp:
            settings = {'icon_fields': ['seven_day', 'five_hour']}
            (Path(app_tmp) / settings_mod.SETTINGS_FILENAME).write_text(json.dumps(settings), encoding='utf-8')
            loaded = _load(Path(app_tmp), Path(home_tmp))
        self.assertEqual(loaded['icon_fields'], ['seven_day', 'five_hour'])


class TestTooltipFieldsValidation(unittest.TestCase):
    """Tests for tooltip_fields setting validation."""

    def _run_validate(self, data: dict) -> tuple[dict, MagicMock]:
        """Run _validate with mocked ctypes and return (result, mock_ctypes)."""
        mock_ctypes = MagicMock()
        with patch.object(settings_mod, 'ctypes', mock_ctypes):
            result = settings_mod._validate(dict(data), Path('/fake/settings.json'))
        return result, mock_ctypes

    def test_valid_list(self):
        """Valid array of non-empty strings passes through."""
        result, mock = self._run_validate({'tooltip_fields': ['five_hour', 'seven_day_sonnet']})
        self.assertEqual(result['tooltip_fields'], ['five_hour', 'seven_day_sonnet'])
        mock.windll.user32.MessageBoxW.assert_not_called()

    def test_empty_list_valid(self):
        """Empty list is valid (tooltip shows only the title)."""
        result, mock = self._run_validate({'tooltip_fields': []})
        self.assertEqual(result['tooltip_fields'], [])
        mock.windll.user32.MessageBoxW.assert_not_called()

    def test_single_entry_valid(self):
        """Single entry is valid."""
        result, mock = self._run_validate({'tooltip_fields': ['five_hour']})
        self.assertEqual(result['tooltip_fields'], ['five_hour'])
        mock.windll.user32.MessageBoxW.assert_not_called()

    def test_not_array_dropped(self):
        """Non-array value is dropped."""
        result, mock = self._run_validate({'tooltip_fields': 'five_hour'})
        self.assertNotIn('tooltip_fields', result)
        mock.windll.user32.MessageBoxW.assert_called_once()

    def test_non_string_entry_dropped(self):
        """Array with non-string entry is dropped."""
        result, mock = self._run_validate({'tooltip_fields': ['five_hour', 42]})
        self.assertNotIn('tooltip_fields', result)
        mock.windll.user32.MessageBoxW.assert_called_once()

    def test_empty_string_entry_dropped(self):
        """Array with empty string entry is dropped."""
        result, mock = self._run_validate({'tooltip_fields': ['five_hour', '']})
        self.assertNotIn('tooltip_fields', result)
        mock.windll.user32.MessageBoxW.assert_called_once()

    def test_bool_entry_dropped(self):
        """Array with boolean entry is dropped."""
        result, mock = self._run_validate({'tooltip_fields': [True, 'five_hour']})
        self.assertNotIn('tooltip_fields', result)
        mock.windll.user32.MessageBoxW.assert_called_once()

    def test_duplicates_removed(self):
        """Duplicate entries are silently removed."""
        result, mock = self._run_validate({'tooltip_fields': ['five_hour', 'seven_day', 'five_hour']})
        self.assertEqual(result['tooltip_fields'], ['five_hour', 'seven_day'])
        mock.windll.user32.MessageBoxW.assert_not_called()

    def test_unknown_field_names_accepted(self):
        """Unknown field names are not rejected."""
        result, mock = self._run_validate({'tooltip_fields': ['future_field']})
        self.assertEqual(result['tooltip_fields'], ['future_field'])
        mock.windll.user32.MessageBoxW.assert_not_called()


class TestTooltipFieldsDefault(unittest.TestCase):
    """Tests for TOOLTIP_FIELDS default value."""

    def test_default_without_settings(self):
        """Default tooltip_fields is ['five_hour', 'seven_day'] when no settings file exists."""
        with TemporaryDirectory() as app_tmp, TemporaryDirectory() as home_tmp:
            loaded = _load(Path(app_tmp), Path(home_tmp))
        self.assertNotIn('tooltip_fields', loaded)

    def test_override_from_settings(self):
        """tooltip_fields is loaded from settings file."""
        with TemporaryDirectory() as app_tmp, TemporaryDirectory() as home_tmp:
            settings = {'tooltip_fields': ['seven_day_sonnet']}
            (Path(app_tmp) / settings_mod.SETTINGS_FILENAME).write_text(json.dumps(settings), encoding='utf-8')
            loaded = _load(Path(app_tmp), Path(home_tmp))
        self.assertEqual(loaded['tooltip_fields'], ['seven_day_sonnet'])


class TestGetAlertThresholds(unittest.TestCase):
    """Tests for get_alert_thresholds() lookup logic."""

    def test_five_hour_returns_session_thresholds(self):
        """five_hour variant returns session thresholds."""
        thresholds = {'five_hour': [70, 90], 'seven_day': [80, 95], 'extra_usage': [50]}
        with patch.object(settings_mod, '_ALERT_THRESHOLDS', thresholds), \
             patch.object(settings_mod, '_S', {}):
            self.assertEqual(settings_mod.get_alert_thresholds('five_hour'), [70, 90])

    def test_seven_day_returns_weekly_thresholds(self):
        """seven_day variant returns weekly thresholds."""
        thresholds = {'five_hour': [70, 90], 'seven_day': [80, 95], 'extra_usage': [50]}
        with patch.object(settings_mod, '_ALERT_THRESHOLDS', thresholds), \
             patch.object(settings_mod, '_S', {}):
            self.assertEqual(settings_mod.get_alert_thresholds('seven_day'), [80, 95])

    def test_seven_day_sonnet_falls_back_to_weekly(self):
        """seven_day_sonnet falls back to seven_day thresholds."""
        thresholds = {'five_hour': [70], 'seven_day': [80, 95], 'extra_usage': [50]}
        with patch.object(settings_mod, '_ALERT_THRESHOLDS', thresholds), \
             patch.object(settings_mod, '_S', {}):
            self.assertEqual(settings_mod.get_alert_thresholds('seven_day_sonnet'), [80, 95])

    def test_seven_day_opus_falls_back_to_weekly(self):
        """seven_day_opus falls back to seven_day thresholds."""
        thresholds = {'five_hour': [70], 'seven_day': [80, 95], 'extra_usage': [50]}
        with patch.object(settings_mod, '_ALERT_THRESHOLDS', thresholds), \
             patch.object(settings_mod, '_S', {}):
            self.assertEqual(settings_mod.get_alert_thresholds('seven_day_opus'), [80, 95])

    def test_exact_settings_override(self):
        """Per-variant settings override takes priority over built-in defaults."""
        thresholds = {'five_hour': [70], 'seven_day': [80, 95], 'extra_usage': [50]}
        settings = {'alert_thresholds_seven_day_opus': [50, 80]}
        with patch.object(settings_mod, '_ALERT_THRESHOLDS', thresholds), \
             patch.object(settings_mod, '_S', settings):
            self.assertEqual(settings_mod.get_alert_thresholds('seven_day_opus'), [50, 80])

    def test_base_period_settings_override(self):
        """Base period settings override applies to variants."""
        thresholds = {'five_hour': [70], 'seven_day': [80, 95], 'extra_usage': [50]}
        settings = {'alert_thresholds_seven_day': [60, 90]}
        with patch.object(settings_mod, '_ALERT_THRESHOLDS', thresholds), \
             patch.object(settings_mod, '_S', settings):
            self.assertEqual(settings_mod.get_alert_thresholds('seven_day_cowork'), [60, 90])

    def test_extra_usage_returns_own_thresholds(self):
        """extra_usage variant returns its own thresholds."""
        thresholds = {'five_hour': [70], 'seven_day': [80, 95], 'extra_usage': [50, 80, 95]}
        with patch.object(settings_mod, '_ALERT_THRESHOLDS', thresholds), \
             patch.object(settings_mod, '_S', {}):
            self.assertEqual(settings_mod.get_alert_thresholds('extra_usage'), [50, 80, 95])

    def test_unknown_variant_returns_empty(self):
        """Unknown variant key returns empty list."""
        with patch.object(settings_mod, '_ALERT_THRESHOLDS', {'five_hour': [80], 'seven_day': [80]}), \
             patch.object(settings_mod, '_S', {}):
            self.assertEqual(settings_mod.get_alert_thresholds('unknown'), [])


class TestPopupFieldsValidation(unittest.TestCase):
    """Tests for popup_fields setting validation."""

    def _run_validate(self, data: dict) -> tuple[dict, MagicMock]:
        mock_ctypes = MagicMock()
        with patch.object(settings_mod, 'ctypes', mock_ctypes):
            result = settings_mod._validate(dict(data), Path('/fake/settings.json'))
        return result, mock_ctypes

    def test_valid_list_with_wildcard(self):
        """Array with field names and wildcard passes through."""
        result, mock = self._run_validate({'popup_fields': ['five_hour', '*']})
        self.assertEqual(result['popup_fields'], ['five_hour', '*'])
        mock.windll.user32.MessageBoxW.assert_not_called()

    def test_wildcard_only(self):
        """Array with only wildcard passes through."""
        result, mock = self._run_validate({'popup_fields': ['*']})
        self.assertEqual(result['popup_fields'], ['*'])
        mock.windll.user32.MessageBoxW.assert_not_called()

    def test_no_wildcard(self):
        """Array without wildcard passes through."""
        result, mock = self._run_validate({'popup_fields': ['five_hour', 'seven_day']})
        self.assertEqual(result['popup_fields'], ['five_hour', 'seven_day'])
        mock.windll.user32.MessageBoxW.assert_not_called()

    def test_empty_list_valid(self):
        """Empty list is valid (no bars shown)."""
        result, mock = self._run_validate({'popup_fields': []})
        self.assertEqual(result['popup_fields'], [])
        mock.windll.user32.MessageBoxW.assert_not_called()

    def test_multiple_wildcards_dropped(self):
        """Multiple wildcards cause the key to be dropped."""
        result, mock = self._run_validate({'popup_fields': ['*', 'five_hour', '*']})
        self.assertNotIn('popup_fields', result)
        mock.windll.user32.MessageBoxW.assert_called_once()

    def test_not_array_dropped(self):
        """Non-array value is dropped."""
        result, mock = self._run_validate({'popup_fields': 'five_hour'})
        self.assertNotIn('popup_fields', result)
        mock.windll.user32.MessageBoxW.assert_called_once()

    def test_non_string_entry_dropped(self):
        """Array with non-string entry is dropped."""
        result, mock = self._run_validate({'popup_fields': ['five_hour', 42]})
        self.assertNotIn('popup_fields', result)
        mock.windll.user32.MessageBoxW.assert_called_once()

    def test_empty_string_entry_dropped(self):
        """Array with empty string entry is dropped."""
        result, mock = self._run_validate({'popup_fields': ['five_hour', '']})
        self.assertNotIn('popup_fields', result)
        mock.windll.user32.MessageBoxW.assert_called_once()

    def test_duplicates_removed(self):
        """Duplicate entries are silently removed (wildcard preserved)."""
        result, mock = self._run_validate({'popup_fields': ['five_hour', 'seven_day', 'five_hour', '*']})
        self.assertEqual(result['popup_fields'], ['five_hour', 'seven_day', '*'])
        mock.windll.user32.MessageBoxW.assert_not_called()


class TestDynamicThresholdValidation(unittest.TestCase):
    """Tests for dynamic alert_thresholds_* key validation."""

    def _run_validate(self, data: dict) -> tuple[dict, MagicMock]:
        mock_ctypes = MagicMock()
        with patch.object(settings_mod, 'ctypes', mock_ctypes):
            result = settings_mod._validate(dict(data), Path('/fake/settings.json'))
        return result, mock_ctypes

    def test_per_variant_threshold_valid(self):
        """Per-variant threshold key is validated as threshold array."""
        result, mock = self._run_validate({'alert_thresholds_seven_day_opus': [50, 80, 95]})
        self.assertEqual(result['alert_thresholds_seven_day_opus'], [50, 80, 95])
        mock.windll.user32.MessageBoxW.assert_not_called()

    def test_per_variant_threshold_invalid_dropped(self):
        """Invalid per-variant threshold value is dropped."""
        result, mock = self._run_validate({'alert_thresholds_seven_day_opus': 'bad'})
        self.assertNotIn('alert_thresholds_seven_day_opus', result)
        mock.windll.user32.MessageBoxW.assert_called_once()

    def test_per_variant_threshold_sorted_deduped(self):
        """Per-variant thresholds are sorted and deduplicated."""
        result, _ = self._run_validate({'alert_thresholds_seven_day_cowork': [95, 50, 80, 50]})
        self.assertEqual(result['alert_thresholds_seven_day_cowork'], [50, 80, 95])


class TestCompactHideValidation(unittest.TestCase):
    """Tests for compact_hide setting validation."""

    def _run_validate(self, data: dict) -> tuple[dict, MagicMock]:
        """Run _validate with mocked ctypes and return (result, mock_ctypes)."""
        mock_ctypes = MagicMock()
        with patch.object(settings_mod, 'ctypes', mock_ctypes):
            result = settings_mod._validate(dict(data), Path('/fake/settings.json'))
        return result, mock_ctypes

    def test_valid_list(self):
        """Array of section keys and field names passes through."""
        result, mock = self._run_validate({'compact_hide': ['account', 'claude_code', 'seven_day_opus']})
        self.assertEqual(result['compact_hide'], ['account', 'claude_code', 'seven_day_opus'])
        mock.windll.user32.MessageBoxW.assert_not_called()

    def test_empty_list_valid(self):
        """Empty list is valid (pinning hides nothing)."""
        result, mock = self._run_validate({'compact_hide': []})
        self.assertEqual(result['compact_hide'], [])
        mock.windll.user32.MessageBoxW.assert_not_called()

    def test_not_array_dropped(self):
        """Non-array value is dropped."""
        result, mock = self._run_validate({'compact_hide': 'account'})
        self.assertNotIn('compact_hide', result)
        mock.windll.user32.MessageBoxW.assert_called_once()

    def test_non_string_entry_dropped(self):
        """Array with non-string entry is dropped."""
        result, mock = self._run_validate({'compact_hide': ['account', 42]})
        self.assertNotIn('compact_hide', result)
        mock.windll.user32.MessageBoxW.assert_called_once()

    def test_empty_string_entry_dropped(self):
        """Array with empty string entry is dropped."""
        result, mock = self._run_validate({'compact_hide': ['account', '']})
        self.assertNotIn('compact_hide', result)
        mock.windll.user32.MessageBoxW.assert_called_once()

    def test_duplicates_removed(self):
        """Duplicate entries are silently removed."""
        result, mock = self._run_validate({'compact_hide': ['account', 'status', 'account']})
        self.assertEqual(result['compact_hide'], ['account', 'status'])
        mock.windll.user32.MessageBoxW.assert_not_called()

    def test_unknown_names_accepted(self):
        """Unknown section/field names are not rejected."""
        result, mock = self._run_validate({'compact_hide': ['future_section']})
        self.assertEqual(result['compact_hide'], ['future_section'])
        mock.windll.user32.MessageBoxW.assert_not_called()


class TestCompactHideDefault(unittest.TestCase):
    """Tests for COMPACT_HIDE default value."""

    def test_default_without_settings(self):
        """compact_hide is absent when no settings file exists (defaults to empty)."""
        with TemporaryDirectory() as app_tmp, TemporaryDirectory() as home_tmp:
            loaded = _load(Path(app_tmp), Path(home_tmp))
        self.assertNotIn('compact_hide', loaded)

    def test_override_from_settings(self):
        """compact_hide is loaded from the settings file."""
        with TemporaryDirectory() as app_tmp, TemporaryDirectory() as home_tmp:
            settings = {'compact_hide': ['account', 'status']}
            (Path(app_tmp) / settings_mod.SETTINGS_FILENAME).write_text(json.dumps(settings), encoding='utf-8')
            loaded = _load(Path(app_tmp), Path(home_tmp))
        self.assertEqual(loaded['compact_hide'], ['account', 'status'])


if __name__ == '__main__':
    unittest.main()

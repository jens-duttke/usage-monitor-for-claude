"""
i18n Tests
===========

Unit tests for detect_lang_code(), load_translations(), and locale file consistency.
"""
from __future__ import annotations

import json
import re
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from usage_monitor_for_claude.i18n import LOCALE_DIR, detect_lang_code, load_translations

MOCK_LOCALE_FILES = ['en.json', 'de.json', 'es.json', 'fr.json', 'hi.json', 'id.json', 'ja.json', 'pt-BR.json', 'uk.json', 'zh-CN.json', 'zh-TW.json']

NORMALIZE_MAP = {
    'de_DE': 'de_DE.ISO8859-1',
    'en_US': 'en_US.ISO8859-1',
    'pt_BR': 'pt_BR.ISO8859-1',
    'ja_JP': 'ja_JP.eucJP',
    'fr_FR': 'fr_FR.ISO8859-1',
    'zh_CN': 'zh_CN.eucCN',
    'zh_TW': 'zh_TW.big5',
    'German_Germany': 'German_Germany',
    'German': 'de_DE.ISO8859-1',
    'Spanish_Mexico': 'Spanish_Mexico',
    'Spanish': 'es_ES.ISO8859-1',
    'Ukrainian_Ukraine': 'Ukrainian_Ukraine',
    'Ukrainian': 'Ukrainian',
    '': '',
}


def _mock_normalize(locale_string):
    """Simulate locale.normalize() for cross-platform test determinism."""
    return NORMALIZE_MAP.get(locale_string, locale_string)


# ---------------------------------------------------------------------------
# detect_lang_code
# ---------------------------------------------------------------------------

@patch('usage_monitor_for_claude.i18n.locale.normalize', side_effect=_mock_normalize)
class TestDetectLangCode(unittest.TestCase):
    """Tests for detect_lang_code()."""

    def setUp(self):
        self._tmp = TemporaryDirectory()
        self._locale_dir = Path(self._tmp.name)
        for name in MOCK_LOCALE_FILES:
            (self._locale_dir / name).write_text('{}')
        self._patch_dir = patch('usage_monitor_for_claude.i18n.LOCALE_DIR', self._locale_dir)
        self._patch_dir.start()

    def tearDown(self):
        self._patch_dir.stop()
        self._tmp.cleanup()

    def test_de_DE_resolves_to_base(self, _mock_norm):
        """Standard ISO locale falls back to base language file."""
        self.assertEqual(detect_lang_code('de_DE'), 'de')

    def test_en_US_resolves_to_base(self, _mock_norm):
        self.assertEqual(detect_lang_code('en_US'), 'en')

    def test_fr_FR_resolves_to_base(self, _mock_norm):
        """Regional locale without regional file falls back to base."""
        self.assertEqual(detect_lang_code('fr_FR'), 'fr')

    def test_pt_BR_regional_file_found(self, _mock_norm):
        """Regional variant with matching file returns region-specific code."""
        self.assertEqual(detect_lang_code('pt_BR'), 'pt-BR')

    def test_zh_CN_regional_file_found(self, _mock_norm):
        self.assertEqual(detect_lang_code('zh_CN'), 'zh-CN')

    def test_zh_TW_regional_file_found(self, _mock_norm):
        self.assertEqual(detect_lang_code('zh_TW'), 'zh-TW')

    def test_ja_JP_no_regional_file(self, _mock_norm):
        """Locale with region but no regional file falls back to base."""
        self.assertEqual(detect_lang_code('ja_JP'), 'ja')

    def test_german_germany_windows_name(self, _mock_norm):
        """Windows-style long locale name resolves via normalize retry."""
        self.assertEqual(detect_lang_code('German_Germany'), 'de')

    def test_spanish_mexico_windows_name(self, _mock_norm):
        """Windows-style name without regional file falls back to base."""
        self.assertEqual(detect_lang_code('Spanish_Mexico'), 'es')

    def test_ukrainian_windows_name(self, _mock_norm):
        """Windows-style name with manual override resolves correctly."""
        self.assertEqual(detect_lang_code('Ukrainian_Ukraine'), 'uk')

    def test_chinese_simplified_windows_name(self, _mock_norm):
        """Simplified Chinese Windows display name resolves to zh-CN."""
        self.assertEqual(detect_lang_code('Chinese (Simplified)_China'), 'zh-CN')

    def test_chinese_traditional_windows_name(self, _mock_norm):
        """Traditional Chinese Windows display name resolves to zh-TW."""
        self.assertEqual(detect_lang_code('Chinese (Traditional)_Taiwan'), 'zh-TW')

    def test_chinese_traditional_hong_kong_windows_name(self, _mock_norm):
        """Hong Kong SAR resolves to the traditional-script file."""
        self.assertEqual(detect_lang_code('Chinese (Traditional)_Hong Kong SAR'), 'zh-TW')

    def test_chinese_simplified_singapore_windows_name(self, _mock_norm):
        """Singapore resolves to the simplified-script file."""
        self.assertEqual(detect_lang_code('Chinese (Simplified)_Singapore'), 'zh-CN')

    def test_chinese_taiwan_without_script_windows_name(self, _mock_norm):
        """A script-less 'Chinese_Taiwan' name resolves via the region to zh-TW."""
        self.assertEqual(detect_lang_code('Chinese_Taiwan'), 'zh-TW')

    def test_hindi_windows_name(self, _mock_norm):
        """Hindi Windows display name resolves to hi."""
        self.assertEqual(detect_lang_code('Hindi_India'), 'hi')

    def test_indonesian_windows_name(self, _mock_norm):
        """Indonesian Windows display name resolves to id."""
        self.assertEqual(detect_lang_code('Indonesian_Indonesia'), 'id')

    def test_base_code_without_region(self, _mock_norm):
        """Base language code without region resolves directly."""
        self.assertEqual(detect_lang_code('fr'), 'fr')

    def test_unknown_locale_falls_back_to_en(self, _mock_norm):
        """Completely unknown locale falls back to English."""
        self.assertEqual(detect_lang_code('xx_YY'), 'en')

    def test_unknown_windows_name_falls_back_to_en(self, _mock_norm):
        """Windows-style name where normalize retry also fails."""
        self.assertEqual(detect_lang_code('Klingon_Qonos'), 'en')

    def test_empty_string_falls_back_to_en(self, _mock_norm):
        self.assertEqual(detect_lang_code(''), 'en')


# ---------------------------------------------------------------------------
# load_translations
# ---------------------------------------------------------------------------

class TestLoadTranslations(unittest.TestCase):
    """Tests for load_translations()."""

    @patch('usage_monitor_for_claude.settings.LANGUAGE', '')
    @patch('usage_monitor_for_claude.i18n.locale.normalize', side_effect=_mock_normalize)
    @patch('usage_monitor_for_claude.i18n.locale.getlocale', return_value=('de_DE', 'UTF-8'))
    def test_loads_detected_locale(self, _mock_get, _mock_norm):
        """Loads the JSON file matching the detected system locale."""
        with TemporaryDirectory() as tmp:
            locale_dir = Path(tmp)
            (locale_dir / 'en.json').write_text('{"title": "English"}')
            (locale_dir / 'de.json').write_text('{"title": "Deutsch"}')

            with patch('usage_monitor_for_claude.i18n.LOCALE_DIR', locale_dir):
                result = load_translations()

        self.assertEqual(result['title'], 'Deutsch')

    @patch('usage_monitor_for_claude.settings.LANGUAGE', '')
    @patch('usage_monitor_for_claude.i18n.locale.normalize', side_effect=_mock_normalize)
    @patch('usage_monitor_for_claude.i18n.locale.getlocale', return_value=(None, None))
    def test_none_locale_falls_back_to_english(self, _mock_get, _mock_norm):
        """None from getlocale() falls back to English."""
        with TemporaryDirectory() as tmp:
            locale_dir = Path(tmp)
            (locale_dir / 'en.json').write_text('{"title": "English"}')

            with patch('usage_monitor_for_claude.i18n.LOCALE_DIR', locale_dir):
                result = load_translations()

        self.assertEqual(result['title'], 'English')

    def test_language_setting_overrides_locale(self):
        """LANGUAGE setting bypasses locale detection entirely."""
        with TemporaryDirectory() as tmp:
            locale_dir = Path(tmp)
            (locale_dir / 'en.json').write_text('{"title": "English"}')
            (locale_dir / 'ja.json').write_text('{"title": "Japanese"}')

            with patch('usage_monitor_for_claude.settings.LANGUAGE', 'ja'), \
                 patch('usage_monitor_for_claude.i18n.LOCALE_DIR', locale_dir):
                result = load_translations()

        self.assertEqual(result['title'], 'Japanese')

    @patch('usage_monitor_for_claude.settings.LANGUAGE', 'xx')
    @patch('usage_monitor_for_claude.i18n.locale.normalize', side_effect=_mock_normalize)
    @patch('usage_monitor_for_claude.i18n.locale.getlocale', return_value=('de_DE', 'UTF-8'))
    def test_invalid_language_setting_falls_back_to_locale(self, _mock_get, _mock_norm):
        """Invalid LANGUAGE setting falls back to locale detection."""
        with TemporaryDirectory() as tmp:
            locale_dir = Path(tmp)
            (locale_dir / 'en.json').write_text('{"title": "English"}')
            (locale_dir / 'de.json').write_text('{"title": "Deutsch"}')

            with patch('usage_monitor_for_claude.i18n.LOCALE_DIR', locale_dir):
                result = load_translations()

        self.assertEqual(result['title'], 'Deutsch')


# ---------------------------------------------------------------------------
# locale file consistency
# ---------------------------------------------------------------------------

class TestLocaleConsistency(unittest.TestCase):
    """Verify all locale files are consistent with en.json (reference)."""

    @classmethod
    def setUpClass(cls):
        cls.locale_files = sorted(LOCALE_DIR.glob('*.json'))
        cls.translations = {}
        for path in cls.locale_files:
            cls.translations[path.stem] = json.loads(path.read_text(encoding='utf-8'))
        cls.reference = cls.translations['en']

    def test_at_least_two_locale_files_exist(self):
        self.assertGreaterEqual(len(self.locale_files), 2)

    def test_all_files_have_same_keys_as_english(self):
        """Every locale file must have exactly the same keys as en.json."""
        ref_keys = set(self.reference.keys())

        for lang, data in self.translations.items():
            if lang == 'en':
                continue
            lang_keys = set(data.keys())
            missing = ref_keys - lang_keys
            extra = lang_keys - ref_keys
            self.assertFalse(missing, f'{lang}.json missing keys: {missing}')
            self.assertFalse(extra, f'{lang}.json has extra keys: {extra}')

    def test_weekdays_have_seven_entries(self):
        """Every locale must have exactly 7 weekday names."""
        for lang, data in self.translations.items():
            self.assertEqual(len(data['weekdays']), 7, f'{lang}.json weekdays count != 7')

    def test_format_placeholders_match_english(self):
        """Format placeholders ({name}) in each translation must match en.json."""
        placeholder_re = re.compile(r'\{(\w+)\}')

        for key, en_value in self.reference.items():
            if not isinstance(en_value, str):
                continue
            en_placeholders = set(placeholder_re.findall(en_value))
            if not en_placeholders:
                continue

            for lang, data in self.translations.items():
                if lang == 'en':
                    continue
                lang_placeholders = set(placeholder_re.findall(data[key]))
                self.assertEqual(
                    en_placeholders, lang_placeholders,
                    f'{lang}.json key "{key}": placeholders {lang_placeholders} != expected {en_placeholders}',
                )

    def test_auth_messages_end_with_the_login_command(self):
        """Every locale must hand the user the command that fixes a missing login.

        The command is what makes these messages actionable, so it has to
        survive translation verbatim and stay at the end, where a reader
        looks for it.
        """
        keys = ('auth_expired', 'auth_expired_short', 'no_token', 'warn_login')

        for lang, data in self.translations.items():
            for key in keys:
                self.assertTrue(
                    data[key].endswith('claude auth login'),
                    f'{lang}.json key "{key}" does not end with the login command: {data[key]!r}',
                )

    def test_claude_tray_tooltip_uses_consistent_display_name(self):
        """Every locale uses the Claude tray display name for all tooltip states."""
        for lang, data in self.translations.items():
            with self.subTest(locale=lang):
                self.assertEqual(data['tooltip_title'], 'Claude Usage')
                self.assertTrue(data['loading'].startswith('Claude Usage'))
                self.assertTrue(data['error_label'].startswith('Claude Usage'))

    def test_no_empty_translations(self):
        """No translation value should be an empty string."""
        for lang, data in self.translations.items():
            for key, value in data.items():
                if isinstance(value, str):
                    self.assertNotEqual(value, '', f'{lang}.json key "{key}" is empty')

    def test_value_types_match_english(self):
        """Value types (str, list) must match en.json for each key."""
        for lang, data in self.translations.items():
            if lang == 'en':
                continue
            for key in self.reference:
                self.assertIsInstance(
                    data[key], type(self.reference[key]),
                    f'{lang}.json key "{key}": expected {type(self.reference[key]).__name__}, '
                    f'got {type(data[key]).__name__}',
                )


if __name__ == '__main__':
    unittest.main()

"""
Internationalization
=====================

Loads translations for the detected system language with English fallback.
"""
from __future__ import annotations

import json
import locale
from pathlib import Path
from typing import Any

__all__ = ['LOCALE_DIR', 'detect_lang_code', 'load_translations', 'T']

LOCALE_DIR = Path(__file__).parent.parent / 'locale'


def detect_lang_code(lang: str) -> str:
    """Detect locale file code from system locale string using convention-based lookup.

    Lookup chain: ``{lang}-{REGION}.json`` → ``{lang}.json`` → ``en.json``.
    No mapping required - the locale directory structure *is* the configuration.

    Parameters
    ----------
    lang : str
        System locale string, e.g. ``'de_DE'`` or ``'German_Germany'``.

    Returns
    -------
    str
        Locale file code (without ``.json``).
    """
    normalized = locale.normalize(lang).split('.')[0]
    parts = normalized.split('_', 1)
    base = parts[0].lower()

    # On Windows, os.getlocale() returns e.g. 'German_Germany', and locale.normalize() fails to rewrite it to an ISO code,
    # so base becomes 'german'. Re-split using 'german' to hopefully trigger a match.
    if len(base) > 3:
        base = locale.normalize(parts[0]).split('.')[0].split('_')[0].lower()

    # Manual overrides for Windows locales that do not normalize cleanly to ISO codes.
    if base == 'ukrainian':
        base = 'uk'

    region = parts[1] if len(parts) > 1 and len(base) <= 3 else ''

    if region and (LOCALE_DIR / f'{base}-{region}.json').exists():
        return f'{base}-{region}'
    if (LOCALE_DIR / f'{base}.json').exists():
        return base

    return 'en'


def _load_locale(code: str) -> dict[str, Any]:
    """Load a single locale file as a dict (empty dict if missing/invalid)."""
    path = LOCALE_DIR / f'{code}.json'
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError):
        return {}


def load_translations() -> dict[str, Any]:
    """Load translations for the configured or detected system language.

    English (``en.json``) is always loaded first as a base, then the selected
    language is overlaid on top.  This guarantees a per-key fallback to
    English: any key missing from a translation file (e.g. a newly added
    string) resolves to the English text instead of raising ``KeyError``.
    """
    from .settings import LANGUAGE

    translations = _load_locale('en')

    if LANGUAGE:
        lang_file = LOCALE_DIR / f'{LANGUAGE}.json'
        if lang_file.exists():
            translations.update(_load_locale(LANGUAGE))
            return translations

    lang = locale.getlocale()[0] or ''
    lang_code = detect_lang_code(lang)
    if lang_code != 'en':
        translations.update(_load_locale(lang_code))

    return translations


T: dict[str, Any] = load_translations()

"""
Formatting
===========

Pure functions for formatting usage data: time-until-reset strings,
elapsed period percentages, credit amounts, status lines, and tooltip text.
"""
from __future__ import annotations

import locale as _locale
import re
from datetime import date, datetime, timedelta, timezone
from typing import Any

from .i18n import T
from .settings import (
    ACTIVE_SCHEDULE, ACTIVE_SCHEDULE_FIELDS, CURRENCY_SYMBOL, POPUP_FIELDS, TIME_FORMAT, TOOLTIP_FIELDS, _SYSTEM_CURRENCY_SYMBOL, _WEEKDAYS,
)

__all__ = [
    'divider_positions', 'elapsed_pct', 'expand_popup_fields', 'field_period', 'fit_tooltip', 'format_credits',
    'format_tooltip', 'is_active_quota', 'parse_field_name', 'popup_label', 'resolve_icon_fields', 'time_until', 'tooltip_label',
]

PERIOD_5H = 5 * 3600
PERIOD_7D = 7 * 24 * 3600

_NUMBER_WORDS = {
    'one': 1, 'two': 2, 'three': 3, 'four': 4, 'five': 5, 'six': 6,
    'seven': 7, 'eight': 8, 'nine': 9, 'ten': 10, 'eleven': 11, 'twelve': 12,
}
_UNIT_SUFFIXES = {'hour': 'h', 'day': 'd'}
_TITLE_CASE_EXCEPTIONS = {'oauth': 'OAuth', 'api': 'API', 'ai': 'AI'}
_CURRENCY_SYMBOLS = {
    'USD': '$', 'EUR': '€', 'GBP': '£', 'JPY': '¥', 'CNY': '¥',
    'INR': '₹', 'KRW': '₩', 'BRL': 'R$', 'CAD': 'CA$', 'AUD': 'A$', 'CHF': 'CHF',
}


def parse_field_name(field: str) -> tuple[int, str, str | None] | None:
    """Parse an API field name into its numeric, unit, and variant components.

    Parameters
    ----------
    field : str
        API field name, e.g. ``'five_hour'``, ``'seven_day_sonnet'``.

    Returns
    -------
    tuple or None
        ``(number, unit, variant)`` where *number* is the parsed digit,
        *unit* is the raw unit word (e.g. ``'hour'``, ``'day'``), and
        *variant* is the remaining suffix or ``None``.
        Returns ``None`` if the number word or unit is not recognized.
    """
    parts = field.split('_', 2)
    if len(parts) < 2:
        return None

    number = _NUMBER_WORDS.get(parts[0])
    unit = parts[1]
    if number is None or unit not in _UNIT_SUFFIXES:
        return None

    variant = parts[2] if len(parts) > 2 else None
    return (number, unit, variant)


def _title_case_variant(text: str) -> str:
    """Title-case a variant string, respecting abbreviation exceptions."""
    return ' '.join(_TITLE_CASE_EXCEPTIONS.get(w.lower(), w.title()) for w in text.split('_'))


def tooltip_label(field: str) -> str:
    """Generate a short tooltip label from an API field name.

    Parameters
    ----------
    field : str
        API field name, e.g. ``'five_hour'``, ``'seven_day_sonnet'``.

    Returns
    -------
    str
        Short label like ``'5h'``, ``'7d'``, or ``'Sonnet 7d'``.
        Falls back to title case of the full field name if unparseable.
    """
    parsed = parse_field_name(field)
    if parsed is None:
        return _title_case_variant(field)

    number, unit, variant = parsed
    label = f'{number}{_UNIT_SUFFIXES[unit]}'
    if variant:
        label = f'{_title_case_variant(variant)} {label}'
    return label


def popup_label(field: str) -> str:
    """Generate a popup bar label from an API field name using i18n templates.

    Parameters
    ----------
    field : str
        API field name, e.g. ``'five_hour'``, ``'seven_day_sonnet'``.

    Returns
    -------
    str
        Localized label like ``'Session (5hr)'`` or ``'Weekly (Sonnet)'``.
        Falls back to title case with abbreviation exceptions if unparseable.
    """
    parsed = parse_field_name(field)
    if parsed is None:
        return _title_case_variant(field)

    number, unit, variant = parsed
    if variant:
        suffix = _title_case_variant(variant)
    elif unit == 'hour':
        suffix = f'{number}hr'
    else:
        suffix = f'{number} {unit}'

    template_key = 'session_label' if unit == 'hour' else 'weekly_label'
    return T[template_key].format(suffix=suffix)


def field_period(field: str) -> int | None:
    """Return the period duration in seconds for a field, or None if unknown.

    Parameters
    ----------
    field : str
        API field name, e.g. ``'five_hour'``, ``'seven_day_sonnet'``.
    """
    parsed = parse_field_name(field)
    if parsed is None:
        return None

    number, unit, _ = parsed
    if unit == 'hour':
        return number * 3600
    if unit == 'day':
        return number * 24 * 3600
    return None


def is_active_quota(field: str, entry: Any) -> bool:
    """Return True if a usage entry is a quota that applies to the account.

    The API lists quota types before they apply, under code names (e.g.
    ``nimbus_quill``), with a utilization of 0 and no reset window.  An entry
    therefore counts only if it has a reset window, a name that parses, or
    the ``from_account_limits`` marker set by ``_merge_scoped_limits()``.

    Parameters
    ----------
    field : str
        API field name.
    entry : Any
        Value of that field in the usage response.
    """
    if not isinstance(entry, dict) or entry.get('utilization') is None:
        return False

    return bool(entry.get('resets_at')) or parse_field_name(field) is not None or bool(entry.get('from_account_limits'))


def _field_sort_key(field: str) -> tuple[int, int, int, str]:
    """Sort key for default field ordering: shorter periods first, base before variants."""
    parsed = parse_field_name(field)
    if parsed is None:
        return (2, 0, 0, field)

    number, unit, variant = parsed
    unit_order = 0 if unit == 'hour' else 1
    variant_order = 0 if variant is None else 1
    return (unit_order, number, variant_order, variant or '')


def expand_popup_fields(popup_fields: list[str], usage_data: dict[str, Any]) -> list[str]:
    """Expand a popup_fields setting into concrete field names based on API data.

    Parameters
    ----------
    popup_fields : list[str]
        User-configured field list, possibly containing the ``'*'`` wildcard
        and ``'-field'`` exclusions. Exclusions take precedence over all
        inclusions, regardless of their position in the list.
    usage_data : dict
        Raw API response dict.

    Returns
    -------
    list[str]
        Ordered list of field names to display, with excluded, null, missing
        and not-yet-active fields removed.
    """
    available = {
        key for key, value in usage_data.items()
        if isinstance(value, dict) and 'resets_at' in value and is_active_quota(key, value)
    }
    excluded = {field[1:] for field in popup_fields if field.startswith('-')}
    available.difference_update(excluded)

    result: list[str] = []
    seen: set[str] = set()

    for field in popup_fields:
        if field.startswith('-'):
            continue
        if field == '*':
            remaining = sorted((f for f in available if f not in seen), key=_field_sort_key)
            for f in remaining:
                seen.add(f)
                result.append(f)
        elif field in available and field not in seen:
            seen.add(field)
            result.append(field)

    return result


def resolve_icon_fields(icon_fields: list[str], popup_fields: list[str], usage_data: dict[str, Any]) -> list[tuple[str, str]]:
    """Resolve the icon_fields setting into the fields the tray icon shows.

    A configured field that is not an active quota (``is_active_quota()``:
    reported as ``null``, omitted - e.g. the five-hour session on plans
    without one - or listed under a code name before it applies) gives up
    its slot: the remaining configured fields move up, and the free slots
    are filled with the next bars of the popup, in popup order.  Only when
    no other quota is left does a configured field stay in place, showing 0%.

    Parameters
    ----------
    icon_fields : list[str]
        User-configured icon fields, each ``'field'`` or ``'field:mode'``.
    popup_fields : list[str]
        User-configured popup fields, the source of the fallback bars.
    usage_data : dict
        Raw API response dict.

    Returns
    -------
    list[tuple[str, str]]
        ``(field, mode)`` per icon slot, top first.  Fallback fields use
        the ``'utilization'`` mode.
    """
    configured = [tuple(entry.split(':', 1)) if ':' in entry else (entry, 'utilization') for entry in icon_fields]
    resolved = [slot for slot in configured if is_active_quota(slot[0], usage_data.get(slot[0]))]
    unavailable = [slot for slot in configured if slot not in resolved]

    shown = {field for field, _mode in resolved}
    fallbacks = _popup_fallbacks(shown, len(configured) - len(resolved), popup_fields, usage_data)
    resolved.extend((field, 'utilization') for field in fallbacks)
    resolved.extend(unavailable[:len(configured) - len(resolved)])

    return resolved


def _popup_fallbacks(shown_fields: set[str], count: int, popup_fields: list[str], usage_data: dict[str, Any]) -> list[str]:
    """Return up to *count* popup bars, in popup order, that are not among *shown_fields*."""
    fallbacks: list[str] = []
    for field in expand_popup_fields(popup_fields, usage_data):
        if len(fallbacks) >= count:
            break
        if field not in shown_fields:
            fallbacks.append(field)

    return fallbacks


def _uses_active_schedule(period_seconds: int, field: str | None) -> bool:
    """Choose scheduled pacing only for eligible long quota windows."""
    if not ACTIVE_SCHEDULE or period_seconds < 24 * 3600:
        return False
    if ACTIVE_SCHEDULE_FIELDS is None:
        return True
    if field is None:
        return False

    for pattern in ACTIVE_SCHEDULE_FIELDS:
        if re.fullmatch(re.escape(pattern).replace(r'\*', '.*'), field):
            return True

    return False


def _local_date(when: datetime) -> date:
    """Read a UTC instant's calendar date in the OS local timezone."""
    return when.astimezone().date()


def _local_boundary(day: date, hour: float) -> datetime:
    """Resolve each local boundary separately, including its date's DST offset."""
    local = datetime(day.year, day.month, day.day) + timedelta(hours=hour)
    return local.astimezone(timezone.utc)


def _schedule_period(reset: datetime, period_seconds: int) -> tuple[list[tuple[datetime, datetime]], list[datetime]]:
    """Clip and union active slots in a quota period, retaining workday starts.

    A previous day's overnight shift may overlap the period. Workday starts
    come from each day's first slot, so lunch breaks and overnight midnight
    crossings never create extra dividers. Intervals are unioned in UTC to
    count overlapping slots once and preserve actual DST elapsed seconds.

    Parameters
    ----------
    reset : datetime
        Timezone-aware end of the quota period.
    period_seconds : int
        Total duration of the period in seconds.

    Returns
    -------
    tuple
        ``(intervals, starts)``: the sorted, disjoint UTC intervals of active
        time inside the period, and the UTC start of every workday whose
        first slot begins strictly inside it.
    """
    end = reset.astimezone(timezone.utc)
    begin = end - timedelta(seconds=period_seconds)
    day = _local_date(begin) - timedelta(days=1)
    last_day = _local_date(end)
    intervals: list[tuple[datetime, datetime]] = []
    starts: list[datetime] = []

    while day <= last_day:
        day_starts: list[datetime] = []
        for start_hour, end_hour in (ACTIVE_SCHEDULE or {}).get(_WEEKDAYS[day.weekday()], []):
            slot_start = _local_boundary(day, start_hour)
            end_day = day + timedelta(days=1) if end_hour < start_hour else day
            slot_end = _local_boundary(end_day, end_hour)
            day_starts.append(slot_start)
            lower, upper = max(begin, slot_start), min(end, slot_end)
            if lower < upper:
                intervals.append((lower, upper))

        if day_starts:
            first_start = min(day_starts)
            if begin < first_start < end:
                starts.append(first_start)
        day += timedelta(days=1)

    merged: list[tuple[datetime, datetime]] = []
    for lower, upper in sorted(intervals):
        if merged and lower <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(upper, merged[-1][1]))
        else:
            merged.append((lower, upper))

    return merged, starts


def _active_seconds(intervals: list[tuple[datetime, datetime]], until: datetime) -> float:
    """Sum elapsed real seconds in disjoint active intervals, clamped at both ends."""
    return sum(max(0.0, (min(upper, until) - lower).total_seconds()) for lower, upper in intervals)


def elapsed_pct(resets_at: str, period_seconds: int, field: str | None = None) -> float | None:
    """Return elapsed percentage of a usage period, or None if not calculable.

    Parameters
    ----------
    resets_at : str
        ISO 8601 timestamp when the limit resets.
    period_seconds : int
        Total duration of the period in seconds (e.g. 18000 for 5h).
    field : str or None
        Quota field used to match optional ``active_schedule_fields`` patterns.
        Durations under one day always retain wall-clock pacing.

    Returns
    -------
    float or None
        Percentage of the period - or of its working time, when an active
        schedule applies to the field - that has already elapsed (0-100),
        or None if the value cannot be determined.
    """
    if not resets_at or period_seconds <= 0:
        return None

    try:
        reset = datetime.fromisoformat(resets_at)
        now = datetime.now(timezone.utc)
        remaining = (reset - now).total_seconds()
        elapsed = period_seconds - remaining

        if _uses_active_schedule(period_seconds, field):
            intervals, _ = _schedule_period(reset, period_seconds)
            total = _active_seconds(intervals, reset)
            if total > 0:
                return _active_seconds(intervals, now) / total * 100

        return max(0.0, min(100.0, elapsed / period_seconds * 100))
    except Exception:
        return None


def divider_positions(resets_at: str, period_seconds: int, field: str | None = None) -> list[float]:
    """Return relative positions (0.0-1.0) of divider marks within a usage period.

    Five-hour periods are split into five equal hour sections, independent
    of clock alignment.  Periods of a day or longer are subdivided at local
    midnight boundaries (e.g. seven day marks on a weekly bar), or - when an
    active schedule applies to the field - at workday starts, placed on the
    same working-time scale as the time marker.  Other
    sub-day periods have no dividers - their subdivision is a deliberate
    design decision for if and when such quota types exist.

    Parameters
    ----------
    resets_at : str
        ISO 8601 timestamp when the limit resets.
    period_seconds : int
        Total duration of the period in seconds.
    field : str or None
        Quota field used to match optional active-schedule patterns.

    Returns
    -------
    list[float]
        Divider positions within the period, each in the range (0.0, 1.0)
        exclusive.  Positions that would round to 0px at typical bar
        widths are omitted.
    """
    if not resets_at or period_seconds <= 0:
        return []

    try:
        reset_utc = datetime.fromisoformat(resets_at)

        if period_seconds < 24 * 3600:
            if period_seconds != PERIOD_5H:
                return []
            return [i / 5 for i in range(1, 5)]

        if _uses_active_schedule(period_seconds, field):
            intervals, starts = _schedule_period(reset_utc, period_seconds)
            total = _active_seconds(intervals, reset_utc)
            if total > 0:
                positions = {_active_seconds(intervals, start) / total for start in starts}
                return sorted(position for position in positions if 0.003 < position < 0.997)

        start_utc = reset_utc - timedelta(seconds=period_seconds)

        start_local = start_utc.astimezone()
        end_local = reset_utc.astimezone()

        # Walk local calendar days and convert each naive midnight separately:
        # astimezone() re-evaluates the UTC offset per date, so a DST
        # changeover inside the period keeps every divider on a true local
        # midnight (adding timedeltas would carry the period-start offset).
        day = start_local.date() + timedelta(days=1)

        positions = []
        while True:
            midnight = datetime(day.year, day.month, day.day).astimezone()
            if midnight >= end_local:
                break
            elapsed = (midnight - start_local).total_seconds()
            rel = elapsed / period_seconds
            if rel > 0.003:
                positions.append(rel)
            day += timedelta(days=1)

        return positions
    except Exception:
        return []


def _format_clock(when: datetime, clock_24h: bool) -> str:
    """Format a local time as a 24-hour ('14:30') or 12-hour ('2:30 PM') clock string."""
    if clock_24h:
        return when.strftime('%H:%M')

    # %I is zero-padded (e.g. '02:30 PM'); strip the single leading zero for '2:30 PM'.
    return when.strftime('%I:%M %p').lstrip('0')


def time_until(iso_str: str, clock_24h: bool | None = None) -> str:
    """Return human-readable reset time.

    Same day:  "Resets in 2h 20m (14:30)"
    Tomorrow:  "Resets tomorrow, 12:00"
    Later:     "Resets Sat., 12:00"

    Parameters
    ----------
    iso_str : str
        ISO 8601 timestamp when the limit resets.
    clock_24h : bool or None
        Format the clock time in 24-hour (True) or 12-hour (False) style.
        ``None`` falls back to the ``time_format`` setting.
    """
    if clock_24h is None:
        clock_24h = TIME_FORMAT == '24h'

    try:
        reset = datetime.fromisoformat(iso_str)
        now = datetime.now(timezone.utc)
        diff = reset - now
        total_seconds = diff.total_seconds()

        # Within the last minute before the reset (or the first moments after,
        # while the server-side reset propagates), show an imminent marker
        # instead of hiding the line - mirrors the native UI. Clearly-stale
        # timestamps (far in the past) still collapse to empty so we do not lie.
        if total_seconds < 60:
            return T['resets_imminent'] if total_seconds > -60 else ''

        total_min = int(total_seconds / 60)
        reset_local = reset.astimezone()
        today = datetime.now().date()
        if reset_local.second >= 30:
            reset_local = reset_local.replace(second=0) + timedelta(minutes=1)
        else:
            reset_local = reset_local.replace(second=0)
        reset_date = reset_local.date()
        time_str = _format_clock(reset_local, clock_24h)

        if reset_date == today:
            if total_min >= 60:
                duration = T['duration_hm'].format(h=total_min // 60, m=total_min % 60)
            else:
                duration = T['duration_m'].format(m=total_min)
            return T['resets_in'].format(duration=duration, clock=time_str)

        if reset_date == today + timedelta(days=1):
            return T['resets_tomorrow'].format(clock=time_str)

        wd = T['weekdays'][reset_local.weekday()]
        return T['resets_weekday'].format(day=wd, clock=time_str)
    except Exception:
        return ''


def _target_currency_symbol(currency: str | None) -> str:
    """Return the symbol to display for a currency amount.

    Precedence: an explicit ``currency_symbol`` user override (``None``
    means unset; an empty override means "no symbol"), then the billing
    currency reported by the API (its known symbol, or the ISO code itself
    as a fallback), then the system locale symbol.

    Parameters
    ----------
    currency : str or None
        ISO 4217 currency code from the API (e.g. ``'EUR'``), or None.
    """
    if CURRENCY_SYMBOL is not None:
        return CURRENCY_SYMBOL

    if currency:
        return _CURRENCY_SYMBOLS.get(currency.upper(), currency.upper())

    return _SYSTEM_CURRENCY_SYMBOL


def format_credits(minor_units: float, currency: str | None = None, decimal_places: int | None = None) -> str:
    """Format a minor-unit amount as a localized currency string.

    Uses the system locale for number formatting (decimal separator, symbol
    placement, grouping).  The displayed symbol follows the billing currency
    reported by the API when it differs from the system locale, so an account
    billed in a currency other than the system's still shows correctly.

    Parameters
    ----------
    minor_units : float
        Amount in the currency's minor units (e.g. 420.0 for 4.20 at two
        decimal places).
    currency : str or None
        ISO 4217 currency code from the API (e.g. ``'EUR'``).
    decimal_places : int or None
        Number of minor-unit decimal places reported by the API; defaults to
        two when not provided.
    """
    places = decimal_places if decimal_places is not None else 2
    amount = minor_units / (10 ** places)
    symbol = _target_currency_symbol(currency)

    try:
        formatted = _locale.currency(amount, grouping=True)

        # An empty symbol (explicit "no symbol" override) removes the system
        # symbol instead of leaving it in place.
        if symbol != _SYSTEM_CURRENCY_SYMBOL and _SYSTEM_CURRENCY_SYMBOL:
            formatted = formatted.replace(_SYSTEM_CURRENCY_SYMBOL, symbol).strip()

        return formatted
    except (ValueError, _locale.Error):
        if symbol:
            return f'{symbol}\u00a0{amount:.{places}f}'
        return f'{amount:.{places}f}'


def format_tooltip(data: dict[str, Any]) -> str:
    """Format usage data as short tooltip text.

    A field from ``tooltip_fields`` that is not an active quota gives up its
    line to the next bar of the popup, following the same rule as the tray
    icon bars (``resolve_icon_fields()``).  Lines carry no reset time, so
    their length does not depend on the locale.

    Parameters
    ----------
    data : dict
        Raw API response dict, or an error dict with an ``'error'`` key.
    """
    if 'error' in data:
        if data.get('auth_error'):
            return f"{T['auth_expired_label']}\n{T['auth_expired_short']}"
        error = data['error']
        server_msg = data.get('server_message')
        if server_msg:
            error += f' {server_msg}'
        return f"{T['error_label']}\n{error[:80]}"

    fields = [field for field in TOOLTIP_FIELDS if is_active_quota(field, data.get(field))]
    fields += _popup_fallbacks(set(fields), len(TOOLTIP_FIELDS) - len(fields), POPUP_FIELDS, data)

    lines = [T['tooltip_title']]
    for field in fields:
        lines.append(f"{tooltip_label(field)}: {data[field]['utilization']:.0f}%")

    return '\n'.join(lines)


def fit_tooltip(text: str, max_length: int | None) -> str:
    """Shorten tooltip text to fit the tray's length limit.

    Whole lines are dropped from the end, so no line is ever shown cut off.
    Only a first line that is too long on its own (e.g. a long account
    prefix) is cut to the limit.

    Parameters
    ----------
    text : str
        Tooltip text, lines separated by ``'\\n'``.
    max_length : int or None
        Maximum length in UTF-16 code units - the unit of the Windows tooltip
        buffer, where a character outside the Basic Multilingual Plane (e.g.
        an emoji in a directory name) takes two - or ``None`` for no limit.
    """
    if max_length is None:
        return text

    lines = text.split('\n')
    while len(lines) > 1 and _utf16_length('\n'.join(lines)) > max_length:
        lines.pop()

    fitted = '\n'.join(lines)
    while _utf16_length(fitted) > max_length:
        fitted = fitted[:-1]

    return fitted


def _utf16_length(text: str) -> int:
    """Return the length of *text* in UTF-16 code units."""
    return len(text.encode('utf-16-le', errors='surrogatepass')) // 2

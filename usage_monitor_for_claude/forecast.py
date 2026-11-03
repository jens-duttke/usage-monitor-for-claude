"""
Forecast
=========

Pure functions behind the popup's usage forecast: an in-memory log of the
readings seen while the app runs, and a projection of the current pace to
the end of the quota window.

The projection uses the same elapsed-time scale as the popup's time marker
(:func:`~usage_monitor_for_claude.formatting.elapsed_fraction`), so a quota
is forecast to run out exactly when its bar is drawn in the warning color,
and with an active schedule the projected line stays flat outside working
hours.  Nothing here is persisted: the log starts empty on every launch.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from .formatting import divider_positions, elapsed_fraction, field_period, format_clock, is_active_quota
from .i18n import T
from .settings import TIME_FORMAT

__all__ = ['MAX_SAMPLES', 'record_samples', 'usage_forecast']

# Readings kept per quota - more than a week of polls at the normal cadence,
# since unchanged readings collapse into the ends of a flat run.
MAX_SAMPLES = 2000

# A reset window that moves by more than this is a new window, not jitter
# (the API reports the same reset with varying sub-second precision).
_WINDOW_TOLERANCE_SECONDS = 3600

# Below this share of elapsed time the pace is too noisy to project.
_MIN_ELAPSED_FRACTION = 0.02

# Points of the projected line; enough for flat off-hour steps to stay sharp.
_PROJECTION_STEPS = 120

# Bisection steps for the run-out instant - below a second over a week.
_BISECTION_STEPS = 40


def record_samples(history: dict[str, dict[str, Any]], usage: dict[str, Any], now: float) -> dict[str, dict[str, Any]]:
    """Return *history* extended by the readings in a successful usage response.

    Each active quota with a reset window keeps the readings of its current
    window as ``(unix_time, utilization)`` pairs.  A new window - a reset
    time that moved, or a utilization that dropped (a reset or an account
    switch) - starts the log over.  A reading equal to the two before it
    only moves the end of that flat run, so a quiet week stays small.

    Parameters
    ----------
    history : dict
        Previous result of this function (``{}`` initially).  Not modified.
    usage : dict
        Raw API response dict.
    now : float
        Unix time of the reading.

    Returns
    -------
    dict
        ``{field: {'resets_at': str, 'samples': tuple}}`` for every quota
        in *usage* that has a reset window.
    """
    result: dict[str, dict[str, Any]] = {}
    for field, entry in usage.items():
        if field == 'extra_usage' or not is_active_quota(field, entry) or not entry.get('resets_at'):
            continue

        # The response reaches this function unguarded from the poll loop, so a
        # changed value type must skip the reading instead of raising.
        utilization = entry.get('utilization')
        if not isinstance(utilization, (int, float)):
            continue

        resets_at = entry['resets_at']
        pct = float(utilization)
        previous = history.get(field)
        samples: tuple[tuple[float, float], ...] = ()
        if previous and _same_window(previous['resets_at'], resets_at) and pct >= previous['samples'][-1][1]:
            samples = previous['samples']

        if len(samples) >= 2 and samples[-1][1] == pct and samples[-2][1] == pct:
            samples = samples[:-1] + ((now, pct),)
        else:
            samples = (samples + ((now, pct),))[-MAX_SAMPLES:]

        result[field] = {'resets_at': resets_at, 'samples': samples}

    return result


def usage_forecast(field: str, usage: dict[str, Any], history: dict[str, dict[str, Any]], now: datetime) -> dict[str, Any] | None:
    """Build the popup's forecast chart for one quota.

    The pace is the average over the window so far: the current utilization
    divided by the elapsed share of the window.  Projected forward on the
    same scale, it gives the utilization at reset, or - when it would pass
    100% first - the time the quota runs out.  Usage comes in bursts, so the
    summary states this as an approximation at the current pace; an exhausted
    quota has nothing left to project and gets no summary.

    Parameters
    ----------
    field : str
        Quota field to forecast.
    usage : dict
        Raw API response dict.
    history : dict
        Readings logged by :func:`record_samples`.
    now : datetime
        Timezone-aware current time.

    Returns
    -------
    dict or None
        Chart payload with ``x`` as the wall-clock share of the window
        (0.0-1.0) and ``y`` as utilization (0-100), or None when the field
        has no period, no reset window, or no utilization.
    """
    entry = usage.get(field)
    period = field_period(field)
    if not period or not isinstance(entry, dict) or entry.get('utilization') is None or not entry.get('resets_at'):
        return None

    fraction_at = elapsed_fraction(entry['resets_at'], period, field=field)
    if fraction_at is None:
        return None

    reset = datetime.fromisoformat(entry['resets_at'])
    start = reset - timedelta(seconds=period)
    if not start <= now < reset:
        return None

    pct = float(entry['utilization'])

    def wall_x(when: datetime) -> float:
        return max(0.0, min(1.0, (when - start).total_seconds() / period))

    now_x = wall_x(now)
    measured = []
    logged = history.get(field)
    if logged and _same_window(logged['resets_at'], entry['resets_at']):
        for timestamp, sample_pct in logged['samples']:
            sample_time = datetime.fromtimestamp(timestamp, timezone.utc)
            if start <= sample_time <= now:
                measured.append([wall_x(sample_time), min(100.0, sample_pct)])
    # The current reading is usually the last logged one; draw it once.
    if measured and measured[-1][0] >= now_x - 1e-6:
        measured.pop()
    measured.append([now_x, min(100.0, pct)])

    forecast: dict[str, Any] = {
        'actual': [[0.0, 0.0]] + measured,
        # Before the first logged reading only the window start (0%) is known.
        'measured_from': measured[0][0],
        'now_x': now_x,
        'ticks': _ticks(start, reset, period),
        'projection': [],
        'runs_out': False,
        'summary': '',
    }

    elapsed_now = fraction_at(now)
    if elapsed_now < _MIN_ELAPSED_FRACTION:
        return forecast

    if pct >= 100:
        forecast['runs_out'] = True
        return forecast

    rate = pct / elapsed_now
    end = _run_out_time(fraction_at, 100 / rate, now, reset) if rate > 100 else reset

    projection = []
    span = (end - now).total_seconds()
    for step in range(_PROJECTION_STEPS + 1):
        when = now + timedelta(seconds=span * step / _PROJECTION_STEPS)
        projection.append([wall_x(when), min(100.0, rate * fraction_at(when))])

    forecast['projection'] = projection
    forecast['runs_out'] = end < reset
    if end < reset:
        forecast['summary'] = T['forecast_runs_out'].format(when=_approximate_time(end, period))
    else:
        forecast['summary'] = T['forecast_at_reset'].format(pct=f'{min(100.0, rate):.0f}')

    return forecast


def _same_window(previous_reset: str, reset: str) -> bool:
    """Whether two reset timestamps name the same quota window."""
    try:
        moved = datetime.fromisoformat(reset) - datetime.fromisoformat(previous_reset)
        return abs(moved.total_seconds()) <= _WINDOW_TOLERANCE_SECONDS
    except (TypeError, ValueError):
        return False


def _run_out_time(fraction_at: Callable[[datetime], float], target: float, now: datetime, reset: datetime) -> datetime:
    """Find the first instant between *now* and *reset* whose elapsed share reaches *target*.

    The elapsed share never decreases over time, so bisection converges;
    with a schedule it settles on the end of the working time that crosses
    the target, never inside a flat off-hour stretch.
    """
    low, high = now, reset
    for _ in range(_BISECTION_STEPS):
        middle = low + (high - low) / 2
        if fraction_at(middle) >= target:
            high = middle
        else:
            low = middle

    return high


def _ticks(start: datetime, reset: datetime, period: int) -> list[dict[str, Any]]:
    """Return x-axis ticks: local midnights labelled with the day they start, or unlabelled hours."""
    positions = divider_positions(reset.isoformat(), period)
    if period < 24 * 3600:
        return [{'x': position, 'label': ''} for position in positions]

    ticks = []
    for position in positions:
        midnight = (start + timedelta(seconds=period * position)).astimezone()
        ticks.append({'x': position, 'label': T['weekdays'][midnight.weekday()]})

    return ticks


def _approximate_time(when: datetime, period: int) -> str:
    """Format a projected instant no finer than the projection deserves: the weekday, or the nearest hour within a session."""
    local = when.astimezone()
    if period >= 24 * 3600:
        return T['weekdays'][local.weekday()]

    nearest_hour = (local + timedelta(minutes=30)).replace(minute=0, second=0, microsecond=0)

    return format_clock(nearest_hour, TIME_FORMAT == '24h')

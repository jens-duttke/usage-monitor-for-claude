"""
Token Stats
============

Aggregates today's Claude Code token usage per model from the local JSONL
transcripts in the Claude config directory.  Read-only and fully offline:
no network access, no credential handling, no file writes.

The four token categories (input, output, cache read, cache write) are kept
separate because they are billed at very different rates; the popup shows
output as the primary number rather than a single misleading sum.
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any

__all__ = ['collect_token_stats', 'format_tokens', 'pretty_model_name']

_FAMILIES = ('fable', 'opus', 'sonnet', 'haiku')
_TOKEN_UNITS = ((1_000_000_000, 'B'), (1_000_000, 'M'), (1_000, 'k'))
_DATE_TOKEN = re.compile(r'^\d{8}$')


def _projects_dir() -> Path:
    """Return the Claude Code projects directory, honoring ``CLAUDE_CONFIG_DIR``."""
    config_dir = os.environ.get('CLAUDE_CONFIG_DIR')
    base = Path(config_dir) if config_dir else Path.home() / '.claude'
    return base / 'projects'


def pretty_model_name(model_id: str) -> str:
    """Return a short display name for a model identifier.

    Handles both current identifiers, where the version follows the family
    (``claude-sonnet-4-5-20250929`` -> ``Sonnet 4.5``), and legacy ones,
    where it precedes the family (``claude-3-5-sonnet-20241022`` ->
    ``Sonnet 3.5``).  A trailing 8-digit date snapshot is ignored, and
    multi-segment versions are joined with dots so ``opus-4-10`` renders as
    ``Opus 4.10`` rather than being treated as a float.

    Parameters
    ----------
    model_id : str
        Raw model identifier, e.g. ``'claude-fable-5'``.

    Returns
    -------
    str
        Display name like ``'Fable 5'``.  Unrecognized identifiers (no known
        family) are returned unchanged.
    """
    tokens = [t for t in model_id.lower().split('-') if t != 'claude' and not _DATE_TOKEN.match(t)]

    family = next((t for t in tokens if t in _FAMILIES), None)
    if family is None:
        return model_id

    version = '.'.join(t for t in tokens if t != family and t.isdigit())
    return f'{family.capitalize()} {version}' if version else family.capitalize()


def format_tokens(count: int) -> str:
    """Format a token count as a short human-readable string (e.g. ``'3.4M'``).

    Rounds to one decimal and promotes to the next larger unit when rounding
    would otherwise produce a four-digit mantissa, so ``999_999`` formats as
    ``'1.0M'`` rather than ``'1000.0k'``.

    Parameters
    ----------
    count : int
        Token count to format.
    """
    for index, (divisor, suffix) in enumerate(_TOKEN_UNITS):
        if count >= divisor:
            value = round(count / divisor, 1)
            if value >= 1000 and index > 0:
                larger_divisor, larger_suffix = _TOKEN_UNITS[index - 1]
                return f'{count / larger_divisor:.1f}{larger_suffix}'
            return f'{value:.1f}{suffix}'
    return str(count)


def collect_token_stats(now: datetime | None = None) -> list[dict[str, Any]]:
    """Aggregate today's per-model token usage from local transcripts.

    Scans ``<config>/projects/*/*.jsonl`` for assistant messages with usage
    data since local midnight.  Entries are deduplicated on the
    ``(message id, request id)`` pair - including entries that lack a message
    id, which would otherwise be counted repeatedly across retries.

    Parameters
    ----------
    now : datetime or None
        Reference time for the local-midnight cutoff; defaults to the current
        local time (the parameter exists for testability).

    Returns
    -------
    list[dict]
        One entry per model, sorted by output tokens descending.  Each entry
        is ``{'model', 'name', 'output', 'input', 'cache_read',
        'cache_write'}``.  Empty when no transcripts exist or nothing was used
        today.
    """
    reference = now if now is not None else datetime.now().astimezone()
    midnight = reference.astimezone().replace(hour=0, minute=0, second=0, microsecond=0)
    cutoff = midnight.timestamp()

    totals: dict[str, dict[str, int]] = {}
    seen: set[tuple[Any, Any]] = set()

    for transcript_path in _projects_dir().glob('*/*.jsonl'):
        try:
            if transcript_path.stat().st_mtime < cutoff:
                continue
            with open(transcript_path, 'r', encoding='utf-8', errors='ignore') as transcript_file:
                for line in transcript_file:
                    if '"usage"' not in line:
                        continue

                    try:
                        record = json.loads(line)
                    except (ValueError, TypeError):
                        continue

                    message = record.get('message') or {}
                    usage = message.get('usage') or {}
                    model = message.get('model') or ''
                    if not usage or not model or model == '<synthetic>':
                        continue

                    timestamp_str = record.get('timestamp') or ''
                    try:
                        timestamp = datetime.fromisoformat(timestamp_str.replace('Z', '+00:00')).timestamp()
                    except ValueError:
                        continue
                    if timestamp < cutoff:
                        continue

                    dedup_key = (message.get('id'), record.get('requestId'))
                    if dedup_key in seen:
                        continue
                    seen.add(dedup_key)

                    model_totals = totals.setdefault(
                        model, {'input': 0, 'output': 0, 'cache_read': 0, 'cache_write': 0},
                    )
                    model_totals['input'] += usage.get('input_tokens') or 0
                    model_totals['output'] += usage.get('output_tokens') or 0
                    model_totals['cache_read'] += usage.get('cache_read_input_tokens') or 0
                    model_totals['cache_write'] += usage.get('cache_creation_input_tokens') or 0
        except OSError:
            continue

    stats = []
    for model, model_totals in totals.items():
        stats.append({
            'model': model,
            'name': pretty_model_name(model),
            'output': model_totals['output'],
            'input': model_totals['input'],
            'cache_read': model_totals['cache_read'],
            'cache_write': model_totals['cache_write'],
        })

    stats.sort(key=lambda entry: (-entry['output'], entry['name']))
    return stats

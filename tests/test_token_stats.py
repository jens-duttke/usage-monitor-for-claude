"""
Token Stats Tests
==================

Unit tests for token_stats: transcript aggregation, deduplication,
output-primary ordering, model name parsing, and token formatting.
"""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

from usage_monitor_for_claude.token_stats import collect_token_stats, format_tokens, pretty_model_name


def _entry(model, timestamp, message_id='msg', request_id='req',
           input_tokens=10, output_tokens=20, cache_read=30, cache_write=40):
    """Build one transcript JSONL line with usage data."""
    message = {'model': model, 'usage': {
        'input_tokens': input_tokens,
        'output_tokens': output_tokens,
        'cache_read_input_tokens': cache_read,
        'cache_creation_input_tokens': cache_write,
    }}
    if message_id is not None:
        message['id'] = message_id
    return json.dumps({'timestamp': timestamp.isoformat(), 'requestId': request_id, 'message': message})


class CollectTokenStatsTest(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.config_dir = Path(self._tmp.name)
        self.project_dir = self.config_dir / 'projects' / 'proj'
        self.project_dir.mkdir(parents=True)

        patcher = mock.patch.dict(os.environ, {'CLAUDE_CONFIG_DIR': str(self.config_dir)})
        patcher.start()
        self.addCleanup(patcher.stop)

        self.now = datetime.now().astimezone().replace(hour=12, minute=0, second=0, microsecond=0)

    def _write(self, filename, lines):
        (self.project_dir / filename).write_text('\n'.join(lines), encoding='utf-8')

    def test_categories_kept_separate_not_summed(self):
        self._write('a.jsonl', [_entry('claude-fable-5', self.now, message_id='m1')])
        entry = collect_token_stats(now=self.now)[0]
        self.assertEqual(entry['output'], 20)
        self.assertEqual(entry['input'], 10)
        self.assertEqual(entry['cache_read'], 30)
        self.assertEqual(entry['cache_write'], 40)
        self.assertNotIn('total', entry)

    def test_aggregates_across_entries(self):
        self._write('a.jsonl', [
            _entry('claude-fable-5', self.now, message_id='m1', output_tokens=20),
            _entry('claude-fable-5', self.now, message_id='m2', output_tokens=5),
        ])
        self.assertEqual(collect_token_stats(now=self.now)[0]['output'], 25)

    def test_sorted_by_output_descending(self):
        # Model with huge cache but small output must rank below higher-output model.
        self._write('a.jsonl', [
            _entry('claude-opus-4-8', self.now, message_id='m1', output_tokens=100, cache_read=1_000_000_000),
            _entry('claude-fable-5', self.now, message_id='m2', output_tokens=200, cache_read=0),
        ])
        names = [e['name'] for e in collect_token_stats(now=self.now)]
        self.assertEqual(names, ['Fable 5', 'Opus 4.8'])

    def test_dedup_applies_even_without_message_id(self):
        # Two id-less entries sharing a request id must be counted once.
        self._write('a.jsonl', [
            _entry('claude-fable-5', self.now, message_id=None, request_id='r1', output_tokens=10),
            _entry('claude-fable-5', self.now, message_id=None, request_id='r1', output_tokens=10),
        ])
        self.assertEqual(collect_token_stats(now=self.now)[0]['output'], 10)

    def test_dedup_on_message_and_request_pair(self):
        self._write('a.jsonl', [
            _entry('claude-fable-5', self.now, message_id='m1', request_id='r1', output_tokens=10),
            _entry('claude-fable-5', self.now, message_id='m1', request_id='r1', output_tokens=10),
            _entry('claude-fable-5', self.now, message_id='m1', request_id='r2', output_tokens=10),
        ])
        self.assertEqual(collect_token_stats(now=self.now)[0]['output'], 20)

    def test_entries_before_midnight_excluded(self):
        yesterday = self.now - timedelta(days=1)
        self._write('a.jsonl', [
            _entry('claude-fable-5', yesterday, message_id='m1', output_tokens=999),
            _entry('claude-fable-5', self.now, message_id='m2', output_tokens=7),
        ])
        self.assertEqual(collect_token_stats(now=self.now)[0]['output'], 7)

    def test_files_not_modified_today_skipped(self):
        self._write('old.jsonl', [_entry('claude-fable-5', self.now, message_id='m1')])
        midnight = self.now.replace(hour=0, minute=0, second=0, microsecond=0)
        old = (midnight - timedelta(hours=1)).timestamp()
        os.utime(self.project_dir / 'old.jsonl', (old, old))
        self.assertEqual(collect_token_stats(now=self.now), [])

    def test_malformed_and_synthetic_ignored(self):
        self._write('a.jsonl', [
            'not json "usage"',
            json.dumps({'timestamp': self.now.isoformat(), 'message': {'model': '<synthetic>', 'usage': {'output_tokens': 5}}}),
            json.dumps({'timestamp': 'bad', 'message': {'model': 'claude-fable-5', 'usage': {'output_tokens': 5}}}),
            _entry('claude-fable-5', self.now, message_id='m1', output_tokens=8),
        ])
        stats = collect_token_stats(now=self.now)
        self.assertEqual(len(stats), 1)
        self.assertEqual(stats[0]['output'], 8)

    def test_empty_when_no_transcripts(self):
        self.assertEqual(collect_token_stats(now=self.now), [])


class PrettyModelNameTest(unittest.TestCase):

    def test_current_identifiers(self):
        self.assertEqual(pretty_model_name('claude-fable-5'), 'Fable 5')
        self.assertEqual(pretty_model_name('claude-opus-4-8'), 'Opus 4.8')
        self.assertEqual(pretty_model_name('claude-haiku-4-5-20251001'), 'Haiku 4.5')

    def test_two_digit_minor_not_treated_as_float(self):
        self.assertEqual(pretty_model_name('claude-opus-4-10'), 'Opus 4.10')

    def test_legacy_version_before_family(self):
        self.assertEqual(pretty_model_name('claude-3-5-sonnet-20241022'), 'Sonnet 3.5')
        self.assertEqual(pretty_model_name('claude-3-opus-20240229'), 'Opus 3')

    def test_unknown_model_unchanged(self):
        self.assertEqual(pretty_model_name('gpt-5'), 'gpt-5')


class FormatTokensTest(unittest.TestCase):

    def test_plain_below_thousand(self):
        self.assertEqual(format_tokens(0), '0')
        self.assertEqual(format_tokens(999), '999')

    def test_unit_scaling(self):
        self.assertEqual(format_tokens(1_500), '1.5k')
        self.assertEqual(format_tokens(1_000_000), '1.0M')
        self.assertEqual(format_tokens(1_000_000_000), '1.0B')

    def test_rounding_promotes_unit(self):
        self.assertEqual(format_tokens(999_999), '1.0M')
        self.assertEqual(format_tokens(999_999_999), '1.0B')

    def test_no_false_promotion(self):
        self.assertEqual(format_tokens(999_400), '999.4k')


if __name__ == '__main__':
    unittest.main()

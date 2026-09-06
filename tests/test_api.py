"""
API Client Tests
=================

Unit tests for read_access_token(), fetch_usage() and
fetch_prepaid_credits().
"""
from __future__ import annotations

import json
import unittest
from tempfile import TemporaryDirectory
from pathlib import Path
from unittest.mock import MagicMock, patch

from usage_monitor_for_claude.api import (
    API_URL_USAGE, _extract_server_message, _merge_scoped_limits, _model_slug, _normalize_prepaid_credits, _parse_retry_after,
    fetch_prepaid_credits, fetch_usage, read_access_token,
)
from usage_monitor_for_claude.i18n import LOCALE_DIR

EN = json.loads((LOCALE_DIR / 'en.json').read_text(encoding='utf-8'))


# ---------------------------------------------------------------------------
# CLAUDE_CONFIG_DIR
# ---------------------------------------------------------------------------

class TestClaudeConfigDir(unittest.TestCase):
    """Tests for CLAUDE_CONFIG_DIR resolution."""

    def test_default_uses_home_claude(self):
        """Without CLAUDE_CONFIG_DIR env var, defaults to ~/.claude/."""
        with patch.dict('os.environ', {}, clear=False):
            # Remove CLAUDE_CONFIG_DIR if it happens to be set
            env = {k: v for k, v in __import__('os').environ.items() if k != 'CLAUDE_CONFIG_DIR'}
            with patch.dict('os.environ', env, clear=True):
                import importlib
                import usage_monitor_for_claude.api as api_mod
                importlib.reload(api_mod)
                try:
                    self.assertEqual(api_mod.CLAUDE_CONFIG_DIR, Path.home() / '.claude')
                    self.assertEqual(api_mod.CLAUDE_CREDENTIALS, Path.home() / '.claude' / '.credentials.json')
                finally:
                    importlib.reload(api_mod)

    def test_custom_config_dir(self):
        """CLAUDE_CONFIG_DIR env var overrides the default path."""
        with TemporaryDirectory() as tmp:
            with patch.dict('os.environ', {'CLAUDE_CONFIG_DIR': tmp}):
                import importlib
                import usage_monitor_for_claude.api as api_mod
                importlib.reload(api_mod)
                try:
                    self.assertEqual(api_mod.CLAUDE_CONFIG_DIR, Path(tmp))
                    self.assertEqual(api_mod.CLAUDE_CREDENTIALS, Path(tmp) / '.credentials.json')
                finally:
                    importlib.reload(api_mod)

    def test_empty_config_dir_uses_default(self):
        """Empty CLAUDE_CONFIG_DIR env var falls back to default."""
        with patch.dict('os.environ', {'CLAUDE_CONFIG_DIR': ''}):
            import importlib
            import usage_monitor_for_claude.api as api_mod
            importlib.reload(api_mod)
            try:
                self.assertEqual(api_mod.CLAUDE_CONFIG_DIR, Path.home() / '.claude')
            finally:
                importlib.reload(api_mod)


# ---------------------------------------------------------------------------
# read_access_token
# ---------------------------------------------------------------------------

class TestReadAccessToken(unittest.TestCase):
    """Tests for read_access_token()."""

    def test_file_missing(self):
        """Missing credentials file returns None."""
        with TemporaryDirectory() as tmp:
            fake_path = Path(tmp) / 'nonexistent.json'
            with patch('usage_monitor_for_claude.api.CLAUDE_CREDENTIALS', fake_path):
                self.assertIsNone(read_access_token())

    def test_valid_token(self):
        """Extracts token from well-formed credentials file."""
        creds = {'claudeAiOauth': {'accessToken': 'sk-test-123'}}
        with TemporaryDirectory() as tmp:
            creds_file = Path(tmp) / 'creds.json'
            creds_file.write_text(json.dumps(creds))
            with patch('usage_monitor_for_claude.api.CLAUDE_CREDENTIALS', creds_file):
                self.assertEqual(read_access_token(), 'sk-test-123')

    def test_unchanged_file_is_parsed_once(self):
        """Repeated reads reuse the token while credentials metadata is unchanged."""
        creds = {'claudeAiOauth': {'accessToken': 'sk-cached'}}
        with TemporaryDirectory() as tmp:
            creds_file = Path(tmp) / 'creds.json'
            creds_file.write_text(json.dumps(creds))
            with patch('usage_monitor_for_claude.api.CLAUDE_CREDENTIALS', creds_file), \
                 patch('usage_monitor_for_claude.api.json.loads', wraps=json.loads) as mock_loads:
                self.assertEqual(read_access_token(), 'sk-cached')
                self.assertEqual(read_access_token(), 'sk-cached')

            self.assertEqual(mock_loads.call_count, 1)

    def test_changed_file_is_reparsed(self):
        """A credentials change invalidates the cached token immediately."""
        with TemporaryDirectory() as tmp:
            creds_file = Path(tmp) / 'creds.json'
            creds_file.write_text(json.dumps({'claudeAiOauth': {'accessToken': 'sk-old'}}))
            with patch('usage_monitor_for_claude.api.CLAUDE_CREDENTIALS', creds_file), \
                 patch('usage_monitor_for_claude.api.json.loads', wraps=json.loads) as mock_loads:
                self.assertEqual(read_access_token(), 'sk-old')
                creds_file.write_text(json.dumps({'claudeAiOauth': {'accessToken': 'sk-new-token'}}))
                self.assertEqual(read_access_token(), 'sk-new-token')

            self.assertEqual(mock_loads.call_count, 2)

    def test_unchanged_malformed_file_is_not_reparsed(self):
        """A malformed file keeps returning no token without repeated parsing."""
        with TemporaryDirectory() as tmp:
            creds_file = Path(tmp) / 'creds.json'
            creds_file.write_text('not json')
            with patch('usage_monitor_for_claude.api.CLAUDE_CREDENTIALS', creds_file), \
                 patch('usage_monitor_for_claude.api.json.loads', wraps=json.loads) as mock_loads:
                self.assertIsNone(read_access_token())
                self.assertIsNone(read_access_token())

            self.assertEqual(mock_loads.call_count, 1)



    def test_malformed_json(self):
        """Malformed JSON returns None."""
        with TemporaryDirectory() as tmp:
            creds_file = Path(tmp) / 'creds.json'
            creds_file.write_text('not json')
            with patch('usage_monitor_for_claude.api.CLAUDE_CREDENTIALS', creds_file):
                self.assertIsNone(read_access_token())

    def test_missing_oauth_key(self):
        """Missing claudeAiOauth key returns None."""
        with TemporaryDirectory() as tmp:
            creds_file = Path(tmp) / 'creds.json'
            creds_file.write_text('{"otherKey": {}}')
            with patch('usage_monitor_for_claude.api.CLAUDE_CREDENTIALS', creds_file):
                self.assertIsNone(read_access_token())

    def test_missing_access_token_key(self):
        """Missing accessToken key returns None."""
        creds = {'claudeAiOauth': {'refreshToken': 'rt-123'}}
        with TemporaryDirectory() as tmp:
            creds_file = Path(tmp) / 'creds.json'
            creds_file.write_text(json.dumps(creds))
            with patch('usage_monitor_for_claude.api.CLAUDE_CREDENTIALS', creds_file):
                self.assertIsNone(read_access_token())

    def test_empty_token_string(self):
        """Empty token string returns None (falsy check)."""
        creds = {'claudeAiOauth': {'accessToken': ''}}
        with TemporaryDirectory() as tmp:
            creds_file = Path(tmp) / 'creds.json'
            creds_file.write_text(json.dumps(creds))
            with patch('usage_monitor_for_claude.api.CLAUDE_CREDENTIALS', creds_file):
                self.assertIsNone(read_access_token())

    def test_read_error_returns_none(self):
        """An OS-level read failure (e.g. a read racing a concurrent write) returns None instead of raising."""
        with TemporaryDirectory() as tmp:
            creds_file = Path(tmp) / 'creds.json'
            creds_file.write_text('{"claudeAiOauth": {"accessToken": "sk-test-123"}}')
            with patch('usage_monitor_for_claude.api.CLAUDE_CREDENTIALS', creds_file), \
                 patch.object(Path, 'read_text', side_effect=PermissionError('locked')):
                self.assertIsNone(read_access_token())

    def test_null_oauth_value_returns_none(self):
        """A claudeAiOauth key holding JSON null (e.g. after a logout) returns None instead of raising."""
        with TemporaryDirectory() as tmp:
            creds_file = Path(tmp) / 'creds.json'
            creds_file.write_text('{"claudeAiOauth": null}')
            with patch('usage_monitor_for_claude.api.CLAUDE_CREDENTIALS', creds_file):
                self.assertIsNone(read_access_token())

    def test_non_object_top_level_returns_none(self):
        """Valid JSON with a non-object top level (list, string, number) returns None instead of raising."""
        for content in ('[]', '"token"', '42', 'null'):
            with self.subTest(content=content), TemporaryDirectory() as tmp:
                creds_file = Path(tmp) / 'creds.json'
                creds_file.write_text(content)
                with patch('usage_monitor_for_claude.api.CLAUDE_CREDENTIALS', creds_file):
                    self.assertIsNone(read_access_token())

    def test_non_dict_oauth_value_returns_none(self):
        """A claudeAiOauth key holding a non-object value returns None instead of raising."""
        with TemporaryDirectory() as tmp:
            creds_file = Path(tmp) / 'creds.json'
            creds_file.write_text('{"claudeAiOauth": "sk-test-123"}')
            with patch('usage_monitor_for_claude.api.CLAUDE_CREDENTIALS', creds_file):
                self.assertIsNone(read_access_token())


# ---------------------------------------------------------------------------
# fetch_usage
# ---------------------------------------------------------------------------

@patch('usage_monitor_for_claude.api.T', EN)
class TestFetchUsage(unittest.TestCase):
    """Tests for fetch_usage()."""

    @patch('usage_monitor_for_claude.api.api_headers', return_value=None)
    def test_no_token_returns_error(self, _mock_headers):
        """Missing token returns no_token error."""
        result = fetch_usage()
        self.assertEqual(result, {'error': EN['no_token']})

    @patch('usage_monitor_for_claude.api.requests.get')
    @patch('usage_monitor_for_claude.api.api_headers', return_value={'Authorization': 'Bearer test'})
    def test_success(self, _mock_headers, mock_get):
        """Successful response returns parsed JSON."""
        mock_resp = MagicMock()
        mock_resp.json.return_value = {'five_hour': {'utilization': 42.0}}
        mock_get.return_value = mock_resp

        result = fetch_usage()

        self.assertEqual(result, {'five_hour': {'utilization': 42.0}})
        mock_get.assert_called_once_with(API_URL_USAGE, headers={'Authorization': 'Bearer test'}, timeout=10)

    @patch('usage_monitor_for_claude.api.requests.get')
    @patch('usage_monitor_for_claude.api.api_headers', return_value={'Authorization': 'Bearer test'})
    def test_connection_error(self, _mock_headers, mock_get):
        """ConnectionError returns connection_error message."""
        import requests
        mock_get.side_effect = requests.ConnectionError()

        result = fetch_usage()

        self.assertEqual(result, {'error': EN['connection_error']})

    @patch('usage_monitor_for_claude.api.requests.get')
    @patch('usage_monitor_for_claude.api.api_headers', return_value={'Authorization': 'Bearer test'})
    def test_certificate_error(self, _mock_headers, mock_get):
        """SSLError returns certificate_error, not the generic connection_error it subclasses."""
        import requests
        mock_get.side_effect = requests.exceptions.SSLError()

        result = fetch_usage()

        self.assertEqual(result, {'error': EN['certificate_error']})

    @patch('usage_monitor_for_claude.api.requests.get')
    @patch('usage_monitor_for_claude.api.api_headers', return_value={'Authorization': 'Bearer test'})
    def test_401_returns_auth_error(self, _mock_headers, mock_get):
        """HTTP 401 returns auth_error with flag."""
        import requests
        mock_resp = MagicMock()
        mock_resp.status_code = 401
        mock_resp.json.return_value = {}
        mock_resp.raise_for_status.side_effect = requests.HTTPError(response=mock_resp)
        mock_get.return_value = mock_resp

        result = fetch_usage()

        self.assertEqual(result['error'], EN['auth_expired'])
        self.assertTrue(result['auth_error'])

    @patch('usage_monitor_for_claude.api.requests.get')
    @patch('usage_monitor_for_claude.api.api_headers', return_value={'Authorization': 'Bearer test'})
    def test_server_error_500(self, _mock_headers, mock_get):
        """HTTP 500 returns server_error with status code."""
        import requests
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_resp.json.return_value = {}
        mock_resp.raise_for_status.side_effect = requests.HTTPError(response=mock_resp)
        mock_get.return_value = mock_resp

        result = fetch_usage()

        self.assertEqual(result, {'error': EN['server_error'].format(code=500)})

    @patch('usage_monitor_for_claude.api.requests.get')
    @patch('usage_monitor_for_claude.api.api_headers', return_value={'Authorization': 'Bearer test'})
    def test_server_error_503(self, _mock_headers, mock_get):
        """HTTP 503 returns server_error with status code."""
        import requests
        mock_resp = MagicMock()
        mock_resp.status_code = 503
        mock_resp.json.return_value = {}
        mock_resp.raise_for_status.side_effect = requests.HTTPError(response=mock_resp)
        mock_get.return_value = mock_resp

        result = fetch_usage()

        self.assertEqual(result, {'error': EN['server_error'].format(code=503)})

    @patch('usage_monitor_for_claude.api.requests.get')
    @patch('usage_monitor_for_claude.api.api_headers', return_value={'Authorization': 'Bearer test'})
    def test_client_http_error(self, _mock_headers, mock_get):
        """Non-5xx, non-401 HTTP error returns http_error with status code."""
        import requests
        mock_resp = MagicMock()
        mock_resp.status_code = 403
        mock_resp.json.return_value = {}
        mock_resp.raise_for_status.side_effect = requests.HTTPError(response=mock_resp)
        mock_get.return_value = mock_resp

        result = fetch_usage()

        self.assertEqual(result, {'error': EN['http_error'].format(code=403)})

    @patch('usage_monitor_for_claude.api.requests.get')
    @patch('usage_monitor_for_claude.api.api_headers', return_value={'Authorization': 'Bearer test'})
    def test_http_error_without_response(self, _mock_headers, mock_get):
        """HTTPError with response=None uses '?' as status code."""
        import requests
        mock_get.side_effect = requests.HTTPError(response=None)

        result = fetch_usage()

        self.assertEqual(result, {'error': EN['http_error'].format(code='?')})
        self.assertNotIn('auth_error', result)

    @patch('usage_monitor_for_claude.api.requests.get')
    @patch('usage_monitor_for_claude.api.api_headers', return_value={'Authorization': 'Bearer test'})
    def test_generic_exception(self, _mock_headers, mock_get):
        """Unexpected exception returns connection_error message."""
        mock_get.side_effect = RuntimeError('unexpected')

        result = fetch_usage()

        self.assertEqual(result, {'error': EN['connection_error']})

    @patch('usage_monitor_for_claude.api.requests.get')
    @patch('usage_monitor_for_claude.api.api_headers', return_value={'Authorization': 'Bearer test'})
    def test_only_calls_usage_url(self, _mock_headers, mock_get):
        """Verify the request goes exclusively to API_URL_USAGE."""
        mock_resp = MagicMock()
        mock_resp.json.return_value = {}
        mock_get.return_value = mock_resp

        fetch_usage()

        called_url = mock_get.call_args[0][0]
        self.assertEqual(called_url, 'https://api.anthropic.com/api/oauth/usage')


# ---------------------------------------------------------------------------
# 429 / rate limit handling
# ---------------------------------------------------------------------------

@patch('usage_monitor_for_claude.api.T', EN)
class TestFetchUsageRateLimit(unittest.TestCase):
    """Tests for HTTP 429 rate-limit handling in fetch_usage()."""

    @patch('usage_monitor_for_claude.api.requests.get')
    @patch('usage_monitor_for_claude.api.api_headers', return_value={'Authorization': 'Bearer test'})
    def test_429_returns_rate_limited_flag(self, _mock_headers, mock_get):
        """HTTP 429 sets rate_limited flag."""
        import requests
        mock_resp = MagicMock()
        mock_resp.status_code = 429
        mock_resp.headers = {}
        mock_resp.json.return_value = {}
        mock_resp.raise_for_status.side_effect = requests.HTTPError(response=mock_resp)
        mock_get.return_value = mock_resp

        result = fetch_usage()

        self.assertTrue(result['rate_limited'])
        self.assertEqual(result['error'], EN['http_error'].format(code=429))

    @patch('usage_monitor_for_claude.api.requests.get')
    @patch('usage_monitor_for_claude.api.api_headers', return_value={'Authorization': 'Bearer test'})
    def test_429_with_retry_after(self, _mock_headers, mock_get):
        """HTTP 429 with Retry-After header includes retry_after in result."""
        import requests
        mock_resp = MagicMock()
        mock_resp.status_code = 429
        mock_resp.headers = {'Retry-After': '60'}
        mock_resp.json.return_value = {}
        mock_resp.raise_for_status.side_effect = requests.HTTPError(response=mock_resp)
        mock_get.return_value = mock_resp

        result = fetch_usage()

        self.assertEqual(result['retry_after'], 60)
        self.assertTrue(result['rate_limited'])

    @patch('usage_monitor_for_claude.api.requests.get')
    @patch('usage_monitor_for_claude.api.api_headers', return_value={'Authorization': 'Bearer test'})
    def test_429_with_server_message(self, _mock_headers, mock_get):
        """HTTP 429 with JSON error body includes server_message."""
        import requests
        mock_resp = MagicMock()
        mock_resp.status_code = 429
        mock_resp.headers = {'Retry-After': '0'}
        mock_resp.json.return_value = {'error': {'message': 'Rate limited. Please try again later.'}}
        mock_resp.raise_for_status.side_effect = requests.HTTPError(response=mock_resp)
        mock_get.return_value = mock_resp

        result = fetch_usage()

        self.assertEqual(result['server_message'], 'Rate limited.')

    @patch('usage_monitor_for_claude.api.requests.get')
    @patch('usage_monitor_for_claude.api.api_headers', return_value={'Authorization': 'Bearer test'})
    def test_429_without_retry_after_header(self, _mock_headers, mock_get):
        """HTTP 429 without Retry-After header omits retry_after from result."""
        import requests
        mock_resp = MagicMock()
        mock_resp.status_code = 429
        mock_resp.headers = {}
        mock_resp.json.return_value = {}
        mock_resp.raise_for_status.side_effect = requests.HTTPError(response=mock_resp)
        mock_get.return_value = mock_resp

        result = fetch_usage()

        self.assertNotIn('retry_after', result)

    @patch('usage_monitor_for_claude.api.requests.get')
    @patch('usage_monitor_for_claude.api.api_headers', return_value={'Authorization': 'Bearer test'})
    def test_server_message_on_non_429_error(self, _mock_headers, mock_get):
        """Server message is included for non-429 HTTP errors too."""
        import requests
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_resp.json.return_value = {'error': {'message': 'Internal server error'}}
        mock_resp.raise_for_status.side_effect = requests.HTTPError(response=mock_resp)
        mock_get.return_value = mock_resp

        result = fetch_usage()

        self.assertEqual(result['server_message'], 'Internal server error')


# ---------------------------------------------------------------------------
# fetch_prepaid_credits
# ---------------------------------------------------------------------------

_ORG_UUID = '2b4f9a1c-7d3e-4a58-9c61-0e8fb2d7a940'


def _prepaid_response(**overrides):
    """Build a prepaid-credits response as returned by the OAuth endpoint."""
    data = {
        'amount': 5597,
        'currency': 'EUR',
        'balance': {'money': {'amount_minor': 5597, 'currency': 'EUR', 'exponent': 2}, 'credits': None},
        'balance_credits': None,
        'auto_reload_settings': None,
        'expiry_policy_months': None,
        'tranches': [],
        'promo_tranches': [],
        'next_expires_at': None,
    }
    data.update(overrides)

    return data


def _http_error_response(status_code):
    """Build a mock response whose raise_for_status() raises an HTTPError."""
    import requests
    resp = MagicMock()
    resp.status_code = status_code
    resp.raise_for_status.side_effect = requests.HTTPError(response=resp)

    return resp


class TestFetchPrepaidCredits(unittest.TestCase):
    """Tests for fetch_prepaid_credits()."""

    @patch('usage_monitor_for_claude.api.requests.get')
    @patch('usage_monitor_for_claude.api.api_headers', return_value={'Authorization': 'Bearer test'})
    def test_success_returns_normalized_balance(self, _mock_headers, mock_get):
        """A numeric amount returns the normalized balance dict."""
        mock_resp = MagicMock()
        mock_resp.json.return_value = _prepaid_response()
        mock_get.return_value = mock_resp

        result = fetch_prepaid_credits(_ORG_UUID)

        self.assertEqual(result, {'amount_minor': 5597.0, 'currency': 'EUR', 'decimal_places': 2})

    @patch('usage_monitor_for_claude.api.requests.get')
    @patch('usage_monitor_for_claude.api.api_headers', return_value={'Authorization': 'Bearer test'})
    def test_request_targets_the_org_endpoint(self, _mock_headers, mock_get):
        """The organization uuid is the only variable part of the URL."""
        mock_resp = MagicMock()
        mock_resp.json.return_value = _prepaid_response()
        mock_get.return_value = mock_resp

        fetch_prepaid_credits(_ORG_UUID)

        mock_get.assert_called_once_with(
            f'https://api.anthropic.com/api/oauth/organizations/{_ORG_UUID}/prepaid/credits',
            headers={'Authorization': 'Bearer test'},
            timeout=5,
        )

    @patch('usage_monitor_for_claude.api.requests.get')
    @patch('usage_monitor_for_claude.api.api_headers', return_value={'Authorization': 'Bearer test'})
    def test_empty_tranches_still_returns_balance(self, _mock_headers, mock_get):
        """Empty tranches / promo_tranches do not affect the balance."""
        mock_resp = MagicMock()
        mock_resp.json.return_value = _prepaid_response(tranches=[], promo_tranches=[])
        mock_get.return_value = mock_resp

        self.assertEqual(fetch_prepaid_credits(_ORG_UUID), {'amount_minor': 5597.0, 'currency': 'EUR', 'decimal_places': 2})

    @patch('usage_monitor_for_claude.api.requests.get')
    @patch('usage_monitor_for_claude.api.api_headers', return_value={'Authorization': 'Bearer test'})
    def test_null_tranche_money_objects_ignored(self, _mock_headers, mock_get):
        """The null money objects nested in a promo tranche are never read."""
        tranche = {
            'remaining_amount_minor_units': 5596, 'granted_amount_minor_units': 8500, 'currency': 'EUR',
            'expires_at': '2026-09-19T00:00:00Z', 'granted_at': None, 'remaining': None, 'granted': None, 'program_id': None,
        }
        mock_resp = MagicMock()
        mock_resp.json.return_value = _prepaid_response(promo_tranches=[tranche])
        mock_get.return_value = mock_resp

        self.assertEqual(fetch_prepaid_credits(_ORG_UUID), {'amount_minor': 5597.0, 'currency': 'EUR', 'decimal_places': 2})

    @patch('usage_monitor_for_claude.api.requests.get')
    @patch('usage_monitor_for_claude.api.api_headers', return_value={'Authorization': 'Bearer test'})
    def test_missing_amount_returns_none(self, _mock_headers, mock_get):
        """A response without an amount means the account has no prepaid credits."""
        mock_resp = MagicMock()
        mock_resp.json.return_value = _prepaid_response(amount=None)
        mock_get.return_value = mock_resp

        self.assertIsNone(fetch_prepaid_credits(_ORG_UUID))

    @patch('usage_monitor_for_claude.api.requests.get')
    @patch('usage_monitor_for_claude.api.api_headers', return_value={'Authorization': 'Bearer test'})
    def test_non_numeric_amount_returns_none(self, _mock_headers, mock_get):
        """A non-numeric amount returns None instead of raising."""
        for amount in ('5597', [], {}, True):
            with self.subTest(amount=amount):
                mock_resp = MagicMock()
                mock_resp.json.return_value = _prepaid_response(amount=amount)
                mock_get.return_value = mock_resp

                self.assertIsNone(fetch_prepaid_credits(_ORG_UUID))

    @patch('usage_monitor_for_claude.api.requests.get')
    @patch('usage_monitor_for_claude.api.api_headers', return_value={'Authorization': 'Bearer test'})
    def test_http_errors_return_none(self, _mock_headers, mock_get):
        """HTTP 401, 429 and 500 all return None without raising."""
        for code in (401, 429, 500):
            with self.subTest(code=code):
                mock_get.return_value = _http_error_response(code)

                self.assertIsNone(fetch_prepaid_credits(_ORG_UUID))

    @patch('usage_monitor_for_claude.api.requests.get')
    @patch('usage_monitor_for_claude.api.api_headers', return_value={'Authorization': 'Bearer test'})
    def test_connection_error_returns_none(self, _mock_headers, mock_get):
        """A connection error returns None without raising."""
        import requests
        mock_get.side_effect = requests.ConnectionError()

        self.assertIsNone(fetch_prepaid_credits(_ORG_UUID))

    @patch('usage_monitor_for_claude.api.requests.get')
    @patch('usage_monitor_for_claude.api.api_headers', return_value={'Authorization': 'Bearer test'})
    def test_unexpected_exception_returns_none(self, _mock_headers, mock_get):
        """No exception escapes - the caller stores the result without guarding."""
        mock_get.side_effect = RuntimeError('unexpected')

        self.assertIsNone(fetch_prepaid_credits(_ORG_UUID))

    @patch('usage_monitor_for_claude.api.requests.get')
    @patch('usage_monitor_for_claude.api.api_headers', return_value={'Authorization': 'Bearer test'})
    def test_invalid_json_body_returns_none(self, _mock_headers, mock_get):
        """A body that is not JSON returns None without raising."""
        mock_resp = MagicMock()
        mock_resp.json.side_effect = ValueError('not JSON')
        mock_get.return_value = mock_resp

        self.assertIsNone(fetch_prepaid_credits(_ORG_UUID))

    @patch('usage_monitor_for_claude.api.requests.get')
    @patch('usage_monitor_for_claude.api.api_headers', return_value={'Authorization': 'Bearer test'})
    def test_invalid_org_uuid_makes_no_request(self, _mock_headers, mock_get):
        """A uuid that is empty, malformed or path-like is rejected before any request."""
        # The trailing-newline case fails only against the \A...\Z anchors: a $ anchor
        # matches before a final newline, so without it the pattern could be weakened
        # to ^...$ without a single test noticing.
        for org_uuid in ('', '   ', 'not-a-uuid', f'{_ORG_UUID}/../../admin', f'../{_ORG_UUID}', f'{_ORG_UUID}\n', None, 123):
            with self.subTest(org_uuid=org_uuid):
                self.assertIsNone(fetch_prepaid_credits(org_uuid))

        mock_get.assert_not_called()

    @patch('usage_monitor_for_claude.api.requests.get')
    @patch('usage_monitor_for_claude.api.api_headers', return_value=None)
    def test_no_token_makes_no_request(self, _mock_headers, mock_get):
        """Without a token no request is made and None is returned."""
        self.assertIsNone(fetch_prepaid_credits(_ORG_UUID))
        mock_get.assert_not_called()


class TestNormalizePrepaidCredits(unittest.TestCase):
    """Tests for _normalize_prepaid_credits()."""

    def test_exponent_from_balance_money(self):
        """The decimal places come from the top-level balance.money exponent."""
        data = _prepaid_response(balance={'money': {'amount_minor': 5597, 'currency': 'JPY', 'exponent': 0}, 'credits': None})
        self.assertEqual(_normalize_prepaid_credits(data), {'amount_minor': 5597.0, 'currency': 'JPY', 'decimal_places': 0})

    def test_null_balance_falls_back_to_top_level_currency(self):
        """A null balance still yields the amount, with two decimal places assumed."""
        data = _prepaid_response(balance=None)
        self.assertEqual(_normalize_prepaid_credits(data), {'amount_minor': 5597.0, 'currency': 'EUR', 'decimal_places': 2})

    def test_null_money_falls_back_to_top_level_currency(self):
        """A null balance.money still yields the amount, with two decimal places assumed."""
        data = _prepaid_response(balance={'money': None, 'credits': None})
        self.assertEqual(_normalize_prepaid_credits(data), {'amount_minor': 5597.0, 'currency': 'EUR', 'decimal_places': 2})

    def test_non_dict_balance_or_money_ignored(self):
        """A balance or money object that changed type is ignored rather than raised on."""
        for balance in ('x', [1], 42, {'money': 'x'}, {'money': [1]}):
            with self.subTest(balance=balance):
                data = _prepaid_response(balance=balance)
                self.assertEqual(_normalize_prepaid_credits(data), {'amount_minor': 5597.0, 'currency': 'EUR', 'decimal_places': 2})

    def test_missing_currency_returns_none_currency(self):
        """Without any currency the field is None, so the locale default applies."""
        data = _prepaid_response(currency=None, balance={'money': {'exponent': 2}, 'credits': None})
        self.assertEqual(_normalize_prepaid_credits(data), {'amount_minor': 5597.0, 'currency': None, 'decimal_places': 2})

    def test_non_integer_exponent_uses_default(self):
        """A non-integer exponent falls back to two decimal places."""
        data = _prepaid_response(balance={'money': {'currency': 'EUR', 'exponent': '2'}, 'credits': None})
        self.assertEqual(_normalize_prepaid_credits(data), {'amount_minor': 5597.0, 'currency': 'EUR', 'decimal_places': 2})

    def test_zero_amount(self):
        """A depleted balance of zero is a valid amount, not a missing one."""
        self.assertEqual(_normalize_prepaid_credits(_prepaid_response(amount=0)), {'amount_minor': 0.0, 'currency': 'EUR', 'decimal_places': 2})

    def test_float_amount(self):
        """A float amount is accepted."""
        self.assertEqual(_normalize_prepaid_credits(_prepaid_response(amount=5597.5)), {'amount_minor': 5597.5, 'currency': 'EUR', 'decimal_places': 2})

    def test_non_dict_returns_none(self):
        """A body that is not an object returns None."""
        for data in (None, [], 'text', 42):
            with self.subTest(data=data):
                self.assertIsNone(_normalize_prepaid_credits(data))


# ---------------------------------------------------------------------------
# Certificate verification
# ---------------------------------------------------------------------------

class TestCertificateVerification(unittest.TestCase):
    """Tests for the Windows certificate store integration."""

    def test_windows_certificate_store_is_active(self):
        """Importing the API module routes TLS verification through the Windows certificate store."""
        import ssl
        import truststore

        self.assertIs(ssl.SSLContext, truststore.SSLContext)


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

class TestExtractServerMessage(unittest.TestCase):
    """Tests for _extract_server_message()."""

    def test_none_response(self):
        self.assertIsNone(_extract_server_message(None))

    def test_json_error_message(self):
        resp = MagicMock()
        resp.json.return_value = {'error': {'message': 'Something went wrong.'}}
        self.assertEqual(_extract_server_message(resp), 'Something went wrong.')

    def test_strips_retry_suffix(self):
        """Strips 'Please try again later.' suffix since the app retries automatically."""
        resp = MagicMock()
        resp.json.return_value = {'error': {'message': 'Rate limited. Please try again later.'}}
        self.assertEqual(_extract_server_message(resp), 'Rate limited.')

    def test_empty_message(self):
        resp = MagicMock()
        resp.json.return_value = {'error': {'message': ''}}
        self.assertIsNone(_extract_server_message(resp))

    def test_no_error_key(self):
        resp = MagicMock()
        resp.json.return_value = {'status': 'ok'}
        self.assertIsNone(_extract_server_message(resp))

    def test_html_body(self):
        resp = MagicMock()
        resp.json.side_effect = ValueError('not JSON')
        self.assertIsNone(_extract_server_message(resp))


class TestParseRetryAfter(unittest.TestCase):
    """Tests for _parse_retry_after()."""

    def test_none_response(self):
        self.assertIsNone(_parse_retry_after(None))

    def test_valid_integer(self):
        resp = MagicMock()
        resp.headers = {'Retry-After': '120'}
        self.assertEqual(_parse_retry_after(resp), 120)

    def test_zero_value(self):
        resp = MagicMock()
        resp.headers = {'Retry-After': '0'}
        self.assertEqual(_parse_retry_after(resp), 0)

    def test_negative_clamped_to_zero(self):
        resp = MagicMock()
        resp.headers = {'Retry-After': '-5'}
        self.assertEqual(_parse_retry_after(resp), 0)

    def test_missing_header(self):
        resp = MagicMock()
        resp.headers = {}
        self.assertIsNone(_parse_retry_after(resp))

    def test_non_numeric_value(self):
        resp = MagicMock()
        resp.headers = {'Retry-After': 'Wed, 21 Oct 2026 07:28:00 GMT'}
        self.assertIsNone(_parse_retry_after(resp))


# ---------------------------------------------------------------------------
# _merge_scoped_limits
# ---------------------------------------------------------------------------

_FIVE_HOUR_RESET = '2026-07-02T10:19:59.752884+00:00'
_SEVEN_DAY_RESET = '2026-07-09T02:59:59.752905+00:00'


def _response_with_scoped(display_name, percent, resets_at):
    """Build a usage response carrying one model-scoped weekly limit."""
    return {
        'five_hour': {'utilization': 4.0, 'resets_at': _FIVE_HOUR_RESET},
        'seven_day': {'utilization': 1.0, 'resets_at': _SEVEN_DAY_RESET},
        'limits': [
            {'kind': 'session', 'group': 'session', 'percent': 4, 'resets_at': _FIVE_HOUR_RESET, 'scope': None},
            {'kind': 'weekly_all', 'group': 'weekly', 'percent': 1, 'resets_at': _SEVEN_DAY_RESET, 'scope': None},
            {'kind': 'weekly_scoped', 'group': 'weekly', 'percent': percent, 'resets_at': resets_at,
             'scope': {'model': {'id': None, 'display_name': display_name}, 'surface': None}},
        ],
    }


class TestMergeScopedLimits(unittest.TestCase):
    """Tests for _merge_scoped_limits()."""

    def test_no_limits_key_passthrough(self):
        """A response without a 'limits' array is returned unchanged."""
        data = {'five_hour': {'utilization': 42.0}}
        self.assertEqual(_merge_scoped_limits(data), {'five_hour': {'utilization': 42.0}})

    def test_limits_not_a_list_passthrough(self):
        """A non-list 'limits' value is ignored."""
        data = {'seven_day': {'utilization': 1.0}, 'limits': None}
        self.assertEqual(_merge_scoped_limits(data), data)

    def test_active_scoped_limit_becomes_field(self):
        """An active model-scoped weekly limit becomes a synthetic quota field."""
        result = _merge_scoped_limits(_response_with_scoped('Fable', 30, _SEVEN_DAY_RESET))
        self.assertEqual(result['seven_day_fable'], {'utilization': 30.0, 'resets_at': _SEVEN_DAY_RESET})

    def test_percent_is_float(self):
        """The integer 'percent' is exposed as a float 'utilization'."""
        result = _merge_scoped_limits(_response_with_scoped('Fable', 30, _SEVEN_DAY_RESET))
        self.assertIsInstance(result['seven_day_fable']['utilization'], float)

    def test_inactive_scoped_limit_still_exposed(self):
        """A scoped limit without a reset window is exposed at 0% with resets_at None."""
        result = _merge_scoped_limits(_response_with_scoped('Fable', 0, None))
        self.assertEqual(result['seven_day_fable'], {'utilization': 0.0, 'resets_at': None})

    def test_existing_top_level_field_not_overwritten(self):
        """A top-level field wins over a scoped limit for the same model."""
        data = _response_with_scoped('Sonnet', 50, _SEVEN_DAY_RESET)
        data['seven_day_sonnet'] = {'utilization': 55.0, 'resets_at': _SEVEN_DAY_RESET}
        result = _merge_scoped_limits(data)
        self.assertEqual(result['seven_day_sonnet']['utilization'], 55.0)

    def test_scoped_without_base_group_skipped(self):
        """Without a non-scoped limit of the same group, no prefix can be derived."""
        data = {
            'five_hour': {'utilization': 4.0, 'resets_at': _FIVE_HOUR_RESET},
            'seven_day': {'utilization': 1.0, 'resets_at': _SEVEN_DAY_RESET},
            'limits': [
                {'kind': 'weekly_scoped', 'group': 'weekly', 'percent': 30, 'resets_at': _SEVEN_DAY_RESET,
                 'scope': {'model': {'display_name': 'Fable'}}},
            ],
        }
        self.assertNotIn('seven_day_fable', _merge_scoped_limits(data))

    def test_input_not_mutated(self):
        """The original response dict is not mutated in place."""
        data = _response_with_scoped('Fable', 30, _SEVEN_DAY_RESET)
        _merge_scoped_limits(data)
        self.assertNotIn('seven_day_fable', data)

    def test_original_fields_preserved(self):
        """Existing top-level quota fields survive the merge unchanged."""
        result = _merge_scoped_limits(_response_with_scoped('Fable', 30, _SEVEN_DAY_RESET))
        self.assertEqual(result['five_hour']['utilization'], 4.0)
        self.assertEqual(result['seven_day']['utilization'], 1.0)


# ---------------------------------------------------------------------------
# _model_slug
# ---------------------------------------------------------------------------

class TestModelSlug(unittest.TestCase):
    """Tests for _model_slug()."""

    def test_single_word(self):
        self.assertEqual(_model_slug('Fable'), 'fable')

    def test_multi_word(self):
        self.assertEqual(_model_slug('Claude Sonnet'), 'claude_sonnet')

    def test_digits_and_punctuation(self):
        self.assertEqual(_model_slug('Opus 4.5'), 'opus_4_5')


if __name__ == '__main__':
    unittest.main()

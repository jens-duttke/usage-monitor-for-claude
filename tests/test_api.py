"""
API Client Tests
=================

Unit tests for read_access_token() and fetch_usage().
"""
from __future__ import annotations

import json
import unittest
from tempfile import TemporaryDirectory
from pathlib import Path
from unittest.mock import MagicMock, patch

from usage_monitor_for_claude.api import API_URL_USAGE, _extract_server_message, _parse_retry_after, fetch_usage, read_access_token
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


if __name__ == '__main__':
    unittest.main()

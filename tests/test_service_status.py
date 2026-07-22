"""
Service Status Tests
======================

Unit tests for fetch_service_status().
"""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from usage_monitor_for_claude.service_status import STATUS_URL, fetch_service_status


class TestFetchServiceStatus(unittest.TestCase):
    """Tests for fetch_service_status()."""

    @patch('usage_monitor_for_claude.service_status.requests.get')
    def test_operational_status(self, mock_get):
        """A 'none' indicator with description is returned as-is."""
        mock_resp = MagicMock()
        mock_resp.json.return_value = {'status': {'indicator': 'none', 'description': 'All Systems Operational'}}
        mock_get.return_value = mock_resp

        result = fetch_service_status()

        self.assertEqual(result, {'indicator': 'none', 'description': 'All Systems Operational'})
        mock_get.assert_called_once_with(STATUS_URL, timeout=10)

    @patch('usage_monitor_for_claude.service_status.requests.get')
    def test_minor_incident(self, mock_get):
        """A 'minor' indicator is returned as-is."""
        mock_resp = MagicMock()
        mock_resp.json.return_value = {'status': {'indicator': 'minor', 'description': 'Partial System Outage'}}
        mock_get.return_value = mock_resp

        result = fetch_service_status()

        self.assertEqual(result, {'indicator': 'minor', 'description': 'Partial System Outage'})

    @patch('usage_monitor_for_claude.service_status.requests.get')
    def test_major_incident(self, mock_get):
        """A 'major' indicator is returned as-is."""
        mock_resp = MagicMock()
        mock_resp.json.return_value = {'status': {'indicator': 'major', 'description': 'Major System Outage'}}
        mock_get.return_value = mock_resp

        result = fetch_service_status()

        self.assertEqual(result, {'indicator': 'major', 'description': 'Major System Outage'})

    @patch('usage_monitor_for_claude.service_status.requests.get')
    def test_critical_incident(self, mock_get):
        """A 'critical' indicator is returned as-is."""
        mock_resp = MagicMock()
        mock_resp.json.return_value = {'status': {'indicator': 'critical', 'description': 'Critical System Outage'}}
        mock_get.return_value = mock_resp

        result = fetch_service_status()

        self.assertEqual(result, {'indicator': 'critical', 'description': 'Critical System Outage'})

    @patch('usage_monitor_for_claude.service_status.requests.get')
    def test_missing_description_defaults_to_empty_string(self, mock_get):
        """Missing description key defaults to an empty string, not None."""
        mock_resp = MagicMock()
        mock_resp.json.return_value = {'status': {'indicator': 'none'}}
        mock_get.return_value = mock_resp

        result = fetch_service_status()

        self.assertEqual(result['description'], '')

    @patch('usage_monitor_for_claude.service_status.requests.get')
    def test_unrecognized_indicator_returns_none(self, mock_get):
        """An indicator value outside the known set is treated as unusable."""
        mock_resp = MagicMock()
        mock_resp.json.return_value = {'status': {'indicator': 'unknown_future_value', 'description': 'Something New'}}
        mock_get.return_value = mock_resp

        self.assertIsNone(fetch_service_status())

    @patch('usage_monitor_for_claude.service_status.requests.get')
    def test_missing_status_key_returns_none(self, mock_get):
        """A response body without a 'status' key returns None."""
        mock_resp = MagicMock()
        mock_resp.json.return_value = {}
        mock_get.return_value = mock_resp

        self.assertIsNone(fetch_service_status())

    @patch('usage_monitor_for_claude.service_status.requests.get')
    def test_timeout_returns_none(self, mock_get):
        """A request timeout returns None (caller shows a gray/unknown dot)."""
        import requests
        mock_get.side_effect = requests.Timeout()

        self.assertIsNone(fetch_service_status())

    @patch('usage_monitor_for_claude.service_status.requests.get')
    def test_connection_error_returns_none(self, mock_get):
        """A connection error returns None."""
        import requests
        mock_get.side_effect = requests.ConnectionError()

        self.assertIsNone(fetch_service_status())

    @patch('usage_monitor_for_claude.service_status.requests.get')
    def test_http_error_returns_none(self, mock_get):
        """A non-2xx HTTP status returns None."""
        import requests
        mock_resp = MagicMock()
        mock_resp.raise_for_status.side_effect = requests.HTTPError(response=mock_resp)
        mock_get.return_value = mock_resp

        self.assertIsNone(fetch_service_status())

    @patch('usage_monitor_for_claude.service_status.requests.get')
    def test_malformed_json_returns_none(self, mock_get):
        """A response body that is not valid JSON returns None."""
        mock_resp = MagicMock()
        mock_resp.json.side_effect = ValueError('not JSON')
        mock_get.return_value = mock_resp

        self.assertIsNone(fetch_service_status())

    @patch('usage_monitor_for_claude.service_status.requests.get')
    def test_only_calls_status_url(self, mock_get):
        """Verify the request goes exclusively to STATUS_URL."""
        mock_resp = MagicMock()
        mock_resp.json.return_value = {'status': {'indicator': 'none', 'description': ''}}
        mock_get.return_value = mock_resp

        fetch_service_status()

        called_url = mock_get.call_args[0][0]
        self.assertEqual(called_url, 'https://status.claude.com/api/v2/status.json')


if __name__ == '__main__':
    unittest.main()

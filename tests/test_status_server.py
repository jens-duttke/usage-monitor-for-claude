"""Status endpoint contract, lifecycle, and provider isolation tests."""
from __future__ import annotations

import http.client
import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from usage_monitor_for_claude import app as app_module, settings
from usage_monitor_for_claude.status_server import StatusServer, build_snapshot


def make_cache() -> MagicMock:
    cache = MagicMock()
    cache.snapshot = SimpleNamespace(usage={'five_hour': {'utilization': 42.2, 'resets_at': None}},
                                     last_success_time=None, last_error=None,
                                     profile={'email': 'synthetic@example.invalid'})
    return cache


class TestStatusContract(unittest.TestCase):
    def test_only_sanitized_cached_data_is_exposed(self) -> None:
        cache = make_cache()
        cache.snapshot.last_error = 'sensitive synthetic error'
        data = build_snapshot(cache, 'claude', 'Claude')
        provider = data['providers']['claude']
        self.assertEqual(data['schema'], 1)
        self.assertEqual(provider['windows'], {'five_hour': {'utilization': 42, 'resets_at': ''}})
        self.assertIsNone(provider['stale'])
        self.assertEqual(provider['error'], 'Usage data unavailable.')
        self.assertNotIn('synthetic', json.dumps(data))
        cache.update.assert_not_called()
        cache.ensure_profile.assert_not_called()

    def test_empty_malformed_and_stale_state(self) -> None:
        cache = make_cache()
        for usage in ({}, None, {'null': None, 'raw': [], 'window': {'utilization': float('nan')}}):
            cache.snapshot.usage = usage
            data = build_snapshot(cache, 'claude', 'Claude')
            json.dumps(data, allow_nan=False)
        cache.snapshot.last_success_time = 100
        with patch('usage_monitor_for_claude.status_server.time.time', return_value=100 + 3 * settings.POLL_INTERVAL):
            self.assertFalse(build_snapshot(cache, 'claude', 'Claude')['providers']['claude']['stale'])
        with patch('usage_monitor_for_claude.status_server.time.time', return_value=101 + 3 * settings.POLL_INTERVAL):
            self.assertTrue(build_snapshot(cache, 'claude', 'Claude')['providers']['claude']['stale'])

    def test_loopback_paths_methods_and_shutdown(self) -> None:
        cache = make_cache()
        server = StatusServer(cache, 'claude', 'Claude', 0)
        server.start()
        self.addCleanup(server.shutdown)
        assert server._server is not None
        self.assertEqual(server._server.server_address[0], '127.0.0.1')
        port = server._server.server_port
        for method, path, expected in [('GET', '/', 200), ('GET', '/usage', 200), ('GET', '/unknown', 404),
                                       ('POST', '/usage', 405), ('HEAD', '/usage', 405), ('OPTIONS', '/', 405),
                                       ('PUT', '/', 405), ('PATCH', '/', 405), ('DELETE', '/', 405), ('TRACE', '/', 405),
                                       ('CONNECT', '/', 405), ('UNKNOWN', '/', 405)]:
            with self.subTest(method=method, path=path):
                connection = http.client.HTTPConnection('127.0.0.1', port, timeout=2)
                try:
                    connection.request(method, path)
                    response = connection.getresponse()
                    self.assertEqual(response.status, expected)
                    self.assertIsNone(response.getheader('Access-Control-Allow-Origin'))
                    if expected == 405:
                        self.assertEqual(response.getheader('Allow'), 'GET')
                    response.read()
                finally:
                    connection.close()
        cache.update.assert_not_called()
        conflict = StatusServer(cache, 'claude', 'Claude', port)
        with self.assertRaises(OSError):
            conflict.start()
        server.shutdown()
        server.shutdown()
        replacement = StatusServer(cache, 'claude', 'Claude', port)
        replacement.start()
        replacement.shutdown()

    def test_settings_defaults_and_validation(self) -> None:
        self.assertFalse(settings.STATUS_SERVER_ENABLED)
        self.assertEqual(settings.STATUS_SERVER_PORT, 45456)
        with patch.object(settings, 'show_warning_box'):
            for value in (0, 1023, 65536, True, '45455'):
                self.assertNotIn('status_server_port', settings._validate({'status_server_port': value}, Path('synthetic.json')))
            for value in (1024, 65535):
                self.assertEqual(settings._validate({'status_server_port': value}, Path('synthetic.json'))['status_server_port'], value)
            self.assertNotIn('status_server_enabled', settings._validate({'status_server_enabled': 'true'}, Path('synthetic.json')))


class TestStatusLifecycle(unittest.TestCase):
    def make_app(self):
        app = object.__new__(app_module.UsageMonitorForClaude)
        app.cache, app.icon = make_cache(), MagicMock()
        return app

    def test_disabled_by_default(self) -> None:
        app = self.make_app()
        with patch.object(app_module, 'StatusServer') as server, patch.object(app_module, 'STATUS_SERVER_ENABLED', False):
            app.run()
        server.assert_not_called()
        app.icon.run.assert_called_once()

    def test_listener_is_closed_on_normal_exit_and_failure(self) -> None:
        for failure in (None, RuntimeError('synthetic tray failure')):
            app = self.make_app()
            app.icon.run.side_effect = failure
            with patch.object(app_module, 'StatusServer') as server, patch.object(app_module, 'STATUS_SERVER_ENABLED', True):
                if failure:
                    with self.assertRaises(RuntimeError):
                        app.run()
                else:
                    app.run()
                server.return_value.start.assert_called_once()
                server.return_value.shutdown.assert_called_once()

    def test_port_conflict_does_not_stop_tray(self) -> None:
        app = self.make_app()
        with patch.object(app_module, 'StatusServer') as server, patch.object(app_module, 'STATUS_SERVER_ENABLED', True):
            server.return_value.start.side_effect = OSError('synthetic bind failure')
            app.run()
        app.icon.run.assert_called_once()

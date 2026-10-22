"""Optional loopback-only access to sanitized, cached usage snapshots."""
from __future__ import annotations

import json
import logging
import math
import socket
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, cast

from .settings import POLL_INTERVAL

__all__ = ['StatusServer', 'build_snapshot']

log = logging.getLogger(__name__)


def build_snapshot(cache: Any, provider_id: str, display_name: str) -> dict[str, Any]:
    """Project one immutable cached snapshot without invoking the provider.

    Parameters
    ----------
    cache : UsageCache
        The existing application's cache; only its snapshot property is read.
    provider_id : str
        The standalone provider identifier.
    display_name : str
        Human-readable provider name.

    Returns
    -------
    dict
        Versioned public status, excluding profiles and raw error details.
    """
    snapshot = cache.snapshot
    now = time.time()
    last = snapshot.last_success_time
    stale = None if last is None else now - last > 3 * POLL_INTERVAL
    windows = {}
    usage = snapshot.usage if isinstance(snapshot.usage, dict) else {}
    for key, entry in usage.items():
        if not isinstance(entry, dict) or 'utilization' not in entry:
            continue
        value = entry['utilization']
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            value = 0
        reset = entry.get('resets_at')
        windows[key] = {'utilization': round(value), 'resets_at': reset if isinstance(reset, str) else ''}
    return {
        'schema': 1,
        'generated_at': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
        'providers': {provider_id: {
            'display_name': display_name, 'stale': stale,
            'error': 'Usage data unavailable.' if snapshot.last_error else None,
            'windows': windows,
        }},
    }


class _StatusHTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def server_bind(self) -> None:
        """Prevent duplicate Windows listeners while allowing normal Unix restarts."""
        exclusive = getattr(socket, 'SO_EXCLUSIVEADDRUSE', None)
        if exclusive is not None:
            self.allow_reuse_address = False
            self.socket.setsockopt(socket.SOL_SOCKET, exclusive, 1)
        super().server_bind()

    cache: Any
    provider_id: str
    display_name: str


class _StatusHandler(BaseHTTPRequestHandler):
    server_version = 'UsageMonitorStatus'
    sys_version = ''

    def _reply(self, status: int, payload: dict[str, Any], *, send_body: bool = True) -> None:
        body = json.dumps(payload, allow_nan=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        if send_body:
            self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 - standard-library interface
        """Serve the two documented paths from cached state only."""
        try:
            if self.path not in ('/', '/usage'):
                self._reply(404, {'error': 'not found'})
                return
            server = cast(_StatusHTTPServer, self.server)
            self._reply(200, build_snapshot(server.cache, server.provider_id, server.display_name))
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception:
            log.warning('status response failed')

    def send_error(self, code: int, message: str | None = None, explain: str | None = None) -> None:
        """Treat unknown request methods consistently with unsupported standard methods."""
        if code == 501:
            self._method_not_allowed()
        else:
            super().send_error(code, message, explain)

    def _method_not_allowed(self) -> None:
        self.send_response(405)
        self.send_header('Allow', 'GET')
        self.send_header('Content-Length', '0')
        self.end_headers()

    do_POST = _method_not_allowed
    do_PUT = _method_not_allowed
    do_PATCH = _method_not_allowed
    do_DELETE = _method_not_allowed
    do_HEAD = _method_not_allowed
    do_OPTIONS = _method_not_allowed
    do_TRACE = _method_not_allowed
    do_CONNECT = _method_not_allowed

    def log_message(self, format: str, *args: Any) -> None:
        """Do not log request targets or user-controlled input."""


class StatusServer:
    """Own an optional HTTP listener whose lifetime follows the tray app."""

    def __init__(self, cache: Any, provider_id: str, display_name: str, port: int) -> None:
        self._cache = cache
        self._provider_id = provider_id
        self._display_name = display_name
        self._port = port
        self._server: _StatusHTTPServer | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        """Bind loopback and serve; raise OSError when binding fails."""
        server = _StatusHTTPServer(('127.0.0.1', self._port), _StatusHandler)
        server.cache, server.provider_id, server.display_name = self._cache, self._provider_id, self._display_name
        self._server = server
        self._thread = threading.Thread(target=server.serve_forever, daemon=True, name='usage-status')
        try:
            self._thread.start()
        except Exception:
            server.server_close()
            self._server = self._thread = None
            raise

    def shutdown(self) -> None:
        """Stop serving and release the socket; safe to call more than once."""
        server, self._server = self._server, None
        thread, self._thread = self._thread, None
        if server is not None:
            try:
                server.shutdown()
            finally:
                server.server_close()
        if thread is not None:
            thread.join(timeout=2)

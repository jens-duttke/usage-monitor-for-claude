"""
API Client
===========

Reads Claude Code OAuth credentials and communicates with the
Anthropic API.  This is the only module that handles credentials.

Network communication exclusively with ``api.anthropic.com``.
Credentials used only in HTTP Authorization headers.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

import requests

from .i18n import T

__all__ = ['API_URL_USAGE', 'API_URL_PROFILE', 'CLAUDE_CONFIG_DIR', 'CLAUDE_CREDENTIALS', 'read_access_token', 'api_headers', 'fetch_usage', 'fetch_profile']

# API endpoints & credentials
API_URL_USAGE = 'https://api.anthropic.com/api/oauth/usage'
API_URL_PROFILE = 'https://api.anthropic.com/api/oauth/profile'
CLAUDE_CONFIG_DIR = Path(os.environ.get('CLAUDE_CONFIG_DIR', '')) if os.environ.get('CLAUDE_CONFIG_DIR') else Path.home() / '.claude'
CLAUDE_CREDENTIALS = CLAUDE_CONFIG_DIR / '.credentials.json'
_FALLBACK_USER_AGENT = 'claude-code/2.1.85'

# Network resilience: a few retries for transient failures (DNS hiccups,
# dropped connections, slow responses, laptop sleep/wake) so a single blip
# does not flip the tray into the error state.  Only connection/timeout
# errors are retried here; HTTP status errors (401/429/5xx) are handled by
# the caller.
_TIMEOUT = 10
_MAX_RETRIES = 2
_RETRY_BACKOFF = 1.0


def _get_with_retry(url: str, headers: dict[str, str]) -> requests.Response:
    """GET *url*, retrying transient connection/timeout errors.

    Retries only ``ConnectionError`` / ``Timeout``; ``HTTPError`` propagates
    immediately so the caller can map status codes (401/429/5xx) to states.
    """
    last_exc: Exception | None = None
    for attempt in range(_MAX_RETRIES + 1):
        try:
            resp = requests.get(url, headers=headers, timeout=_TIMEOUT)
            resp.raise_for_status()
            return resp
        except (requests.ConnectionError, requests.Timeout) as exc:
            last_exc = exc
            if attempt < _MAX_RETRIES:
                time.sleep(_RETRY_BACKOFF * (attempt + 1))
                continue
            raise
    raise last_exc if last_exc is not None else requests.ConnectionError(url)


def read_access_token() -> str | None:
    """Read the current access token from the Claude credentials file."""
    if not CLAUDE_CREDENTIALS.exists():
        return None

    try:
        creds = json.loads(CLAUDE_CREDENTIALS.read_text())
        return creds.get('claudeAiOauth', {}).get('accessToken') or None
    except (json.JSONDecodeError, KeyError):
        return None


def api_headers() -> dict[str, str] | None:
    """Return auth headers for the Anthropic OAuth API, or None."""
    token = read_access_token()
    if not token:
        return None

    return {
        'Authorization': f'Bearer {token}',
        'Content-Type': 'application/json',
        'User-Agent': _user_agent(),
        'anthropic-beta': 'oauth-2025-04-20',
    }


def fetch_usage() -> dict[str, Any]:
    """Fetch usage data from the Anthropic OAuth usage API."""
    headers = api_headers()
    if not headers:
        return {'error': T['no_token']}

    try:
        resp = _get_with_retry(API_URL_USAGE, headers)
        return resp.json()
    except (requests.ConnectionError, requests.Timeout):
        return {'error': T['connection_error']}
    except requests.HTTPError as e:
        code = e.response.status_code if e.response is not None else 0
        server_msg = _extract_server_message(e.response)
        extra: dict[str, Any] = {}
        if server_msg:
            extra['server_message'] = server_msg

        if code == 401:
            return {**extra, 'error': T['auth_expired'], 'auth_error': True}
        if code == 429:
            retry = _parse_retry_after(e.response)
            if retry is not None:
                extra['retry_after'] = retry
            return {**extra, 'error': T['http_error'].format(code=429), 'rate_limited': True}
        if 500 <= code < 600:
            return {**extra, 'error': T['server_error'].format(code=code)}
        return {**extra, 'error': T['http_error'].format(code=code or '?')}
    except Exception:
        return {'error': T['connection_error']}


def fetch_profile() -> dict[str, Any] | None:
    """Fetch account profile from the Anthropic OAuth profile API."""
    headers = api_headers()
    if not headers:
        return None

    try:
        resp = _get_with_retry(API_URL_PROFILE, headers)
        return resp.json()
    except Exception:
        return None


# Helpers


def _user_agent() -> str:
    """Return the User-Agent string with the installed Claude Code version."""
    from .claude_cli import CLAUDE_CLI_PATH, cli_version

    version = cli_version(CLAUDE_CLI_PATH)
    return f'claude-code/{version}' if version else _FALLBACK_USER_AGENT


def _extract_server_message(response: requests.Response | None) -> str | None:
    """Extract ``error.message`` from a JSON error response body.

    Strips the trailing "Please try again later." suffix that the API
    appends to some error messages - the app retries automatically, so
    the advice would be misleading.
    """
    if response is None:
        return None
    try:
        msg = response.json().get('error', {}).get('message') or None
        if msg:
            msg = msg.removesuffix(' Please try again later.').removesuffix(' Please try again later').strip()
        return msg or None
    except Exception:
        return None


def _parse_retry_after(response: requests.Response | None) -> int | None:
    """Parse the ``Retry-After`` header as an integer number of seconds."""
    if response is None:
        return None
    raw = response.headers.get('Retry-After')
    if raw is None:
        return None
    try:
        return max(int(raw), 0)
    except (ValueError, TypeError):
        return None

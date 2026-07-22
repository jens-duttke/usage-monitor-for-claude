"""
Service Status
================

Fetches the public Claude system-status indicator from status.claude.com.
Unauthenticated and read-only - no credentials are involved.
"""
from __future__ import annotations

from typing import Any

import requests

__all__ = ['STATUS_PAGE_URL', 'STATUS_URL', 'fetch_service_status']

STATUS_URL = 'https://status.claude.com/api/v2/status.json'
STATUS_PAGE_URL = 'https://status.claude.com/'
_VALID_INDICATORS = frozenset({'none', 'minor', 'major', 'critical'})


def fetch_service_status() -> dict[str, Any] | None:
    """Fetch the current Claude system-status indicator and description.

    Returns
    -------
    dict or None
        Dict with ``indicator`` (``'none'``, ``'minor'``, ``'major'`` or
        ``'critical'``) and ``description`` keys, or ``None`` if the
        request failed, timed out, or returned an unrecognized indicator.
    """
    try:
        resp = requests.get(STATUS_URL, timeout=10)
        resp.raise_for_status()
        status = resp.json().get('status') or {}
    except Exception:
        return None

    indicator = status.get('indicator')
    if indicator not in _VALID_INDICATORS:
        return None

    return {'indicator': indicator, 'description': status.get('description') or ''}

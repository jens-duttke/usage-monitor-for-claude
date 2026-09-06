"""
Windows Popup Host
===================

Kept out of :mod:`usage_monitor_for_claude.platforms` because the host
depends on pywebview.
"""
from __future__ import annotations

from .popup_win32 import WINDOW_KWARGS, PopupHost, popup_url

__all__ = ['WINDOW_KWARGS', 'PopupHost', 'popup_url']

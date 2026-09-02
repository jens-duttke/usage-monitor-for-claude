"""
Popup Host Dispatch
====================

Selects the popup window host for the running system.

Kept out of :mod:`usage_monitor_for_claude.platforms` for the same reason as
the single-instance guard: the hosts reach into pywebview, which the platform
package itself must stay free of.
"""
from __future__ import annotations

import sys

if sys.platform == 'win32':
    from .popup_win32 import WINDOW_KWARGS, PopupHost, popup_url
else:
    from .popup_linux import WINDOW_KWARGS, PopupHost, popup_url

__all__ = ['WINDOW_KWARGS', 'PopupHost', 'popup_url']

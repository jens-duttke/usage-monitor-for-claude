"""
Single-Instance Dispatch
=========================

Selects the single-instance guard for the running system.

Kept out of :mod:`usage_monitor_for_claude.platforms` on purpose: the guard
needs the translations, and ``i18n`` imports ``settings``, which imports the
platform package.  Importing the guard from that package's ``__init__``
would close that cycle.
"""
from __future__ import annotations

import sys

if sys.platform == 'win32':
    from .instance_win32 import ensure_single_instance, release_instance_lock
else:
    from .instance_linux import ensure_single_instance, release_instance_lock

__all__ = ['ensure_single_instance', 'release_instance_lock']

"""
Local Channel Dispatch
=======================

Selects the local channel for the running system: a named pipe on Windows,
a Unix domain socket on Linux.  Both give the same interface - a listener
whose ``accept()`` returns a connection with ``recv``, ``sendall`` and
``close`` - and both are reachable only by the current user.

Kept out of :mod:`usage_monitor_for_claude.platforms` because it is the only
IPC endpoint the application opens: in files of its own it can be audited on
its own, and only the MCP server and the ``--mcp`` bridge import it.
"""
from __future__ import annotations

import sys

if sys.platform == 'win32':
    from .channel_win32 import LocalChannelListener, open_local_channel
else:
    from .channel_linux import LocalChannelListener, open_local_channel

__all__ = ['LocalChannelListener', 'open_local_channel']

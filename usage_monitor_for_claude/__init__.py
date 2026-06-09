"""
Usage Monitor for Claude
=========================

Displays the current Claude.ai usage as a system tray icon.
Left-click the icon to see a detailed usage popup.

Authenticates via Claude Code OAuth token from the Claude config
directory (requires Claude Code login).  Respects ``CLAUDE_CONFIG_DIR``
if set, otherwise defaults to ``~/.claude/``.
"""
from __future__ import annotations

__version__ = '1.16.0'

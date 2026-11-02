"""
Application Command Line
=========================

The command that starts this application again, in the form it is running
now: the EXE of a frozen build, or the interpreter with ``-m`` from source.
"""
from __future__ import annotations

import os
import sys

__all__ = ['app_command']


def app_command(arguments: list[str]) -> tuple[list[str], dict[str, str]]:
    """Return the command line and environment that start the application with ``arguments``.

    A frozen build gets an environment without PyInstaller's internal
    variables, so the new process extracts to a fresh temp directory instead
    of reusing the current one, which is deleted when this process exits.

    Parameters
    ----------
    arguments : list[str]
        Command-line arguments for the new process.

    Returns
    -------
    tuple[list[str], dict[str, str]]
        The program with its arguments, and the environment to start it with.
    """
    if getattr(sys, 'frozen', False):
        env = {key: value for key, value in os.environ.items() if not key.startswith(('_PYI_', '_MEI'))}
        return [sys.executable, *arguments], env

    return [sys.executable, '-m', 'usage_monitor_for_claude', *arguments], dict(os.environ)

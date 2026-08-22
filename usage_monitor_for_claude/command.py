"""
Command
========

Execute user-configured shell commands on usage events.

Commands run as fire-and-forget subprocesses.  Event details are passed
via environment variables so the user's script can inspect them without
any string interpolation in the command itself.
"""
from __future__ import annotations

import ctypes
import os
import subprocess
import sys
import threading
import time
import traceback
from pathlib import Path

from . import __version__

__all__ = ['run_event_command']

# Seconds after launch within which a non-zero exit still counts as a startup
# failure (wrong path, bad arguments) worth an error dialog.  A command that
# launches an app runs for as long as the user keeps that app open; when it
# eventually exits non-zero - it crashed, was killed, or was replaced by a
# second instance of itself - that is not a broken configuration, and a dialog
# raised minutes or hours after the click has no visible connection to it.
_STARTUP_FAILURE_WINDOW = 5.0


def run_event_command(
    commands: list[str], env_vars: dict[str, str], capture_output: bool = False, report_late_failures: bool = True,
) -> None:
    """Launch shell commands with event-specific environment variables.

    Each command runs asynchronously (fire-and-forget).  Exceptions from
    ``subprocess.Popen`` are caught per command so one failure does not
    prevent the remaining commands from running.

    Parameters
    ----------
    commands : list[str]
        Shell command strings to execute.
    env_vars : dict[str, str]
        Mapping of ``USAGE_MONITOR_*`` environment variable names to
        their values.  Merged into the current process environment.
    capture_output : bool
        When True, capture each command's stdout, stderr, and exit code and
        print them once it finishes, and raise an error message box with
        stderr if the command exits with a non-zero code.  Used for
        user-driven actions (the "Test event commands" menu and the
        double-click command) so a failing command is not swallowed silently.
        The wait happens on a background thread, so the call stays
        non-blocking even for a command that keeps running (e.g. a launched
        app).
    report_late_failures : bool
        Only meaningful with *capture_output*.  When False, the error message
        box is limited to commands that fail within ``_STARTUP_FAILURE_WINDOW``
        seconds of launching; a later non-zero exit is printed but not shown.
        Used for the double-click command, which typically launches an app the
        user keeps open.  The "Test event commands" menu leaves it True - there
        the exit code is the point of running the command.
    """
    if not commands:
        return

    env = {**os.environ, 'USAGE_MONITOR_VERSION': __version__, **env_vars}

    # Pin working directory to the executable's folder so that relative paths
    # in commands resolve predictably - even when Windows autostart sets the
    # CWD to C:\Windows\System32.
    if getattr(sys, 'frozen', False):
        working_dir = Path(sys.executable).parent
    else:
        working_dir = Path(__file__).resolve().parent.parent

    for command in commands:
        try:
            if capture_output:
                _launch_and_report(command, env, working_dir, report_late_failures)
            else:
                subprocess.Popen(
                    command, shell=True, env=env, cwd=working_dir,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
        except Exception:
            traceback.print_exc()


def _launch_and_report(command: str, env: dict[str, str], working_dir: Path, report_late_failures: bool) -> None:
    """Launch *command* and print its stdout, stderr, and exit code once it exits.

    The process is waited on in a background daemon thread so the caller is
    never blocked, even by a command that keeps running (e.g. a launched app).
    A non-zero exit code additionally raises an error message box with stderr;
    with *report_late_failures* False only a failure within
    ``_STARTUP_FAILURE_WINDOW`` seconds of the launch does.
    """
    process = subprocess.Popen(
        command, shell=True, env=env, cwd=working_dir,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, errors='replace',
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    started_at = time.monotonic()

    def report() -> None:
        try:
            stdout, stderr = process.communicate()
        except Exception:
            traceback.print_exc()
            return

        runtime = time.monotonic() - started_at

        print(f'[event command] {command}')
        print(f'  exit code: {process.returncode}')
        print(f'  stdout:\n{stdout.rstrip() if stdout.strip() else "    (empty)"}')
        print(f'  stderr:\n{stderr.rstrip() if stderr.strip() else "    (empty)"}')

        if process.returncode == 0:
            return

        if report_late_failures or runtime <= _STARTUP_FAILURE_WINDOW:
            _show_error_box(command, process.returncode, stderr)

    threading.Thread(target=report, daemon=True).start()


def _show_error_box(command: str, returncode: int, stderr: str) -> None:
    """Show an error message box reporting a failed command and its stderr."""
    detail = stderr.strip() or '(no error output on stderr)'
    message = f'The event command exited with code {returncode}:\n\n{command}\n\n{detail}'
    ctypes.windll.user32.MessageBoxW(0, message[:2000], 'Usage Monitor for Claude - Event Command Failed', 0x10)

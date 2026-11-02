"""
Application Command Line Tests
===============================

Unit tests for the command that starts the application again, and a real
run of the detached start the MCP bridge uses.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from usage_monitor_for_claude.launch import app_command

_REPO_ROOT = Path(__file__).resolve().parent.parent


class TestAppCommand(unittest.TestCase):
    """Tests for app_command()."""

    def test_source_checkout_runs_the_package(self):
        with patch.object(sys, 'frozen', False, create=True), patch.object(sys, 'executable', 'C:\\venv\\pythonw.exe'):
            command, _ = app_command(['--exit-if-running'])

        self.assertEqual(command, ['C:\\venv\\pythonw.exe', '-m', 'usage_monitor_for_claude', '--exit-if-running'])

    def test_source_checkout_keeps_the_whole_environment(self):
        """PYTHONPATH is what lets ``-m`` find the checkout when the client started the bridge elsewhere."""
        with patch.object(sys, 'frozen', False, create=True), patch.dict(os.environ, {'PYTHONPATH': 'C:\\checkout', '_MEIPASS2': 'x'}):
            _, env = app_command([])
            expected = dict(os.environ)

        self.assertEqual(env, expected)
        self.assertEqual(env['PYTHONPATH'], 'C:\\checkout')

    def test_frozen_build_runs_the_exe(self):
        with patch.object(sys, 'frozen', True, create=True), patch.object(sys, 'executable', 'C:\\Tools\\UsageMonitorForClaude.exe'):
            command, _ = app_command(['--config-dir=C:\\accounts\\work'])

        self.assertEqual(command, ['C:\\Tools\\UsageMonitorForClaude.exe', '--config-dir=C:\\accounts\\work'])

    def test_frozen_build_drops_pyinstallers_variables(self):
        """A new process that found them would reuse the temp directory this process deletes on exit."""
        variables = {'_PYI_APPLICATION_HOME_DIR': 'C:\\Temp\\_MEI1', '_MEIPASS2': 'C:\\Temp\\_MEI1', 'CLAUDE_CONFIG_DIR': 'C:\\accounts\\work'}
        with patch.object(sys, 'frozen', True, create=True), patch.dict(os.environ, variables):
            _, env = app_command([])

        self.assertNotIn('_PYI_APPLICATION_HOME_DIR', env)
        self.assertNotIn('_MEIPASS2', env)
        self.assertEqual(env['CLAUDE_CONFIG_DIR'], 'C:\\accounts\\work')

    def test_arguments_are_not_shared_between_calls(self):
        arguments = ['--exit-if-running']
        command, _ = app_command(arguments)
        command.append('--verbose')

        self.assertEqual(arguments, ['--exit-if-running'])


class TestStartDetachedForReal(unittest.TestCase):
    """The started process must not hold the caller's standard streams open."""

    def test_caller_exits_while_the_started_process_keeps_running(self):
        """An MCP client reads the bridge's stdout until it closes; a process that inherited it would keep it open for its lifetime."""
        probe = (
            'import os, sys; from usage_monitor_for_claude.platforms import start_detached; '
            'start_detached([sys.executable, "-c", "import time; time.sleep(8)"], dict(os.environ)); '
            'print("started", flush=True)'
        )

        started = time.monotonic()
        completed = subprocess.run(
            [sys.executable, '-c', probe], capture_output=True, cwd=_REPO_ROOT, env={**os.environ, 'PYTHONPATH': str(_REPO_ROOT)},
            timeout=30, check=False,
        )
        elapsed = time.monotonic() - started

        self.assertEqual(completed.stdout.strip(), b'started', completed.stderr.decode(errors='replace'))
        self.assertLess(elapsed, 6.0, 'reading the caller\'s output waited for the started process')


if __name__ == '__main__':
    unittest.main()

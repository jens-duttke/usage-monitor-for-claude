"""
Linux Launcher Tests
====================

Tests for the ``usage-monitor-for-claude`` shell launcher in the project root.
It is exercised through a stub interpreter in a temporary checkout, so the
assertions cover what the script itself decides - which interpreter it picks,
what it puts on ``PYTHONPATH``, and which working directory it leaves behind -
without starting the application.
"""
from __future__ import annotations

import os
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

if sys.platform == 'win32':
    raise unittest.SkipTest('The shell launcher is used on Linux only')

LAUNCHER = Path(__file__).resolve().parent.parent / 'usage-monitor-for-claude'

# Reports what the launcher handed the interpreter, in place of starting the app.
STUB_INTERPRETER = (
    '#!/bin/sh\n'
    'echo "cwd=$PWD"\n'
    'echo "pythonpath=$PYTHONPATH"\n'
    'echo "args=$*"\n'
)


def _run(script: Path, *arguments: str, cwd: Path, environment: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        [str(script), *arguments],
        capture_output=True, text=True, timeout=60,
        cwd=str(cwd), env={**os.environ, **(environment or {})},
    )


class TestLauncherFile(unittest.TestCase):

    def test_is_executable(self):
        """A launcher without the executable bit could not be started or symlinked."""
        self.assertTrue(os.access(LAUNCHER, os.X_OK))


class TestLauncherInvocation(unittest.TestCase):
    """Tests for the command the launcher builds, run against a stub interpreter."""

    def setUp(self):
        self._directory = TemporaryDirectory()
        self.addCleanup(self._directory.cleanup)

        self.checkout = Path(self._directory.name) / 'checkout'
        interpreter = self.checkout / '.venv' / 'bin' / 'python'
        interpreter.parent.mkdir(parents=True)
        interpreter.write_text(STUB_INTERPRETER, encoding='utf-8')
        interpreter.chmod(0o755)

        self.launcher = self.checkout / LAUNCHER.name
        self.launcher.write_text(LAUNCHER.read_text(encoding='utf-8'), encoding='utf-8')
        self.launcher.chmod(0o755)

        self.elsewhere = Path(self._directory.name) / 'elsewhere'
        self.elsewhere.mkdir()

    def _symlink_outside_the_checkout(self) -> Path:
        """Create the link the README suggests putting in ~/.local/bin."""
        link = Path(self._directory.name) / 'bin' / LAUNCHER.name
        link.parent.mkdir()
        link.symlink_to(self.launcher)

        return link

    def test_runs_the_package_with_the_checkout_interpreter(self):
        """The virtual environment's interpreter runs the package as a module."""
        result = _run(self.launcher, cwd=self.elsewhere)

        self.assertEqual(result.returncode, 0)
        self.assertIn('args=-m usage_monitor_for_claude', result.stdout)

    def test_puts_the_checkout_on_pythonpath(self):
        """PYTHONPATH is what makes the package importable, so no directory change is needed."""
        result = _run(self.launcher, cwd=self.elsewhere)

        self.assertIn(f'pythonpath={self.checkout}', result.stdout)
        self.assertIn(f'cwd={self.elsewhere}', result.stdout)

    def test_keeps_an_existing_pythonpath(self):
        """An inherited PYTHONPATH stays intact, with the checkout searched first."""
        result = _run(self.launcher, cwd=self.elsewhere, environment={'PYTHONPATH': '/opt/example'})

        self.assertIn(f'pythonpath={self.checkout}:/opt/example', result.stdout)

    def test_passes_arguments_through(self):
        """Command-line arguments such as --config-dir must reach the application."""
        result = _run(self.launcher, '--config-dir=/tmp/example', cwd=self.elsewhere)

        self.assertIn('args=-m usage_monitor_for_claude --config-dir=/tmp/example', result.stdout)

    def test_resolves_a_symlink_to_its_own_checkout(self):
        """Started through a symlink, the launcher still finds the checkout it belongs to."""
        result = _run(self._symlink_outside_the_checkout(), cwd=self.elsewhere)

        self.assertIn(f'pythonpath={self.checkout}', result.stdout)

    def test_resolves_a_name_found_on_path(self):
        """The documented setup - a symlink in ~/.local/bin - is started by name, not by path."""
        link = self._symlink_outside_the_checkout()

        search_path = os.pathsep.join([str(link.parent), os.environ.get('PATH', '')])
        result = subprocess.run(
            ['/bin/sh', '-c', LAUNCHER.name],
            capture_output=True, text=True, timeout=60,
            cwd=str(self.elsewhere), env={**os.environ, 'PATH': search_path},
        )

        self.assertEqual(result.returncode, 0)
        self.assertIn(f'pythonpath={self.checkout}', result.stdout)

    def test_reports_a_missing_virtual_environment(self):
        """Without a virtual environment the launcher explains where to look instead of failing obscurely."""
        (self.checkout / '.venv' / 'bin' / 'python').unlink()

        result = _run(self.launcher, cwd=self.elsewhere)

        self.assertEqual(result.returncode, 1)
        self.assertIn('README.md', result.stderr)
        self.assertEqual(result.stdout, '')

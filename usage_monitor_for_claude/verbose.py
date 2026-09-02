"""
Verbose Diagnostics
====================

Prints system, environment and runtime diagnostics for ``--verbose``.

The report skeleton lives here; every probe that differs per operating
system comes from :mod:`usage_monitor_for_claude.platforms` as ready-made
label/value rows.
"""
from __future__ import annotations

import importlib.metadata
import locale
import os
import sys
from pathlib import Path

from .platforms import (
    DIAGNOSTIC_PACKAGES, diagnostic_display_rows, diagnostic_post_init_rows,
    diagnostic_runtime_rows, diagnostic_system_rows, setup_console,
)

__all__ = ['setup_console', 'print_startup_diagnostics', 'print_runtime_diagnostics']


def _section(title: str) -> None:
    """Print a section header."""
    print(f'\n  {title}')
    print(f'  {"-" * len(title)}')


def _row(label: str, value: str, indent: int = 4) -> None:
    """Print a key-value row with aligned columns."""
    print(f'{" " * indent}{label + ":":<22s} {value}')


def _package_version(name: str) -> str:
    """Get installed package version, or 'not found'."""
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return 'not found'


def _redact_home(path_str: str) -> str:
    """Replace the user's home directory with ``~`` to avoid exposing the username.

    Case-insensitive (Windows paths compare that way, and e.g. a
    ``CLAUDE_CONFIG_DIR`` set externally may be differently cased) and
    boundary-aware, so a sibling profile whose name merely starts with the
    username is not partially redacted.
    """
    home = str(Path.home())
    normalized_path = os.path.normcase(path_str)
    normalized_home = os.path.normcase(home)

    if normalized_path == normalized_home:
        return '~'
    if normalized_path.startswith(normalized_home + os.sep):
        return '~' + path_str[len(home):]

    return path_str


def _credentials_status() -> str:
    """Check if the credentials file exists (never reads its content)."""
    config_dir = Path(os.environ.get('CLAUDE_CONFIG_DIR', '')) if os.environ.get('CLAUDE_CONFIG_DIR') else Path.home() / '.claude'
    cred_path = config_dir / '.credentials.json'
    display_path = _redact_home(str(cred_path))

    if cred_path.exists():
        return f'found ({display_path})'

    return f'NOT FOUND ({display_path})'


def print_startup_diagnostics() -> None:
    """Print system and environment diagnostics before webview starts."""
    from . import __version__

    print(f'\n  Usage Monitor for Claude v{__version__} - Verbose Mode')
    print(f'  {"=" * 48}')

    _section('System')
    for label, value in diagnostic_system_rows():
        _row(label, value)

    _section('Python')
    _row('Version', sys.version.split()[0])
    _row('Executable', _redact_home(sys.executable))
    frozen = getattr(sys, 'frozen', False)
    _row('Frozen (PyInstaller)', str(frozen))
    if frozen:
        _row('Bundle dir', _redact_home(getattr(sys, '_MEIPASS', 'unknown')))

    _section('Locale')
    sys_locale = locale.getlocale()
    _row('System locale', f'{sys_locale[0]}, {sys_locale[1]}' if sys_locale[0] else 'not set')
    _row('Filesystem encoding', sys.getfilesystemencoding())
    _row('Default encoding', sys.getdefaultencoding())
    _row('CLAUDE_CONFIG_DIR', _redact_home(os.environ.get('CLAUDE_CONFIG_DIR', '')) or '(not set)')

    _section('Display')
    for label, value in diagnostic_display_rows():
        _row(label, value)

    _section('Runtimes')
    for label, value in diagnostic_runtime_rows():
        _row(label, value)

    _section('Dependencies')
    for package in DIAGNOSTIC_PACKAGES:
        _row(package, _package_version(package))

    _section('Credentials')
    _row('File', _credentials_status())

    print()


def print_runtime_diagnostics() -> None:
    """Print diagnostics that are only available after the GUI toolkit has loaded."""
    import webview  # type: ignore[import-untyped]  # no type stubs available

    _section('Runtime (post-init)')

    _row('Webview renderer', getattr(webview, 'renderer', None) or 'unknown')

    guilib = getattr(webview, 'guilib', None)
    _row('GUI backend', guilib.__name__ if guilib else 'unknown')

    for label, value in diagnostic_post_init_rows():
        _row(label, value)

    print()

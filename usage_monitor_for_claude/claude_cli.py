"""
Claude CLI
===========

Discovers Claude Code installations on the system and provides
token refresh via the ``claude update`` command.  Does not handle
credentials directly - delegates to the Claude CLI binary.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .settings import CLI_COMMAND


def _discover_cli_path() -> Path:
    """Discover the Claude Code CLI binary path.

    Strategy
    --------
    1. ``shutil.which('claude')`` - respects PATH and PATHEXT.  A typical
       npm install resolves to ``claude.cmd`` in ``%APPDATA%\\npm`` because
       ``.CMD`` is in the default PATHEXT.
    2. If the result is a ``.ps1`` shim (uncommon - happens when the user
       has added ``.PS1`` to PATHEXT), substitute the sibling ``.cmd`` or
       ``.exe``; subprocess cannot directly execute PowerShell scripts.
    3. Fall back to the standard npm location at ``%APPDATA%\\npm``.
    4. Last resort: return the native Windows installer path so callers'
       ``is_file()`` checks fail gracefully and produce sensible logs.
    """
    found = shutil.which('claude')
    if found:
        path = Path(found)
        if path.suffix.lower() == '.ps1':
            for ext in ('.cmd', '.exe'):
                alt = path.with_suffix(ext)
                if alt.is_file():
                    return alt
        return path

    appdata = os.environ.get('APPDATA')
    if appdata:
        for name in ('claude.cmd', 'claude.exe'):
            candidate = Path(appdata) / 'npm' / name
            if candidate.is_file():
                return candidate

    return Path.home() / '.local' / 'bin' / 'claude.exe'


# Resolved at import time. The CLI path doesn't move during runtime.
CLAUDE_CLI_PATH = _discover_cli_path()

_EXTENSION_DIRS: list[tuple[str, Path]] = [
    ('VS Code', Path.home() / '.vscode' / 'extensions'),
    ('VS Code Insiders', Path.home() / '.vscode-insiders' / 'extensions'),
    ('Cursor', Path.home() / '.cursor' / 'extensions'),
    ('Windsurf', Path.home() / '.windsurf' / 'extensions'),
]
_EXTENSION_PREFIX = 'anthropic.claude-code-'

CHANGELOG_URL = 'https://github.com/anthropics/claude-code/blob/main/CHANGELOG.md'
PROJECT_URL = 'https://github.com/jens-duttke/usage-monitor-for-claude'

__all__ = ['CLAUDE_CLI_PATH', 'CHANGELOG_URL', 'PROJECT_URL', 'ClaudeInstallation', 'RefreshResult', 'cli_version', 'find_installations', 'refresh_token']

# Cache: path → (mtime, version) - avoids re-running subprocess when the binary hasn't changed
_version_cache: dict[Path, tuple[float, str]] = {}

# Cache for a custom cli_command version, keyed by the command tuple.  A custom
# command (e.g. a WSL invocation) has no local file to stat for change
# detection, so its version is cached for the process lifetime: updating that
# CLI is picked up on the next app start.  Spawning it per read is not an
# option - the popup re-reads on every data change, which would boot WSL every
# few minutes.
_command_version_cache: dict[tuple[str, ...], str] = {}


@dataclass
class ClaudeInstallation:
    """A discovered Claude Code installation."""

    name: str
    version: str
    path: Path


@dataclass
class RefreshResult:
    """Result of a ``claude update`` invocation."""

    success: bool
    updated: bool
    old_version: str
    new_version: str
    error: str


def find_installations() -> list[ClaudeInstallation]:
    """Discover Claude Code installations on the system.

    Checks the native CLI path, any ``cli_command`` configured by the user
    (e.g. a WSL install), and common IDE extension directories.  Extension
    versions are extracted from directory names (no subprocess needed).
    CLI versions are read via ``claude --version``.

    Returns
    -------
    list[ClaudeInstallation]
        Found installations, native CLI first, then configured commands,
        then IDE extensions.
    """
    results: list[ClaudeInstallation] = []

    # Native CLI
    if CLAUDE_CLI_PATH.is_file():
        version = cli_version(CLAUDE_CLI_PATH)
        if version:
            results.append(ClaudeInstallation('CLI', version, CLAUDE_CLI_PATH))

    # Configured commands - listed in addition to the native CLI, which stays
    # visible because it is the install this app authenticates and refreshes with
    for name, command in CLI_COMMAND.items():
        version = _command_version(command)
        if version:
            # A custom command has no single binary path; its last argument
            # is the closest match (e.g. the claude path behind ``wsl``).
            results.append(ClaudeInstallation(name, version, Path(command[-1])))

    # IDE extensions - extract version from directory name
    for ide_name, ext_dir in _EXTENSION_DIRS:
        try:
            if not ext_dir.is_dir():
                continue

            best_version = ''
            best_parts: tuple[int, ...] = ()
            best_path = None
            for entry in ext_dir.iterdir():
                if not entry.name.startswith(_EXTENSION_PREFIX):
                    continue
                # Directory name format: anthropic.claude-code-X.Y.Z-win32-x64
                remainder = entry.name[len(_EXTENSION_PREFIX):]
                match = re.match(r'(\d+\.\d+\.\d+)', remainder)
                if match:
                    version = match.group(1)
                    parts = tuple(int(x) for x in version.split('.'))
                    if parts > best_parts:
                        best_version = version
                        best_parts = parts
                        best_path = entry
        except OSError:
            # A directory that exists but cannot be enumerated (ACL denial,
            # broken junction, cloud placeholder) must not break the popup.
            continue

        if best_version and best_path:
            results.append(ClaudeInstallation(ide_name, best_version, best_path))

    return results


def refresh_token() -> RefreshResult:
    """Run ``claude update`` to refresh the OAuth token.

    Uses the native CLI binary only - a ``cli_command`` entry is display
    only.  The refresh works because the CLI renews the expired token in
    the credentials file this app reads; a CLI behind ``cli_command``
    (e.g. a WSL install) keeps its own credentials inside WSL and would
    leave that file untouched, so the token would never change.

    Returns
    -------
    RefreshResult
        Outcome of the update attempt.
    """
    if not CLAUDE_CLI_PATH.is_file():
        return RefreshResult(success=False, updated=False, old_version='', new_version='', error='CLI not found')

    try:
        proc = subprocess.run(
            [str(CLAUDE_CLI_PATH), 'update'],
            capture_output=True, text=True, encoding='utf-8', errors='replace',
            timeout=60, creationflags=subprocess.CREATE_NO_WINDOW,
        )
    except subprocess.TimeoutExpired:
        return RefreshResult(success=False, updated=False, old_version='', new_version='', error='Timeout')
    except OSError as e:
        return RefreshResult(success=False, updated=False, old_version='', new_version='', error=str(e))

    # ``claude update`` emits UTF-8; decoding it with the ambient locale codec
    # (e.g. cp950) can kill the pipe-reader thread and leave a stream as None,
    # so decode as UTF-8 above and defend against a None stream here (#80).
    output = (proc.stdout or '') + (proc.stderr or '')

    # Parse: "Successfully updated from X.Y.Z to version A.B.C"
    update_match = re.search(r'updated from (\S+) to (?:version )?(\S+)', output)
    if update_match:
        return RefreshResult(
            success=True, updated=True,
            old_version=update_match.group(1), new_version=update_match.group(2),
            error='',
        )

    # Parse: "Claude Code is up to date (X.Y.Z)"
    uptodate_match = re.search(r'up to date \((\S+)\)', output)
    if uptodate_match:
        return RefreshResult(
            success=True, updated=False,
            old_version=uptodate_match.group(1), new_version=uptodate_match.group(1),
            error='',
        )

    # Command ran but output was unexpected
    if proc.returncode == 0:
        return RefreshResult(success=True, updated=False, old_version='', new_version='', error='')

    return RefreshResult(success=False, updated=False, old_version='', new_version='', error=output.strip()[:200])


def cli_version(path: Path) -> str:
    """Run ``claude --version`` and return the version string, or ``''``.

    Results are cached by file modification time so the subprocess is
    only spawned once per binary change (i.e. after an update).
    """
    try:
        mtime = path.stat().st_mtime
        cached = _version_cache.get(path)
        if cached and cached[0] == mtime:
            return cached[1]

        proc = subprocess.run(
            [str(path), '--version'],
            capture_output=True, text=True, encoding='utf-8', errors='replace',
            timeout=10, creationflags=subprocess.CREATE_NO_WINDOW,
        )
        # ``claude --version`` emits UTF-8; decode it as UTF-8 (not the ambient
        # locale codec, e.g. cp950) so a non-ASCII glyph cannot kill the
        # pipe-reader thread and leave ``proc.stdout`` as None (#80).
        version = _parse_version(proc.stdout or '')
        _version_cache[path] = (mtime, version)
        return version
    except Exception:
        return ''


def _command_version(command: list[str]) -> str:
    """Run ``<command> --version`` and return the version string, or ``''``.

    Used for a custom ``cli_command`` that has no local file to stat; the
    result is cached per command tuple for the process lifetime.
    """
    key = tuple(command)
    cached = _command_version_cache.get(key)
    if cached is not None:
        return cached

    try:
        proc = subprocess.run(
            [*command, '--version'],
            capture_output=True, text=True, encoding='utf-8', errors='replace',
            timeout=10, creationflags=subprocess.CREATE_NO_WINDOW,
        )
    except Exception:
        return ''

    # ``--version`` output is UTF-8; decode it as UTF-8 above (not the ambient
    # locale codec) so a non-ASCII glyph cannot kill the pipe-reader thread and
    # leave ``proc.stdout`` as None.  This parse runs outside the ``try`` and
    # find_installations() calls this unguarded, so also defend against a None
    # stdout here rather than raising AttributeError into the popup (#80).
    version = _parse_version(proc.stdout or '')
    _command_version_cache[key] = version
    return version


def _parse_version(output: str) -> str:
    """Extract a leading ``X.Y.Z`` version from ``--version`` output.

    Output format: ``"2.1.69 (Claude Code)"``.
    """
    match = re.match(r'(\d+\.\d+\.\d+)', output.strip())
    return match.group(1) if match else ''

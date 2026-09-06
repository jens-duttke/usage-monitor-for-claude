"""
Build Script
=============

Builds a standalone EXE for Claude&CodexUsage using PyInstaller.

Usage:
    python build.py

Produces:
    dist/Claude&CodexUsage.exe
"""
from __future__ import annotations

import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).parent
DIST = ROOT / 'dist'
SPEC = ROOT / 'usage_monitor_for_claude.spec'
EXE = DIST / 'Claude&CodexUsage.exe'
CRITICAL_ASSETS = (
    'usage_monitor_for_claude/notification_logo.ico',
    'usage_monitor_for_claude/popup/popup.html',
    'usage_monitor_for_claude/popup/popup.css',
    'usage_monitor_for_claude/popup/popup.js',
    'locale/en.json',
)


def _expected_file_version() -> str:
    """Read the four-part file version from version_info.py."""
    version_info = (ROOT / 'version_info.py').read_text(encoding='utf-8')
    match = re.search(r"StringStruct\('FileVersion',\s*'([^']+)'\)", version_info)
    if match is None:
        raise RuntimeError('FileVersion is missing from version_info.py')
    return match.group(1)


def _archive_contents(exe: Path) -> set[str]:
    """Return the files embedded in a one-file PyInstaller executable."""
    result = subprocess.run(
        [sys.executable, '-m', 'PyInstaller.utils.cliutils.archive_viewer', '-l', '-b', str(exe)],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        check=True,
    )
    return {line.strip().replace('\\', '/') for line in result.stdout.splitlines() if line.strip()}


def _file_version(exe: Path) -> str:
    """Read the Windows file version from an executable."""
    command = (
        f"(Get-Item -LiteralPath '{exe.resolve()}').VersionInfo.FileVersion"
    )
    result = subprocess.run(
        ['powershell', '-NoProfile', '-NonInteractive', '-Command', command],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def validate_build(exe: Path = EXE, started_at_ns: int | None = None) -> None:
    """Reject an absent, stale, incomplete, or incorrectly versioned executable."""
    if not exe.is_file():
        raise RuntimeError(f'Build failed - EXE not found: {exe}')
    if started_at_ns is not None and exe.stat().st_mtime_ns < started_at_ns:
        raise RuntimeError(f'Build produced a stale EXE: {exe}')

    expected_version = _expected_file_version()
    actual_version = _file_version(exe)
    if actual_version != expected_version:
        raise RuntimeError(f'EXE version mismatch: expected {expected_version}, got {actual_version or "<empty>"}')

    contents = _archive_contents(exe)
    missing_assets = [asset for asset in CRITICAL_ASSETS if asset not in contents]
    if missing_assets:
        raise RuntimeError(f'EXE is missing critical assets: {", ".join(missing_assets)}')


def build() -> None:
    """Run PyInstaller and validate the resulting standalone EXE."""
    print('Starting PyInstaller build ...')
    started_at_ns = time.time_ns()
    cmd = [sys.executable, '-m', 'PyInstaller', '--clean', '--noconfirm', str(SPEC)]
    subprocess.check_call(cmd, cwd=str(ROOT))

    validate_build(EXE, started_at_ns)
    size_mb = EXE.stat().st_size / (1024 * 1024)
    print(f'\nBuild successful!  {EXE}  ({size_mb:.1f} MB)')


if __name__ == '__main__':
    build()

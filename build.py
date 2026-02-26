"""
Build Script
=============

Builds a standalone EXE for Usage Monitor for Claude using PyInstaller.

Usage:
    python build.py

Produces:
    dist/UsageMonitorForClaude.exe
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent
DIST = ROOT / 'dist'
SPEC = ROOT / 'usage_monitor_for_claude.spec'


def build() -> None:
    """Run PyInstaller to produce the standalone EXE."""
    print('Starting PyInstaller build ...')
    cmd = [sys.executable, '-m', 'PyInstaller', '--clean', '--noconfirm', str(SPEC)]
    subprocess.check_call(cmd, cwd=str(ROOT))

    exe = DIST / 'UsageMonitorForClaude.exe'
    if exe.exists():
        size_mb = exe.stat().st_size / (1024 * 1024)
        print(f'\nBuild successful!  {exe}  ({size_mb:.1f} MB)')
    else:
        print('\nBuild failed - EXE not found.')
        sys.exit(1)


if __name__ == '__main__':
    build()

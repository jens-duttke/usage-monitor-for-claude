"""
Build Validation Tests
======================

Behavioral tests for validation of the packaged executable.
"""
from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import build


class TestValidateBuild(unittest.TestCase):
    """Tests for rejecting invalid packaged executables and accepting complete ones."""

    def _exe_path(self, temporary_directory: str) -> Path:
        exe = Path(temporary_directory) / 'Claude&CodexUsage.exe'
        exe.write_bytes(b'executable')
        return exe

    def test_rejects_missing_exe(self):
        """A build with no executable fails validation before inspecting its contents."""
        with TemporaryDirectory() as temporary_directory:
            exe = Path(temporary_directory) / 'Claude&CodexUsage.exe'
            with self.assertRaisesRegex(RuntimeError, r'EXE not found'):
                build.validate_build(exe)

    def test_rejects_stale_exe(self):
        """An executable older than the build start timestamp is rejected."""
        with TemporaryDirectory() as temporary_directory:
            exe = self._exe_path(temporary_directory)
            started_at_ns = exe.stat().st_mtime_ns + 1
            with self.assertRaisesRegex(RuntimeError, r'stale EXE'):
                build.validate_build(exe, started_at_ns=started_at_ns)

    def test_rejects_file_version_mismatch(self):
        """An executable carrying a different FileVersion is rejected."""
        with TemporaryDirectory() as temporary_directory:
            exe = self._exe_path(temporary_directory)
            with patch.object(build, '_expected_file_version', return_value='1.2.3.4'), \
                    patch.object(build, '_file_version', return_value='1.2.3.3'):
                with self.assertRaisesRegex(RuntimeError, r'EXE version mismatch: expected 1\.2\.3\.4, got 1\.2\.3\.3'):
                    build.validate_build(exe)

    def test_rejects_missing_critical_assets(self):
        """An executable missing a required bundled asset is rejected."""
        with TemporaryDirectory() as temporary_directory:
            exe = self._exe_path(temporary_directory)
            with patch.object(build, '_expected_file_version', return_value='1.2.3.4'), \
                    patch.object(build, '_file_version', return_value='1.2.3.4'), \
                    patch.object(build, '_archive_contents', return_value=set(build.CRITICAL_ASSETS[:-1])):
                with self.assertRaisesRegex(RuntimeError, r'EXE is missing critical assets: locale/en\.json'):
                    build.validate_build(exe)

    def test_accepts_current_exe_with_expected_version_and_assets(self):
        """A current executable with the expected version and all critical assets passes."""
        with TemporaryDirectory() as temporary_directory:
            exe = self._exe_path(temporary_directory)
            with patch.object(build, '_expected_file_version', return_value='1.2.3.4'), \
                    patch.object(build, '_file_version', return_value='1.2.3.4'), \
                    patch.object(build, '_archive_contents', return_value=set(build.CRITICAL_ASSETS)):
                build.validate_build(exe, started_at_ns=exe.stat().st_mtime_ns)


if __name__ == '__main__':
    unittest.main()

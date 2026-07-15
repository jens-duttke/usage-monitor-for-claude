"""
Autostart
==========

Manages Windows autostart via the ``HKCU\\...\\Run`` registry key.
Each monitor instance (one per Claude config directory) uses its own
registry value name and stores its ``--config-dir`` in the command.
"""
from __future__ import annotations

import os
import sys
import winreg

from .instance_id import config_dir_suffix, is_default_config_dir

__all__ = ['AUTOSTART_REG_KEY', 'AUTOSTART_REG_BASE_NAME', 'is_autostart_enabled', 'set_autostart', 'sync_autostart_path']

AUTOSTART_REG_KEY = r'Software\Microsoft\Windows\CurrentVersion\Run'
AUTOSTART_REG_BASE_NAME = 'UsageMonitorForClaude'


def _autostart_reg_name() -> str:
    """Return the per-instance registry value name."""
    return AUTOSTART_REG_BASE_NAME + config_dir_suffix()


def _autostart_command() -> str:
    """Return the command line to store in the registry for this instance."""
    command = f'"{sys.executable}"'
    if not is_default_config_dir():
        command += f' --config-dir="{os.environ["CLAUDE_CONFIG_DIR"]}"'
    return command


def is_autostart_enabled() -> bool:
    """Check whether the app is registered to start with Windows.

    Returns
    -------
    bool
        ``True`` if a matching registry value exists under ``HKCU\\...\\Run``.
    """
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, AUTOSTART_REG_KEY) as key:
            winreg.QueryValueEx(key, _autostart_reg_name())
            return True
    except FileNotFoundError:
        return False


def set_autostart(enable: bool) -> None:
    """Create or remove the autostart registry entry.

    Parameters
    ----------
    enable : bool
        ``True`` to register autostart, ``False`` to remove it.
    """
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, AUTOSTART_REG_KEY, 0, winreg.KEY_SET_VALUE) as key:
        if enable:
            winreg.SetValueEx(key, _autostart_reg_name(), 0, winreg.REG_SZ, _autostart_command())
        else:
            try:
                winreg.DeleteValue(key, _autostart_reg_name())
            except FileNotFoundError:
                pass


def sync_autostart_path() -> None:
    """Update the autostart registry command if the EXE has been moved.

    Compares the stored command with the current expected one and
    silently updates the registry value when they differ.
    """
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, AUTOSTART_REG_KEY) as key:
            stored, _ = winreg.QueryValueEx(key, _autostart_reg_name())
    except FileNotFoundError:
        return

    if stored != _autostart_command():
        set_autostart(True)

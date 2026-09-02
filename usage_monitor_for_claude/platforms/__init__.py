"""
Platform Layer
===============

Selects the operating-system backend and re-exports its API under one name,
so the rest of the package never branches on the running system.

Every backend module must import cleanly without any GUI toolkit present:
the test suite and headless tooling import this package, and the Linux
backend therefore defers its ``gi`` imports until a function actually needs
them.
"""
from __future__ import annotations

import sys

IS_WINDOWS = sys.platform == 'win32'

if IS_WINDOWS:
    from .win32 import (
        DIAGNOSTIC_PACKAGES, ask_yes_no, autostart_supported, diagnostic_display_rows, diagnostic_post_init_rows,
        diagnostic_runtime_rows, diagnostic_system_rows, double_click_seconds, get_idle_seconds,
        install_tray_click_handler, is_autostart_enabled, is_workstation_locked, load_font, no_window_kwargs,
        prepare_gui_environment, register_notification_identity, set_autostart, set_dpi_awareness,
        setup_console, show_error_box,
        show_topmost_error, show_warning_box, sync_autostart_path, system_time_format,
        taskbar_uses_light_theme, watch_theme_change,
    )
else:
    from .linux import (
        DIAGNOSTIC_PACKAGES, ask_yes_no, autostart_supported, diagnostic_display_rows, diagnostic_post_init_rows,
        diagnostic_runtime_rows, diagnostic_system_rows, double_click_seconds, get_idle_seconds,
        install_tray_click_handler, is_autostart_enabled, is_workstation_locked, load_font, no_window_kwargs,
        prepare_gui_environment, register_notification_identity, set_autostart, set_dpi_awareness,
        setup_console, show_error_box,
        show_topmost_error, show_warning_box, sync_autostart_path, system_time_format,
        taskbar_uses_light_theme, watch_theme_change,
    )

__all__ = [
    'DIAGNOSTIC_PACKAGES', 'IS_WINDOWS', 'ask_yes_no', 'autostart_supported', 'diagnostic_display_rows',
    'diagnostic_post_init_rows', 'diagnostic_runtime_rows', 'diagnostic_system_rows',
    'double_click_seconds', 'get_idle_seconds', 'install_tray_click_handler', 'is_autostart_enabled',
    'is_workstation_locked',
    'load_font', 'no_window_kwargs', 'prepare_gui_environment', 'register_notification_identity', 'set_autostart',
    'set_dpi_awareness', 'setup_console', 'show_error_box', 'show_topmost_error', 'show_warning_box',
    'sync_autostart_path', 'system_time_format', 'taskbar_uses_light_theme', 'watch_theme_change',
]

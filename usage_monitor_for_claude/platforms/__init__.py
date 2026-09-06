"""
Windows Platform Layer
======================

Re-exports the Windows system integration API under one name so the rest of
the package never needs to know about platform-specific implementation details.
"""
from __future__ import annotations

from .win32 import (
    DIAGNOSTIC_PACKAGES, ask_yes_no, autostart_supported, diagnostic_display_rows, diagnostic_post_init_rows,
    diagnostic_runtime_rows, diagnostic_system_rows, double_click_seconds, dual_tray_supported, get_idle_seconds,
    install_tray_click_handler, is_autostart_enabled, is_screensaver_running, is_workstation_locked, load_font, no_window_kwargs,
    prepare_gui_environment, register_notification_identity, set_autostart, set_dpi_awareness,
    setup_console, show_error_box,
    show_topmost_error, show_warning_box, sync_autostart_path, system_time_format,
    taskbar_uses_light_theme, watch_theme_change,
)

__all__ = [
    'DIAGNOSTIC_PACKAGES', 'ask_yes_no', 'autostart_supported', 'diagnostic_display_rows',
    'diagnostic_post_init_rows', 'diagnostic_runtime_rows', 'diagnostic_system_rows',
    'double_click_seconds', 'dual_tray_supported', 'get_idle_seconds', 'install_tray_click_handler', 'is_autostart_enabled',
    'load_font', 'no_window_kwargs', 'prepare_gui_environment', 'register_notification_identity', 'set_autostart',
    'set_dpi_awareness', 'setup_console', 'show_error_box', 'show_topmost_error', 'show_warning_box',
    'sync_autostart_path', 'system_time_format', 'taskbar_uses_light_theme', 'watch_theme_change',
]

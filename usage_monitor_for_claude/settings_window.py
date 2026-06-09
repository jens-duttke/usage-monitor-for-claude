"""
Settings Window
================

Dark-themed WebView2 settings dialog for configuring alert thresholds.
Opens near the system tray like the popup.
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes
import json
import logging
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any

import webview  # type: ignore[import-untyped]

from .i18n import T
from .settings import BG, get_alert_thresholds, save_user_setting

if TYPE_CHECKING:
    from .app import UsageMonitorForClaude

__all__ = ['open_settings_window']

log = logging.getLogger(__name__)

_SETTINGS_DIR = Path(__file__).parent / 'settings_ui'
_BASELINE_DPI = 96
_GWL_EXSTYLE = -20
_WS_EX_TOOLWINDOW = 0x00000080
_WS_EX_APPWINDOW  = 0x00040000
_WS_EX_LAYERED    = 0x00080000
_LWA_ALPHA        = 0x00000002


class _MONITORINFO(ctypes.Structure):
    _fields_ = [
        ('cbSize',    ctypes.wintypes.DWORD),
        ('rcMonitor', ctypes.wintypes.RECT),
        ('rcWork',    ctypes.wintypes.RECT),
        ('dwFlags',   ctypes.wintypes.DWORD),
    ]


# ---------------------------------------------------------------------------
# JS-callable API
# ---------------------------------------------------------------------------

class _SettingsApi:
    """Methods exposed to the settings JS via pywebview's JS bridge."""

    def __init__(self, win: _SettingsWindow) -> None:
        self._win = win

    def report_height(self, height: int) -> None:
        if height and height != self._win._last_height:
            self._win._last_height = height
            self._win._resize_and_position(height)
            if not self._win._shown:
                self._win._show()

    def close(self) -> None:
        self._win._close()

    def save(self, data: dict) -> None:
        self._win._on_save(data)


# ---------------------------------------------------------------------------
# Window
# ---------------------------------------------------------------------------

class _SettingsWindow:
    WIDTH = 310

    def __init__(self, app: UsageMonitorForClaude) -> None:
        self.app = app
        self._hwnd = 0
        self._last_height = 260
        self._shown = False
        self._closed = threading.Event()

        api = _SettingsApi(self)
        self._window = webview.create_window(
            T.get('settings_title', 'Settings'),
            url=str(_SETTINGS_DIR / 'settings.html'),
            width=self.WIDTH, height=self._last_height,
            resizable=False, frameless=True, shadow=False,
            easy_drag=False, on_top=True, hidden=True,
            background_color=BG,
            js_api=api,
        )
        self._window.events.loaded += self._on_loaded
        self._window.events.closed += self._on_closed
        self._closed.wait()

    # ── webview events ──────────────────────────────────────────────────────

    def _on_loaded(self) -> None:
        config = self._build_config()
        self._window.evaluate_js(f'init({json.dumps(config)})')

        self._hwnd = self._window.native.Handle.ToInt32()
        ex_style = ctypes.windll.user32.GetWindowLongW(self._hwnd, _GWL_EXSTYLE)
        ctypes.windll.user32.SetWindowLongW(
            self._hwnd, _GWL_EXSTYLE,
            (ex_style | _WS_EX_TOOLWINDOW | _WS_EX_LAYERED) & ~_WS_EX_APPWINDOW,
        )
        ctypes.windll.user32.SetLayeredWindowAttributes(self._hwnd, 0, 0, _LWA_ALPHA)
        self._window.show()

    def _on_closed(self) -> None:
        self._closed.set()

    # ── internal ────────────────────────────────────────────────────────────

    def _build_config(self) -> dict[str, Any]:
        five_hour   = get_alert_thresholds('five_hour')
        seven_day   = get_alert_thresholds('seven_day')
        return {
            't': {
                'thresholds_section': T.get('settings_thresholds', 'Alert Thresholds'),
                'five_hour':          T.get('settings_five_hour', 'Hourly (5h)'),
                'seven_day':          T.get('settings_seven_day', 'Weekly (7d)'),
                'alert1':             T.get('settings_alert1', '1st alert'),
                'alert2':             T.get('settings_alert2', '2nd alert'),
                'hint_thresh':        T.get('settings_hint_thresh', 'Leave a field empty to use only one alert.'),
                'save':               T.get('settings_save', 'Save'),
                'cancel':             T.get('settings_cancel', 'Cancel'),
            },
            'data': {
                'five_hour': five_hour,
                'seven_day': seven_day,
            },
        }

    def _show(self) -> None:
        ex_style = ctypes.windll.user32.GetWindowLongW(self._hwnd, _GWL_EXSTYLE)
        ctypes.windll.user32.SetWindowLongW(self._hwnd, _GWL_EXSTYLE, ex_style & ~_WS_EX_LAYERED)
        self._shown = True

    def _close(self) -> None:
        try:
            self._window.destroy()
        except Exception:
            pass
        self._closed.set()

    def _on_save(self, data: dict) -> None:
        try:
            five_hour = [int(v) for v in data.get('five_hour', []) if 1 <= int(v) <= 99]
            seven_day = [int(v) for v in data.get('seven_day', []) if 1 <= int(v) <= 99]

            if not five_hour or not seven_day:
                log.warning('Settings save: empty threshold list, ignoring')
                return

            save_user_setting('alert_thresholds_five_hour', five_hour)
            save_user_setting('alert_thresholds_seven_day', seven_day)

        except Exception:
            log.exception('Failed to save settings')
        finally:
            self._close()

    def _tray_position(self, phys_w: int, phys_h: int) -> tuple[int, int]:
        tray_hwnd = ctypes.windll.user32.FindWindowW('Shell_TrayWnd', None)
        hmon = ctypes.windll.user32.MonitorFromWindow(tray_hwnd, 2)
        mi = _MONITORINFO()
        mi.cbSize = ctypes.sizeof(_MONITORINFO)
        ctypes.windll.user32.GetMonitorInfoW(hmon, ctypes.byref(mi))
        mon = mi.rcMonitor
        work = mi.rcWork
        dpi = ctypes.windll.user32.GetDpiForWindow(self._hwnd) or ctypes.windll.user32.GetDpiForSystem()
        scale = dpi / _BASELINE_DPI
        margin = 12
        x = work.left + margin if work.left > mon.left else work.right - phys_w - margin
        y = work.top  + margin if work.top  > mon.top  else work.bottom - phys_h - margin
        return int(x / scale), int(y / scale)

    def _resize_and_position(self, height: int) -> None:
        dpi = ctypes.windll.user32.GetDpiForWindow(self._hwnd) or ctypes.windll.user32.GetDpiForSystem()
        scale = dpi / _BASELINE_DPI
        phys_w = int(self.WIDTH * scale)
        phys_h = int(height * scale)
        self._window.resize(self.WIDTH, height)
        x, y = self._tray_position(phys_w, phys_h)
        self._window.move(x, y)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def open_settings_window(app: UsageMonitorForClaude) -> None:
    """Open the settings window (blocks until closed)."""
    _SettingsWindow(app)

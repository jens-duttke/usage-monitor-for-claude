"""
Usage Monitor for Claude
=========================

Displays the current Claude.ai usage as a system tray icon.
Left-click the icon to see a detailed usage popup.

Authenticates via Claude Code OAuth token from
~/.claude/.credentials.json (requires Claude Code login).
"""
from __future__ import annotations

import ctypes
import functools
import json
import locale
import os
import sys
import threading
import time
import tkinter as tk
import traceback
import winreg
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

import pystray  # type: ignore[import-untyped]  # no type stubs available
import requests
from PIL import Image, ImageDraw, ImageFont

# ── Configuration ──────────────────────────────────────────────
POLL_INTERVAL = 120  # Seconds between updates
POLL_FAST = 60  # Polling interval when usage is actively increasing
POLL_FAST_EXTRA = 2  # Extra fast polls after usage stops increasing
POLL_ERROR = 30  # Polling interval after a failed request

AUTOSTART_REG_KEY = r'Software\Microsoft\Windows\CurrentVersion\Run'
AUTOSTART_REG_NAME = 'UsageMonitorForClaude'
THEME_REG_KEY = r'Software\Microsoft\Windows\CurrentVersion\Themes\Personalize'
THEME_REG_VALUE = 'SystemUsesLightTheme'
REG_NOTIFY_CHANGE_LAST_SET = 0x00000004

API_URL_USAGE = 'https://api.anthropic.com/api/oauth/usage'
API_URL_PROFILE = 'https://api.anthropic.com/api/oauth/profile'
CLAUDE_CREDENTIALS = Path.home() / '.claude' / '.credentials.json'

# ── Theme ──────────────────────────────────────────────────────
BG = '#1e1e1e'
FG = '#cccccc'
FG_DIM = '#888888'
FG_HEADING = '#ffffff'
BAR_BG = '#333333'
BAR_FG = '#4a9eff'
BAR_FG_HIGH = '#e05050'
# ───────────────────────────────────────────────────────────────

# ── i18n ──────────────────────────────────────────────────────
LOCALE_DIR = Path(__file__).parent / 'locale'


def detect_lang_code(lang: str) -> str:
    """Detect locale file code from system locale string using convention-based lookup.

    Lookup chain: ``{lang}-{REGION}.json`` → ``{lang}.json`` → ``en.json``.
    No mapping required - the locale directory structure *is* the configuration.

    Parameters
    ----------
    lang : str
        System locale string, e.g. ``'de_DE'`` or ``'German_Germany'``.

    Returns
    -------
    str
        Locale file code (without ``.json``).
    """
    normalized = locale.normalize(lang).split('.')[0]
    parts = normalized.split('_', 1)
    base = parts[0].lower()

    # locale.normalize() doesn't resolve all Windows names (e.g. 'Spanish_Mexico').
    # If the base is not a short ISO 639 code, retry with just the language word.
    if len(base) > 3:
        base = locale.normalize(parts[0]).split('.')[0].split('_')[0].lower()

    region = parts[1] if len(parts) > 1 and len(base) <= 3 else ''

    if region and (LOCALE_DIR / f'{base}-{region}.json').exists():
        return f'{base}-{region}'
    if (LOCALE_DIR / f'{base}.json').exists():
        return base

    return 'en'


def load_translations() -> dict[str, Any]:
    """Load translations for the detected system language, fallback to English."""
    lang = locale.getlocale()[0] or ''
    lang_code = detect_lang_code(lang)

    return json.loads((LOCALE_DIR / f'{lang_code}.json').read_text(encoding='utf-8'))


T: dict[str, Any] = load_translations()
# ───────────────────────────────────────────────────────────────


def api_headers() -> dict[str, str] | None:
    """Return auth headers for the Anthropic OAuth API, or None."""
    if not CLAUDE_CREDENTIALS.exists():
        return None

    try:
        creds = json.loads(CLAUDE_CREDENTIALS.read_text())
        token = creds.get('claudeAiOauth', {}).get('accessToken')
        if not token:
            return None

        return {
            'Authorization': f'Bearer {token}',
            'Content-Type': 'application/json',
            'User-Agent': 'usage-monitor-for-claude/1.0',
            'anthropic-beta': 'oauth-2025-04-20',
        }
    except (json.JSONDecodeError, KeyError):
        return None


def fetch_usage() -> dict[str, Any]:
    """Fetch usage data from the Anthropic OAuth usage API."""
    headers = api_headers()
    if not headers:
        return {'error': T['no_token']}

    try:
        resp = requests.get(API_URL_USAGE, headers=headers, timeout=10)
        resp.raise_for_status()
        return resp.json()
    except requests.ConnectionError:
        return {'error': T['connection_error']}
    except requests.HTTPError as e:
        code = e.response.status_code if e.response is not None else 0
        if code == 401:
            return {'error': T['auth_expired'], 'auth_error': True}
        return {'error': T['http_error'].format(code=code or '?')}
    except Exception:
        return {'error': T['connection_error']}


def fetch_profile() -> dict[str, Any] | None:
    """Fetch account profile from the Anthropic OAuth profile API."""
    headers = api_headers()
    if not headers:
        return None

    try:
        resp = requests.get(API_URL_PROFILE, headers=headers, timeout=10)
        resp.raise_for_status()
        return resp.json()
    except Exception:
        return None


PERIOD_5H = 5 * 3600
PERIOD_7D = 7 * 24 * 3600


def elapsed_pct(resets_at: str, period_seconds: int) -> float | None:
    """Return elapsed percentage of a usage period, or None if not calculable.

    Parameters
    ----------
    resets_at : str
        ISO 8601 timestamp when the limit resets.
    period_seconds : int
        Total duration of the period in seconds (e.g. 18000 for 5h).

    Returns
    -------
    float or None
        Percentage of the period that has already elapsed (0-100),
        or None if the value cannot be determined.
    """
    if not resets_at or period_seconds <= 0:
        return None

    try:
        reset = datetime.fromisoformat(resets_at)
        now = datetime.now(timezone.utc)
        remaining = (reset - now).total_seconds()
        elapsed = period_seconds - remaining

        return max(0.0, min(100.0, elapsed / period_seconds * 100))
    except Exception:
        return None


def time_until(iso_str: str) -> str:
    """Return human-readable reset time.

    Same day:  "Resets in 2h 20m (14:30)"
    Tomorrow:  "Resets tomorrow, 12:00"
    Later:     "Resets Sat., 12:00"
    """
    try:
        reset = datetime.fromisoformat(iso_str)
        now = datetime.now(timezone.utc)
        diff = reset - now

        total_min = max(0, int(diff.total_seconds() / 60))
        if total_min == 0:
            return ''

        reset_local = reset.astimezone()
        today = datetime.now().date()
        if reset_local.second >= 30:
            reset_local = reset_local.replace(second=0) + timedelta(minutes=1)
        else:
            reset_local = reset_local.replace(second=0)
        reset_date = reset_local.date()
        time_str = reset_local.strftime('%H:%M')

        if reset_date == today:
            if total_min >= 60:
                duration = T['duration_hm'].format(h=total_min // 60, m=total_min % 60)
            else:
                duration = T['duration_m'].format(m=total_min)
            return T['resets_in'].format(duration=duration, clock=time_str)

        if reset_date == today + timedelta(days=1):
            return T['resets_tomorrow'].format(clock=time_str)

        wd = T['weekdays'][reset_local.weekday()]
        return T['resets_weekday'].format(day=wd, clock=time_str)
    except Exception:
        return ''


def format_tooltip(data: dict[str, Any]) -> str:
    """Format usage data as short tooltip text."""
    if 'error' in data:
        if data.get('auth_error'):
            return f"{T['auth_expired_label']}\n{T['auth_expired_short']}"
        return f"{T['error_label']}\n{data['error'][:80]}"

    lines = [T['title']]
    for key, short in [('five_hour', '5h'), ('seven_day', '7d')]:
        entry = data.get(key)
        if entry and entry.get('utilization') is not None:
            pct = f"{entry['utilization']:.0f}%"
            reset = time_until(entry.get('resets_at', ''))
            line = f'{short}: {pct}'
            if reset:
                line += f' ({reset})'
            lines.append(line)

    return '\n'.join(lines)


# ── Icon creation ──────────────────────────────────────────────
# Monochrome icon for the Windows system tray (adapts to taskbar theme).
# Layout (64x64): "C" at the top, two thin progress bars at the bottom
# (session / weekly) with proportional fill.

ICON_LIGHT = {  # Light icons for dark taskbar (default)
    'fg': (255, 255, 255, 255),
    'fg_half': (255, 255, 255, 80),
    'fg_dim': (255, 255, 255, 140),
}
ICON_DARK = {  # Dark icons for light taskbar
    'fg': (0, 0, 0, 255),
    'fg_half': (0, 0, 0, 80),
    'fg_dim': (0, 0, 0, 140),
}
TRANSPARENT = (0, 0, 0, 0)


@functools.lru_cache(maxsize=None)
def load_font(size: int, symbol: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """Load font at given size. Use symbol=True for Unicode glyphs not in Arial."""
    windir = os.environ.get('WINDIR', 'C:\\Windows')
    if symbol:
        names = (f'{windir}\\Fonts\\seguisym.ttf', 'seguisym.ttf')
    else:
        names = (f'{windir}\\Fonts\\arialbd.ttf', 'arialbd.ttf', f'{windir}\\Fonts\\arial.ttf', 'arial.ttf')
    for name in names:
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue

    return ImageFont.load_default()


def taskbar_uses_light_theme() -> bool:
    """Return True if the Windows taskbar uses the light theme.

    Reads ``SystemUsesLightTheme`` from the Personalize registry key.
    Returns False (dark) if the value cannot be read.
    """
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, THEME_REG_KEY) as key:
            value, _ = winreg.QueryValueEx(key, THEME_REG_VALUE)
            return bool(value)
    except OSError:
        return False


def watch_theme_change(callback: Callable[[], None]) -> None:
    """Block the current thread and call *callback* whenever the taskbar theme changes.

    Uses ``RegNotifyChangeKeyValue`` to sleep until the registry key
    is modified, avoiding any polling.  Designed to run in a daemon thread.
    """
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, THEME_REG_KEY, 0, winreg.KEY_READ) as key:
        while True:
            if ctypes.windll.advapi32.RegNotifyChangeKeyValue(int(key), False, REG_NOTIFY_CHANGE_LAST_SET, None, False) != 0:
                return
            callback()


def create_icon_image(pct_5h: float, pct_7d: float, light_taskbar: bool = False) -> Image.Image:
    """Create monochrome tray icon: 'C' letter + two usage bars."""
    colors = ICON_DARK if light_taskbar else ICON_LIGHT
    fg, fg_half = colors['fg'], colors['fg_half']

    S = 64
    img = Image.new('RGBA', (S, S), TRANSPARENT)
    draw = ImageDraw.Draw(img)

    # ── Top text: "C", percentage when usage > 50%, or "✕" at 100% ──
    stroke_width = 0
    if pct_5h >= 100:
        text, font = '\u2715', load_font(36, symbol=True)
        stroke_width = 2
    elif pct_5h > 50:
        text, font = f'{pct_5h:.0f}', load_font(40)
    else:
        text, font = 'C', load_font(42)

    bbox = draw.textbbox((0, 0), text, font=font, stroke_width=stroke_width)
    tw = bbox[2] - bbox[0]
    draw.text(((S - tw) / 2 - bbox[0], -bbox[1]), text, fill=fg, font=font, stroke_width=stroke_width, stroke_fill=fg)

    # ── Progress bars – full width, flush to bottom ──
    bar_h = 9
    gap = 3
    bar2_y = S - bar_h
    bar1_y = bar2_y - gap - bar_h

    for y, pct in ((bar1_y, pct_5h), (bar2_y, pct_7d)):
        draw.rectangle([0, y, S - 1, y + bar_h - 1], fill=fg_half)
        fill_w = max(0, min(S, int(S * pct / 100)))
        if fill_w > 0:
            draw.rectangle([0, y, fill_w - 1, y + bar_h - 1], fill=fg)

    return img


def create_status_image(text: str, light_taskbar: bool = False) -> Image.Image:
    """Create monochrome centered-text icon for error/status states."""
    fg_dim = (ICON_DARK if light_taskbar else ICON_LIGHT)['fg_dim']

    S = 64
    img = Image.new('RGBA', (S, S), TRANSPARENT)
    draw = ImageDraw.Draw(img)
    font = load_font(46)
    bbox = draw.textbbox((0, 0), text, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    draw.text(((S - tw) / 2 - bbox[0], (S - th) / 2 - bbox[1]), text, fill=fg_dim, font=font)

    return img


# ── Popup window ───────────────────────────────────────────────

class UsagePopup:
    """Dark-themed popup window showing account info and usage bars."""

    WIDTH = 340
    REFRESH_MS = 60_000

    def __init__(self, app: UsageMonitorForClaude) -> None:
        """Create and display a popup window with usage details.

        Blocks the calling thread until the window is closed (runs its own mainloop).

        Parameters
        ----------
        app : UsageMonitorForClaude
            Parent application providing ``usage_data`` and ``profile_data``.
        """
        self.app = app
        self.root = tk.Tk()
        self.root.withdraw()
        self.win = tk.Toplevel(self.root)
        self.win.overrideredirect(True)
        self.win.attributes('-topmost', True)  # type: ignore[call-overload]  # tkinter overload stubs incomplete
        self.win.configure(bg=BG)
        self.win.minsize(self.WIDTH, 0)
        self.win.resizable(False, False)

        self._main_frame: tk.Frame | None = None
        self._usage_frame: tk.Frame | None = None
        self._usage_bars: list[dict[str, Any]] = []
        self._build_content()

        self.win.update_idletasks()
        self._position_near_tray()
        self._schedule_refresh()

        self.win.bind('<Escape>', lambda e: self._close())
        self.win.bind('<FocusOut>', lambda e: self._close())
        self.win.focus_force()

        self.root.mainloop()

    def _close(self) -> None:
        try:
            self.root.destroy()
        except tk.TclError:
            pass

    def _schedule_refresh(self) -> None:
        try:
            self.root.after(self.REFRESH_MS, self._on_refresh)
        except tk.TclError:
            pass

    def _on_refresh(self) -> None:
        try:
            self._update_usage_section()
            self._schedule_refresh()
        except tk.TclError:
            pass

    def _position_near_tray(self) -> None:
        """Place the popup in the bottom-right corner, above the Windows taskbar."""
        w = self.win.winfo_width()
        h = self.win.winfo_height()
        sx = self.win.winfo_screenwidth()
        sy = self.win.winfo_screenheight()
        # Bottom-right, above the Windows taskbar
        x = sx - w - 12
        y = sy - h - 60
        self.win.geometry(f'+{x}+{y}')

    def _build_content(self) -> None:
        """Build the popup layout: title bar, account info, and usage section."""
        pad = 16
        self._main_frame = tk.Frame(self.win, bg=BG, padx=pad)
        self._main_frame.pack(fill='both', expand=True, pady=(12, 16))

        # ── Title bar ──
        title_frame = tk.Frame(self._main_frame, bg=BG)
        title_frame.pack(fill='x', pady=(0, 4))
        tk.Label(title_frame, text=T['title'], font=('Segoe UI', 13, 'bold'), fg=FG_HEADING, bg=BG).pack(side='left')
        close_btn = tk.Label(title_frame, text='\u00d7', font=('Segoe UI', 16), fg=FG_DIM, bg=BG, cursor='hand2')
        close_btn.pack(side='right')
        close_btn.bind('<Button-1>', lambda e: self._close())

        # ── Account section ──
        profile = self.app.profile_data
        if profile:
            self._section_heading(self._main_frame, T['account'])
            account = profile.get('account', {})
            org = profile.get('organization', {})
            plan = org.get('organization_type', '').replace('_', ' ').title()
            email = account.get('email', '')
            if email:
                self._info_row(self._main_frame, T['email'], email)
            if plan:
                self._info_row(self._main_frame, T['plan'], plan)
            tk.Frame(self._main_frame, bg=BAR_BG, height=1).pack(fill='x', pady=(10, 4))

        # ── Usage section (rebuilt on refresh) ──
        self._build_usage_section()

    def _usage_entries(self) -> list[tuple[str, dict[str, Any] | None, int]]:
        """Return the list of usage entry tuples from current data."""
        usage = self.app.usage_data
        return [
            (T['session'], usage.get('five_hour'), PERIOD_5H),
            (T['weekly'], usage.get('seven_day'), PERIOD_7D),
            (T['weekly_sonnet'], usage.get('seven_day_sonnet'), PERIOD_7D),
            (T['weekly_opus'], usage.get('seven_day_opus'), PERIOD_7D),
        ]

    def _visible_entries(self) -> list[tuple[str, dict[str, Any], int]]:
        """Return only entries that have utilization data."""
        return [(label, entry, period) for label, entry, period in self._usage_entries() if entry and entry.get('utilization') is not None]

    def _build_usage_section(self) -> None:
        """Build the usage bars section from scratch, replacing any previous content."""
        usage = self.app.usage_data

        if self._usage_frame:
            self._usage_frame.destroy()
        self._usage_bars = []

        self._usage_frame = tk.Frame(self._main_frame, bg=BG)
        self._usage_frame.pack(fill='x')

        self._section_heading(self._usage_frame, T['usage'])

        if 'error' in usage:
            tk.Label(
                self._usage_frame, text=usage['error'][:120], fg='#e05050', bg=BG,
                font=('Segoe UI', 9), wraplength=self.WIDTH - 32, justify='left',
            ).pack(anchor='w', pady=4)
            return

        first = True
        for label, entry, period in self._visible_entries():
            widgets = self._create_usage_bar(self._usage_frame, label, entry, period, first=first)
            self._usage_bars.append(widgets)
            first = False

    def _update_usage_section(self) -> None:
        """Update usage bars in-place, falling back to full rebuild if structure changed."""
        usage = self.app.usage_data
        visible = self._visible_entries()

        if 'error' in usage or len(visible) != len(self._usage_bars):
            self._build_usage_section()
            return

        for (_label, entry, period), widgets in zip(visible, self._usage_bars):
            self._update_usage_bar(widgets, entry, period)

    def _section_heading(self, parent: tk.Frame, text: str) -> None:
        tk.Label(parent, text=text, font=('Segoe UI', 9, 'bold'), fg=FG_DIM, bg=BG).pack(anchor='w', pady=(8, 2))

    def _info_row(self, parent: tk.Frame, label: str, value: str) -> None:
        row = tk.Frame(parent, bg=BG)
        row.pack(fill='x', pady=0)
        tk.Label(row, text=label, fg=FG_DIM, bg=BG, font=('Segoe UI', 10)).pack(side='left')
        tk.Label(row, text=value, fg=FG, bg=BG, font=('Segoe UI', 10)).pack(side='right')

    def _create_usage_bar(
        self, parent: tk.Frame, label: str, entry: dict[str, Any], period_seconds: int, *, first: bool = False,
    ) -> dict[str, Any]:
        """Create a usage bar group and return widget references for in-place updates."""
        pct = entry.get('utilization', 0) or 0
        resets_at = entry.get('resets_at', '')
        high = pct >= 80

        row = tk.Frame(parent, bg=BG)
        row.pack(fill='x', pady=(4 if first else 8, 4))
        tk.Label(row, text=label, fg=FG, bg=BG, font=('Segoe UI', 10), padx=0).pack(side='left')
        pct_label = tk.Label(row, text=f'{pct:.0f}%', fg=FG, bg=BG, font=('Segoe UI', 10), padx=0)
        pct_label.pack(side='right')

        bar_h = 8
        bar_frame = tk.Frame(parent, bg=BAR_BG, height=bar_h)
        bar_frame.pack(fill='x', padx=2, pady=(0, 2))
        bar_frame.pack_propagate(False)
        fill_pct = max(0.0, min(1.0, pct / 100))
        fill_frame = None
        if fill_pct > 0:
            fill_frame = tk.Frame(bar_frame, bg=BAR_FG_HIGH if high else BAR_FG)
            fill_frame.place(relwidth=fill_pct, relheight=1.0)

        time_pct = elapsed_pct(resets_at, period_seconds)
        marker_frame = None
        if time_pct is not None:
            marker_rel = max(0.0, min(1.0, time_pct / 100))
            marker_frame = tk.Frame(bar_frame, bg='#ffffff', width=1)
            marker_frame.place(relx=marker_rel, relheight=1.0, width=1)

        reset_text = time_until(resets_at) if resets_at else ''
        reset_label = tk.Label(parent, text=reset_text, fg=FG_DIM, bg=BG, font=('Segoe UI', 8))
        if reset_text:
            reset_label.pack(anchor='w')

        return {
            'pct_label': pct_label, 'bar_frame': bar_frame,
            'fill_frame': fill_frame, 'marker_frame': marker_frame, 'reset_label': reset_label,
        }

    def _update_usage_bar(self, widgets: dict[str, Any], entry: dict[str, Any], period_seconds: int) -> None:
        """Update an existing usage bar's values in-place."""
        pct = entry.get('utilization', 0) or 0
        resets_at = entry.get('resets_at', '')
        high = pct >= 80
        bar_frame = widgets['bar_frame']

        widgets['pct_label'].configure(text=f'{pct:.0f}%')

        fill_pct = max(0.0, min(1.0, pct / 100))
        color = BAR_FG_HIGH if high else BAR_FG
        if fill_pct > 0:
            if widgets['fill_frame']:
                widgets['fill_frame'].place_configure(relwidth=fill_pct)
                widgets['fill_frame'].configure(bg=color)
            else:
                widgets['fill_frame'] = tk.Frame(bar_frame, bg=color)
                widgets['fill_frame'].place(relwidth=fill_pct, relheight=1.0)
        elif widgets['fill_frame']:
            widgets['fill_frame'].destroy()
            widgets['fill_frame'] = None

        time_pct = elapsed_pct(resets_at, period_seconds)
        if time_pct is not None:
            marker_rel = max(0.0, min(1.0, time_pct / 100))
            if widgets['marker_frame']:
                widgets['marker_frame'].place_configure(relx=marker_rel)
            else:
                widgets['marker_frame'] = tk.Frame(bar_frame, bg='#ffffff', width=1)
                widgets['marker_frame'].place(relx=marker_rel, relheight=1.0, width=1)
        elif widgets['marker_frame']:
            widgets['marker_frame'].destroy()
            widgets['marker_frame'] = None

        reset_text = time_until(resets_at) if resets_at else ''
        reset_label = widgets['reset_label']
        if reset_text:
            reset_label.configure(text=reset_text)
            if not reset_label.winfo_manager():
                reset_label.pack(anchor='w')
        elif reset_label.winfo_manager():
            reset_label.pack_forget()


# ── Autostart (Windows) ───────────────────────────────────────


def is_autostart_enabled() -> bool:
    """Check whether the app is registered to start with Windows.

    Returns
    -------
    bool
        ``True`` if a matching registry value exists under ``HKCU\\...\\Run``.
    """
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, AUTOSTART_REG_KEY) as key:
            winreg.QueryValueEx(key, AUTOSTART_REG_NAME)
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
            winreg.SetValueEx(key, AUTOSTART_REG_NAME, 0, winreg.REG_SZ, f'"{sys.executable}"')
        else:
            try:
                winreg.DeleteValue(key, AUTOSTART_REG_NAME)
            except FileNotFoundError:
                pass


def sync_autostart_path() -> None:
    """Update the autostart registry path if the EXE has been moved.

    Compares the stored path with the current ``sys.executable`` and
    silently updates the registry value when they differ.
    """
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, AUTOSTART_REG_KEY) as key:
            stored, _ = winreg.QueryValueEx(key, AUTOSTART_REG_NAME)
    except FileNotFoundError:
        return

    expected = f'"{sys.executable}"'
    if stored != expected:
        set_autostart(True)


# ── Tray application ──────────────────────────────────────────


class UsageMonitorForClaude:
    """System tray application displaying Claude usage."""

    def __init__(self) -> None:
        """Set up the tray icon with context menu and polling state."""
        self.running = True
        self.usage_data = {}
        self.profile_data = None
        self._prev_5h = None
        self._prev_7d = None
        self._fast_polls_remaining = 0
        self._popup_open = False
        self._light_taskbar = taskbar_uses_light_theme()
        self.icon = pystray.Icon(
            'usage_monitor',
            icon=create_icon_image(0, 0, self._light_taskbar),
            title=T['loading'],
            menu=pystray.Menu(
                pystray.MenuItem(T['title'].replace('&', '&&'), self.on_show_popup, default=True),
                pystray.MenuItem(T['refresh'], self.on_refresh),
                pystray.MenuItem(
                    T['autostart'], self.on_toggle_autostart,
                    checked=lambda item: is_autostart_enabled(),
                    visible=getattr(sys, 'frozen', False),
                ),
                pystray.MenuItem(T['quit'], self.on_quit),
            ),
        )

    def on_show_popup(self, icon: Any = None, item: Any = None) -> None:
        if self._popup_open:
            return
        threading.Thread(target=self._open_popup, daemon=True).start()

    def on_refresh(self, icon: Any = None, item: Any = None) -> None:
        threading.Thread(target=self.update, daemon=True).start()

    def on_toggle_autostart(self, icon: Any = None, item: Any = None) -> None:
        set_autostart(not is_autostart_enabled())

    def on_quit(self, icon: Any = None, item: Any = None) -> None:
        self.running = False
        self.icon.stop()

    def _open_popup(self) -> None:
        self._popup_open = True
        try:
            self.usage_data = fetch_usage()
            if not self.profile_data:
                self.profile_data = fetch_profile()
            UsagePopup(self)
        finally:
            self._popup_open = False

    def _on_theme_changed(self) -> None:
        """Re-render the tray icon when the Windows theme changes."""
        light = taskbar_uses_light_theme()
        if light == self._light_taskbar:
            return

        self._light_taskbar = light
        if 'error' in self.usage_data:
            self.icon.icon = create_status_image('C!' if self.usage_data.get('auth_error') else '!', light)
        else:
            pct_5h = self.usage_data.get('five_hour', {}).get('utilization', 0) or 0
            pct_7d = self.usage_data.get('seven_day', {}).get('utilization', 0) or 0
            self.icon.icon = create_icon_image(pct_5h, pct_7d, light)

    def update(self) -> None:
        """Fetch current usage and update the tray icon and tooltip.

        Tracks session usage changes to enable adaptive fast-polling
        when usage is actively increasing.
        """
        self.usage_data = fetch_usage()

        if 'error' in self.usage_data:
            self.icon.icon = create_status_image('C!' if self.usage_data.get('auth_error') else '!', self._light_taskbar)
            self.icon.title = format_tooltip(self.usage_data)
            return

        pct_5h = self.usage_data.get('five_hour', {}).get('utilization', 0) or 0
        pct_7d = self.usage_data.get('seven_day', {}).get('utilization', 0) or 0

        # Notify when quota resets after being nearly exhausted, but only if the other quota isn't blocking usage
        if self._prev_5h is not None and self._prev_5h > 95 and pct_5h < self._prev_5h and pct_7d < 99:
            self.icon.notify(T['notify_reset'], T['notify_reset_title'])
        if self._prev_7d is not None and self._prev_7d > 98 and pct_7d < self._prev_7d and pct_5h < 99:
            self.icon.notify(T['notify_reset'], T['notify_reset_title'])

        # Adaptive polling: speed up when session usage is increasing
        if self._prev_5h is not None and pct_5h > self._prev_5h:
            self._fast_polls_remaining = POLL_FAST_EXTRA + 1
        elif self._fast_polls_remaining > 0:
            self._fast_polls_remaining -= 1
        self._prev_5h = pct_5h
        self._prev_7d = pct_7d

        self.icon.icon = create_icon_image(pct_5h, pct_7d, self._light_taskbar)
        self.icon.title = format_tooltip(self.usage_data)

    def _seconds_until_next_reset(self) -> float | None:
        """Return seconds until the earliest upcoming quota reset, or None."""
        now = datetime.now(timezone.utc)
        earliest = None
        for key in ('five_hour', 'seven_day', 'seven_day_sonnet', 'seven_day_opus'):
            entry = self.usage_data.get(key)
            if not entry or not entry.get('resets_at'):
                continue
            try:
                reset_time = datetime.fromisoformat(entry['resets_at'])
                seconds = (reset_time - now).total_seconds()
                if seconds > 0 and (earliest is None or seconds < earliest):
                    earliest = seconds
            except Exception:
                continue

        return earliest

    def poll_loop(self) -> None:
        """Poll the API in a loop with adaptive intervals.

        Uses faster polling (``POLL_FAST``) when session usage is increasing,
        slower polling (``POLL_INTERVAL``) when idle, and error-rate polling
        (``POLL_ERROR``) after failed requests.  When a quota reset is
        imminent (within ``interval * 1.5``), the next poll is aligned to
        the reset time for immediate post-reset feedback.
        """
        self.profile_data = fetch_profile()
        while self.running:
            self.update()
            if 'error' in self.usage_data:
                interval = POLL_ERROR
            elif self._fast_polls_remaining > 0:
                interval = POLL_FAST
            else:
                interval = POLL_INTERVAL

            # Align next poll to an imminent reset for faster feedback.
            # The +5s buffer guards against minor timing differences
            # (clocks, caches, processing delays). Follow-up uses POLL_FAST
            # regardless of user activity (quota was likely exhausted).
            next_reset = self._seconds_until_next_reset()
            if next_reset is not None and next_reset + 5 <= interval * 1.5:
                interval = max(int(next_reset) + 5, POLL_FAST)
                self._fast_polls_remaining = max(self._fast_polls_remaining, 2)

            for _ in range(interval):
                if not self.running:
                    break
                time.sleep(1)

    def _on_icon_ready(self, icon: Any) -> None:
        """Called by pystray in a separate thread once the tray icon is set up."""
        try:
            icon.visible = True
            if getattr(sys, 'frozen', False):
                sync_autostart_path()
            if not api_headers():
                icon.notify(f"{T['warn_no_token']}\n{T['warn_login']}", T['title'])
            threading.Thread(target=watch_theme_change, args=(self._on_theme_changed,), daemon=True).start()
            self.poll_loop()
        except Exception:
            crash_log(traceback.format_exc())

    def run(self) -> None:
        self.icon.run(setup=self._on_icon_ready)


def crash_log(msg: str) -> None:
    """Show a crash message box (for windowless EXE builds)."""
    ctypes.windll.user32.MessageBoxW(0, msg[:2000], 'Usage Monitor for Claude - Error', 0x10)


if __name__ == '__main__':
    try:
        app = UsageMonitorForClaude()
        app.run()
    except Exception:
        crash_log(traceback.format_exc())

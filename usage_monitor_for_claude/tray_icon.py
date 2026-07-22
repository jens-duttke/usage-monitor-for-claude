"""
Tray Icon
==========

Creates system tray icons and detects the Windows taskbar theme.
"""
from __future__ import annotations

import ctypes
import functools
import os
import winreg
from typing import Callable

from PIL import Image, ImageDraw, ImageFont

from .settings import BAR_GRADIENT_FILL, ICON_DARK, ICON_LIGHT

__all__ = ['load_font', 'taskbar_uses_light_theme', 'watch_theme_change', 'create_icon_image', 'create_status_image', 'draw_status_dot']

# Theme registry
THEME_REG_KEY = r'Software\Microsoft\Windows\CurrentVersion\Themes\Personalize'
THEME_REG_VALUE = 'SystemUsesLightTheme'
REG_NOTIFY_CHANGE_LAST_SET = 0x00000004

TRANSPARENT = (0, 0, 0, 0)

# Icon canvas and bar geometry (pixels)
ICON_SIZE = 64
BAR_HEIGHT = 9
BAR_GAP = 3
MARKER_WIDTH = 4

# Service status dot: small presence-style overlay in the bottom-right corner
STATUS_DOT_RADIUS = 7
STATUS_DOT_RING_WIDTH = 2
STATUS_DOT_COLORS: dict[str, tuple[int, int, int, int]] = {
    'none': (0x30, 0xa4, 0x5c, 255),  # green - operational
    'minor': (0xe8, 0x9a, 0x1e, 255),  # orange - minor incident
    'major': (0xd9, 0x3a, 0x3a, 255),  # red - major incident
    'critical': (0xd9, 0x3a, 0x3a, 255),  # red - critical outage
}
STATUS_DOT_COLOR_UNKNOWN = (0x88, 0x88, 0x88, 255)  # gray - status API unreachable or not yet fetched


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
            try:
                callback()
            except Exception:
                # A transient callback failure (icon re-render, Shell_NotifyIcon
                # during an Explorer restart) must not end theme watching for
                # the rest of the session.
                pass


def create_icon_image(
    pct_top: float, pct_bottom: float, light_taskbar: bool = False,
    *, mode_top: str = 'utilization', mode_bottom: str = 'utilization',
    time_pct_top: float | None = None, time_pct_bottom: float | None = None,
    extra_usage_available: bool = False,
) -> Image.Image:
    """Create tray icon: 'C' letter + two usage bars.

    Parameters
    ----------
    pct_top : float
        Utilization percentage (0-100) for the upper bar.
    pct_bottom : float
        Utilization percentage (0-100) for the lower bar.
    light_taskbar : bool
        Use dark-on-light colors for a light taskbar.
    mode_top : str
        Display mode for the upper bar: ``'utilization'`` (linear fill)
        or ``'overage'`` (fills as usage exceeds the time marker).
    mode_bottom : str
        Display mode for the lower bar.  Same semantics as *mode_top*.
    time_pct_top : float or None
        Elapsed-time percentage for the upper bar.  Draws the reset-time
        marker in ``utilization`` mode; required for ``overage`` mode.
    time_pct_bottom : float or None
        Elapsed-time percentage for the lower bar.  Same semantics as
        *time_pct_top*.
    extra_usage_available : bool
        True if the account has paid extra-usage credits still available.
        When a quota is fully exhausted, this decides whether to show ``$``
        (continuing costs money) or ``✕`` (fully blocked).
    """
    colors = ICON_DARK if light_taskbar else ICON_LIGHT
    fg, fg_half, fg_warn = colors['fg'], colors['fg_half'], colors['fg_warn']

    S = ICON_SIZE
    img = Image.new('RGBA', (S, S), TRANSPARENT)
    draw = ImageDraw.Draw(img)

    # Top glyph: "✕" when any quota exhausted and no extra credits left,
    # "$" when exhausted but paid extra-usage still available,
    # "C" while usage is still zero, otherwise the percentage.
    stroke_width = 0
    any_exhausted = pct_top >= 100 or pct_bottom >= 100
    if any_exhausted and not extra_usage_available:
        text, font = '\u2715', load_font(36, symbol=True)
        stroke_width = 2
    elif any_exhausted:
        text, font = '$', load_font(42)
        stroke_width = 2
    elif pct_top > 0:
        # Clamp to 99: values in [99.5, 100) would round to a three-digit
        # '100' that overflows the canvas and reads as exhausted.
        text, font = f'{min(pct_top, 99):.0f}', load_font(40)
    else:
        text, font = 'C', load_font(42)

    bbox = draw.textbbox((0, 0), text, font=font, stroke_width=stroke_width)
    tw = bbox[2] - bbox[0]
    draw.text(((S - tw) / 2 - bbox[0], -bbox[1]), text, fill=fg, font=font, stroke_width=stroke_width, stroke_fill=fg)

    # Progress bars - full width, flush to bottom
    bar2_y = S - BAR_HEIGHT
    bar1_y = bar2_y - BAR_GAP - BAR_HEIGHT

    _draw_usage_bar(draw, bar1_y, pct_top, mode_top, time_pct_top, fg, fg_half, fg_warn)
    _draw_usage_bar(draw, bar2_y, pct_bottom, mode_bottom, time_pct_bottom, fg, fg_half, fg_warn)

    return img


def _gradient_color(px: int, start_rgb: tuple[int, int, int], end_rgb: tuple[int, int, int]) -> tuple[int, int, int, int]:
    """Return the gradient RGBA color at pixel column *px* interpolating from *start_rgb* to *end_rgb*."""
    t = px / (ICON_SIZE - 1)
    r = start_rgb[0] + int((end_rgb[0] - start_rgb[0]) * t)
    g = start_rgb[1] + int((end_rgb[1] - start_rgb[1]) * t)
    b = start_rgb[2] + int((end_rgb[2] - start_rgb[2]) * t)
    return (r, g, b, 255)


def _draw_gradient_fill(draw: ImageDraw.ImageDraw, y: int, fill_w: int, start_rgb: tuple[int, int, int], end_rgb: tuple[int, int, int]) -> None:
    """Fill the first *fill_w* columns of the bar with a gradient from *start_rgb* to *end_rgb*."""
    for px in range(fill_w):
        draw.line([(px, y), (px, y + BAR_HEIGHT - 1)], fill=_gradient_color(px, start_rgb, end_rgb))


def _fill_bar(draw: ImageDraw.ImageDraw, y: int, fill_w: int, fg: tuple, fg_warn: tuple, *, warn: bool) -> None:
    """Fill the first *fill_w* columns of the bar.

    Uses a continuous *fg*-to-*fg_warn* gradient when ``BAR_GRADIENT_FILL`` is
    enabled in settings; otherwise a solid *fg_warn* fill when *warn* is true
    and a solid *fg* fill otherwise.
    """
    if BAR_GRADIENT_FILL:
        _draw_gradient_fill(draw, y, fill_w, fg, fg_warn)
    else:
        draw.rectangle([0, y, fill_w - 1, y + BAR_HEIGHT - 1], fill=fg_warn if warn else fg)


def _draw_usage_bar(draw: ImageDraw.ImageDraw, y: int, pct: float, mode: str, time_pct: float | None, fg: tuple, fg_half: tuple, fg_warn: tuple) -> None:
    """Draw one full-width usage bar at vertical offset *y*.

    In ``utilization`` mode the bar fills linearly with *pct* and shows a
    reset-time marker in *fg* at the *time_pct* position; the fill switches
    to *fg_warn* (or fades toward it, if ``BAR_GRADIENT_FILL`` is enabled)
    when usage is ahead of the elapsed time or fully exhausted.  In
    ``overage`` mode the bar fills as *pct* exceeds *time_pct* and no marker
    is drawn - elapsed time is already encoded in the fill.
    """
    draw.rectangle([0, y, ICON_SIZE - 1, y + BAR_HEIGHT - 1], fill=fg_half)

    if mode == 'overage' and time_pct is not None:
        if time_pct >= 100:
            # End state for a stale window (elapsed time clamped to 100%,
            # e.g. between a reset and the confirming poll): usage below the
            # limit stayed within budget (empty bar), an exhausted quota
            # keeps the bar full - never the linear utilization fill.
            if pct >= 100:
                draw.rectangle([0, y, ICON_SIZE - 1, y + BAR_HEIGHT - 1], fill=fg)
            return

        overage = max(0.0, pct - time_pct)
        fill_ratio = min(1.0, overage / (100 - time_pct))
        fill_w = max(0, int(ICON_SIZE * fill_ratio))
        if fill_w > 0:
            _fill_bar(draw, y, fill_w, fg, fg_warn, warn=False)
        return

    fill_w = max(0, min(ICON_SIZE, int(ICON_SIZE * pct / 100)))
    if fill_w > 0:
        warn = mode == 'utilization' and (pct >= 100 or (time_pct is not None and pct > time_pct))
        _fill_bar(draw, y, fill_w, fg, fg_warn, warn=warn)

    if mode != 'utilization' or time_pct is None:
        return

    marker_x = min(ICON_SIZE - MARKER_WIDTH, max(0, int(ICON_SIZE * time_pct / 100) - MARKER_WIDTH // 2))
    marker_end = marker_x + MARKER_WIDTH - 1
    draw.rectangle([marker_x, y, marker_end, y + BAR_HEIGHT - 1], fill=fg)


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


def draw_status_dot(img: Image.Image, indicator: str | None, light_taskbar: bool = False) -> None:
    """Overlay a small presence-style status dot in the icon's bottom-right corner (mutates *img* in place).

    Draws a solid ring in the taskbar-contrasting color before the dot
    itself, so the dot stays legible over whatever is drawn underneath
    (the bottom usage bar) - the same cutout technique used by presence
    badges in Discord/Teams. No other pixel of *img* is touched.

    Parameters
    ----------
    img : Image.Image
        Icon image to draw onto, mutated in place.
    indicator : str or None
        Claude status indicator (``'none'``, ``'minor'``, ``'major'`` or
        ``'critical'``). Any other value, including ``None`` (status API
        unreachable or not yet fetched), draws a gray dot.
    light_taskbar : bool
        Use the dark ring color for a light taskbar.
    """
    draw = ImageDraw.Draw(img)
    ring_rgb = (ICON_DARK if light_taskbar else ICON_LIGHT)['fg']
    dot_color = STATUS_DOT_COLORS.get(indicator, STATUS_DOT_COLOR_UNKNOWN)

    outer_r = STATUS_DOT_RADIUS + STATUS_DOT_RING_WIDTH
    cx = cy = ICON_SIZE - 1 - outer_r

    draw.ellipse([cx - outer_r, cy - outer_r, cx + outer_r, cy + outer_r], fill=ring_rgb)
    draw.ellipse([cx - STATUS_DOT_RADIUS, cy - STATUS_DOT_RADIUS, cx + STATUS_DOT_RADIUS, cy + STATUS_DOT_RADIUS], fill=dot_color)

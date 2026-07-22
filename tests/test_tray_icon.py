"""
Tray Icon Tests
================

Unit tests for tray icon rendering and theme detection.
"""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock, call, patch

from PIL import Image, ImageDraw

import usage_monitor_for_claude.tray_icon as tray_icon_mod


class TestWatchThemeChange(unittest.TestCase):
    """Tests for watch_theme_change() - the registry-based theme watcher."""

    @patch('ctypes.windll.advapi32.RegNotifyChangeKeyValue')
    @patch.object(tray_icon_mod, 'winreg')
    def test_callback_exception_does_not_end_watcher(self, mock_winreg, mock_notify):
        """A transient callback failure (e.g. re-render error during an Explorer
        restart) must not end theme watching for the rest of the session."""
        mock_winreg.OpenKey.return_value.__enter__.return_value = 1234
        mock_notify.side_effect = [0, 0, 1]  # two theme changes, then watcher exit

        calls = []

        def callback():
            calls.append(1)
            if len(calls) == 1:
                raise RuntimeError('transient render failure')

        tray_icon_mod.watch_theme_change(callback)

        self.assertEqual(len(calls), 2)

    @patch('ctypes.windll.advapi32.RegNotifyChangeKeyValue')
    @patch.object(tray_icon_mod, 'winreg')
    def test_watcher_exits_when_notify_fails(self, mock_winreg, mock_notify):
        """A failing RegNotifyChangeKeyValue ends the watcher without callbacks."""
        mock_winreg.OpenKey.return_value.__enter__.return_value = 1234
        mock_notify.return_value = 1

        callback = MagicMock()
        tray_icon_mod.watch_theme_change(callback)

        callback.assert_not_called()


class TestOverageBarEndState(unittest.TestCase):
    """Tests for the overage bar when the elapsed time is clamped to 100%."""

    _FG = (255, 255, 255, 255)
    _FG_HALF = (255, 255, 255, 80)
    _FG_WARN = (224, 80, 80, 255)

    def _draw_bar(self, pct, time_pct):
        img = Image.new('RGBA', (64, 64), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        tray_icon_mod._draw_usage_bar(draw, 0, pct, 'overage', time_pct, self._FG, self._FG_HALF, self._FG_WARN)
        return img

    def test_stale_window_below_limit_shows_empty_bar(self):
        """With a stale resets_at the elapsed time clamps to 100%; usage below the
        limit must keep the overage reading (empty bar) instead of jumping to a
        linear utilization fill until the confirming poll arrives."""
        img = self._draw_bar(80.0, 100.0)
        self.assertEqual(img.getpixel((32, 4)), self._FG_HALF)

    def test_stale_window_exhausted_shows_full_bar(self):
        """An exhausted quota keeps a full bar in the stale-window end state."""
        img = self._draw_bar(100.0, 100.0)
        self.assertEqual(img.getpixel((32, 4)), self._FG)

    def test_active_window_overage_fill_unchanged(self):
        """The regular overage fill (time_pct < 100) is unaffected."""
        # 75% used at 50% elapsed: overage 25 of remaining 50 -> half filled.
        img = self._draw_bar(75.0, 50.0)
        self.assertEqual(img.getpixel((16, 4)), self._FG)
        self.assertEqual(img.getpixel((48, 4)), self._FG_HALF)


class TestIconGlyphNearExhaustion(unittest.TestCase):
    """Tests for the percentage glyph just below 100% utilization."""

    def test_99_5_to_99_99_renders_like_99(self):
        """Utilization in [99.5, 100) must not round up to a three-digit '100'
        that overflows the 64 px canvas (and reads as exhausted) - it renders
        exactly like 99%."""
        reference = tray_icon_mod.create_icon_image(99.0, 10.0)
        for pct in (99.5, 99.9, 99.99):
            with self.subTest(pct=pct):
                img = tray_icon_mod.create_icon_image(pct, 10.0)
                self.assertEqual(img.tobytes(), reference.tobytes())

    def test_100_renders_exhausted_glyph(self):
        """At exactly 100% the exhausted glyph replaces the number."""
        img = tray_icon_mod.create_icon_image(100.0, 10.0)
        reference = tray_icon_mod.create_icon_image(99.0, 10.0)
        self.assertNotEqual(img.tobytes(), reference.tobytes())


class TestLoadFont(unittest.TestCase):
    """Tests for load_font()."""

    def setUp(self):
        tray_icon_mod.load_font.cache_clear()

    def tearDown(self):
        tray_icon_mod.load_font.cache_clear()

    @patch.object(tray_icon_mod, 'ImageFont')
    @patch.dict('os.environ', {'WINDIR': r'C:\Windows'})
    def test_loads_arial_bold_for_normal_text(self, mock_image_font):
        """Default call loads Arial Bold font."""
        mock_font = MagicMock()
        mock_image_font.truetype.return_value = mock_font

        result = tray_icon_mod.load_font(42)

        self.assertIs(result, mock_font)
        mock_image_font.truetype.assert_called_once_with(r'C:\Windows\Fonts\arialbd.ttf', 42)

    @patch.object(tray_icon_mod, 'ImageFont')
    @patch.dict('os.environ', {'WINDIR': r'C:\Windows'})
    def test_loads_segoe_symbol_for_symbol_text(self, mock_image_font):
        """symbol=True loads Segoe UI Symbol font."""
        mock_font = MagicMock()
        mock_image_font.truetype.return_value = mock_font

        result = tray_icon_mod.load_font(36, symbol=True)

        self.assertIs(result, mock_font)
        mock_image_font.truetype.assert_called_once_with(r'C:\Windows\Fonts\seguisym.ttf', 36)

    @patch.object(tray_icon_mod, 'ImageFont')
    @patch.dict('os.environ', {'WINDIR': r'C:\Windows'})
    def test_falls_back_to_default_when_all_fail(self, mock_image_font):
        """Falls back to load_default() when no TrueType font found."""
        mock_image_font.truetype.side_effect = OSError
        mock_default = MagicMock()
        mock_image_font.load_default.return_value = mock_default

        result = tray_icon_mod.load_font(42)

        self.assertIs(result, mock_default)
        mock_image_font.load_default.assert_called_once()

    @patch.object(tray_icon_mod, 'ImageFont')
    @patch.dict('os.environ', {'WINDIR': r'C:\Windows'})
    def test_tries_fallback_names_on_failure(self, mock_image_font):
        """Tries alternative font names when first attempt fails."""
        mock_font = MagicMock()
        mock_image_font.truetype.side_effect = [OSError, mock_font]

        result = tray_icon_mod.load_font(42)

        self.assertIs(result, mock_font)
        self.assertEqual(mock_image_font.truetype.call_count, 2)
        mock_image_font.truetype.assert_called_with('arialbd.ttf', 42)

    @patch.object(tray_icon_mod, 'ImageFont')
    @patch.dict('os.environ', {'WINDIR': r'C:\Windows'})
    def test_lru_cache_returns_same_instance(self, mock_image_font):
        """Cached: same size returns same font object without second truetype call."""
        mock_font = MagicMock()
        mock_image_font.truetype.return_value = mock_font

        first = tray_icon_mod.load_font(42)
        second = tray_icon_mod.load_font(42)

        self.assertIs(first, second)
        mock_image_font.truetype.assert_called_once()

    @patch.object(tray_icon_mod, 'ImageFont')
    @patch.dict('os.environ', {'WINDIR': r'C:\Windows'})
    def test_different_sizes_cached_separately(self, mock_image_font):
        """Different sizes produce separate cache entries."""
        mock_image_font.truetype.return_value = MagicMock()

        tray_icon_mod.load_font(36)
        tray_icon_mod.load_font(42)

        self.assertEqual(mock_image_font.truetype.call_count, 2)

    @patch.object(tray_icon_mod, 'ImageFont')
    @patch.dict('os.environ', {}, clear=True)
    def test_uses_default_windir_when_not_set(self, mock_image_font):
        """Falls back to C:\\Windows when WINDIR is not set."""
        mock_font = MagicMock()
        mock_image_font.truetype.return_value = mock_font

        tray_icon_mod.load_font(42)

        mock_image_font.truetype.assert_called_once_with(r'C:\Windows\Fonts\arialbd.ttf', 42)


class TestTaskbarUsesLightTheme(unittest.TestCase):
    """Tests for taskbar_uses_light_theme()."""

    @patch.object(tray_icon_mod, 'winreg')
    def test_returns_true_for_light_theme(self, mock_winreg):
        """Registry value 1 means light theme."""
        mock_key = MagicMock()
        mock_winreg.OpenKey.return_value.__enter__ = MagicMock(return_value=mock_key)
        mock_winreg.OpenKey.return_value.__exit__ = MagicMock(return_value=False)
        mock_winreg.QueryValueEx.return_value = (1, 4)

        self.assertTrue(tray_icon_mod.taskbar_uses_light_theme())

    @patch.object(tray_icon_mod, 'winreg')
    def test_returns_false_for_dark_theme(self, mock_winreg):
        """Registry value 0 means dark theme."""
        mock_key = MagicMock()
        mock_winreg.OpenKey.return_value.__enter__ = MagicMock(return_value=mock_key)
        mock_winreg.OpenKey.return_value.__exit__ = MagicMock(return_value=False)
        mock_winreg.QueryValueEx.return_value = (0, 4)

        self.assertFalse(tray_icon_mod.taskbar_uses_light_theme())

    @patch.object(tray_icon_mod, 'winreg')
    def test_returns_false_on_os_error(self, mock_winreg):
        """OSError (missing key, permissions) defaults to dark."""
        mock_winreg.OpenKey.side_effect = OSError

        self.assertFalse(tray_icon_mod.taskbar_uses_light_theme())

    @patch.object(tray_icon_mod, 'winreg')
    def test_reads_correct_registry_path(self, mock_winreg):
        """Opens the Personalize registry key."""
        mock_winreg.OpenKey.return_value.__enter__ = MagicMock()
        mock_winreg.OpenKey.return_value.__exit__ = MagicMock(return_value=False)
        mock_winreg.QueryValueEx.return_value = (0, 4)

        tray_icon_mod.taskbar_uses_light_theme()

        mock_winreg.OpenKey.assert_called_once_with(
            mock_winreg.HKEY_CURRENT_USER, tray_icon_mod.THEME_REG_KEY,
        )


def _real_font():
    """Return a real PIL font for rendering tests."""
    from PIL import ImageFont

    try:
        return ImageFont.truetype('arial.ttf', 20)
    except OSError:
        return ImageFont.load_default()


class TestCreateIconImage(unittest.TestCase):
    """Tests for create_icon_image()."""

    def setUp(self):
        tray_icon_mod.load_font.cache_clear()

    def tearDown(self):
        tray_icon_mod.load_font.cache_clear()

    def test_returns_64x64_rgba_image(self):
        """Icon is always 64x64 RGBA."""
        img = tray_icon_mod.create_icon_image(0, 0)

        self.assertEqual(img.size, (64, 64))
        self.assertEqual(img.mode, 'RGBA')

    def test_low_usage_renders_without_error(self):
        """Usage <= 50% renders successfully."""
        img = tray_icon_mod.create_icon_image(30, 20)

        self.assertEqual(img.size, (64, 64))

    def test_high_usage_renders_without_error(self):
        """Usage > 50% renders successfully."""
        img = tray_icon_mod.create_icon_image(75, 20)

        self.assertEqual(img.size, (64, 64))

    def test_full_usage_renders_without_error(self):
        """Usage >= 100% renders successfully."""
        img = tray_icon_mod.create_icon_image(100, 20)

        self.assertEqual(img.size, (64, 64))

    def test_dark_and_light_taskbar_produce_different_images(self):
        """Dark vs light taskbar produces different pixel data."""
        img_dark = tray_icon_mod.create_icon_image(50, 50, light_taskbar=False)
        img_light = tray_icon_mod.create_icon_image(50, 50, light_taskbar=True)

        self.assertEqual(img_dark.size, (64, 64))
        self.assertEqual(img_light.size, (64, 64))
        self.assertNotEqual(img_dark.tobytes(), img_light.tobytes())

    def test_zero_usage_no_bar_fill(self):
        """Zero usage has no filled bar pixels beyond the half-tone background."""
        img = tray_icon_mod.create_icon_image(0, 0)

        self.assertEqual(img.size, (64, 64))

    def test_full_bar_fill_at_100_percent(self):
        """100% usage fills the entire bar width."""
        img_full = tray_icon_mod.create_icon_image(100, 100)
        img_zero = tray_icon_mod.create_icon_image(0, 0)

        # The bar area pixels should differ between 0% and 100%
        self.assertNotEqual(img_full.tobytes(), img_zero.tobytes())

    def test_boundary_zero_differs_from_one(self):
        """0% (shows 'C') and 1% (shows percentage) produce different icons."""
        img_zero = tray_icon_mod.create_icon_image(0, 0)
        img_one = tray_icon_mod.create_icon_image(1, 0)

        self.assertNotEqual(img_zero.tobytes(), img_one.tobytes())

    @patch.object(tray_icon_mod, 'load_font')
    def test_zero_usage_calls_font_size_42(self, mock_font):
        """Usage of 0% requests size 42 font for 'C' letter."""
        mock_font.return_value = _real_font()

        tray_icon_mod.create_icon_image(0, 0)

        mock_font.assert_any_call(42)

    @patch.object(tray_icon_mod, 'load_font')
    def test_nonzero_usage_calls_font_size_40(self, mock_font):
        """Any usage > 0% requests size 40 font for percentage."""
        mock_font.return_value = _real_font()

        tray_icon_mod.create_icon_image(30, 20)

        mock_font.assert_any_call(40)

    @patch.object(tray_icon_mod, 'load_font')
    def test_full_usage_calls_symbol_font(self, mock_font):
        """Usage >= 100% requests size 36 symbol font for cross."""
        mock_font.return_value = _real_font()

        tray_icon_mod.create_icon_image(100, 20)

        mock_font.assert_any_call(36, symbol=True)

    @patch.object(tray_icon_mod, 'load_font')
    def test_bottom_bar_at_100_also_triggers_cross(self, mock_font):
        """Bottom bar at 100% triggers the cross glyph even when top bar is low."""
        mock_font.return_value = _real_font()

        tray_icon_mod.create_icon_image(20, 100)

        mock_font.assert_any_call(36, symbol=True)

    @patch.object(tray_icon_mod, 'load_font')
    def test_extra_usage_available_shows_dollar_when_exhausted(self, mock_font):
        """When a quota is exhausted but paid extra-usage is available, show '$' instead of '✕'."""
        mock_font.return_value = _real_font()

        tray_icon_mod.create_icon_image(100, 20, extra_usage_available=True)

        # Dollar sign uses the regular size-42 font, not the symbol font
        mock_font.assert_any_call(42)
        self.assertNotIn(call(36, symbol=True), mock_font.call_args_list)

    @patch.object(tray_icon_mod, 'load_font')
    def test_extra_usage_available_irrelevant_when_no_quota_exhausted(self, mock_font):
        """extra_usage_available has no effect while every quota is below 100%."""
        mock_font.return_value = _real_font()

        tray_icon_mod.create_icon_image(75, 20, extra_usage_available=True)

        # Still shows the percentage, not '$'
        mock_font.assert_any_call(40)

    def test_dollar_and_cross_states_produce_different_images(self):
        """'$' (extra usage available) and '✕' (fully blocked) render differently."""
        img_cross = tray_icon_mod.create_icon_image(100, 20, extra_usage_available=False)
        img_dollar = tray_icon_mod.create_icon_image(100, 20, extra_usage_available=True)

        self.assertNotEqual(img_cross.tobytes(), img_dollar.tobytes())


class TestCreateIconImageOverageMode(unittest.TestCase):
    """Tests for create_icon_image() overage-mode bars.

    Overage mode shows how far usage has gone into the over-budget zone.
    The bar is empty when pct <= time_pct (on pace or ahead), and full when
    pct reaches 100%. Formula: fill_ratio = clamp((pct - time_pct) / (100 - time_pct), 0, 1)
    """

    def setUp(self):
        tray_icon_mod.load_font.cache_clear()

    def tearDown(self):
        tray_icon_mod.load_font.cache_clear()

    def test_overage_mode_returns_64x64_rgba(self):
        """Overage mode still produces a 64x64 RGBA image."""
        img = tray_icon_mod.create_icon_image(80, 80, mode_top='overage', mode_bottom='overage', time_pct_top=60, time_pct_bottom=60)

        self.assertEqual(img.size, (64, 64))
        self.assertEqual(img.mode, 'RGBA')

    def test_overage_mode_time_pct_at_100_keeps_overage_reading(self):
        """time_pct=100 (stale window right after a reset) keeps the overage
        reading - usage below the limit stays an empty bar instead of jumping
        to the linear utilization fill until the confirming poll arrives."""
        img_end_state = tray_icon_mod.create_icon_image(50, 50, mode_top='overage', mode_bottom='overage', time_pct_top=100, time_pct_bottom=100)
        img_on_pace = tray_icon_mod.create_icon_image(50, 50, mode_top='overage', mode_bottom='overage', time_pct_top=50, time_pct_bottom=50)

        self.assertEqual(img_end_state.tobytes(), img_on_pace.tobytes())

    def test_on_pace_produces_empty_bar(self):
        """Usage exactly at time_pct means on pace - bar pixels are not fully opaque (no fill)."""
        # pct=60, time_pct=60 -> overage=0 -> fill_ratio=0 -> no fill
        img = tray_icon_mod.create_icon_image(60, 60, mode_top='overage', mode_bottom='overage', time_pct_top=60, time_pct_bottom=60)

        S = 64
        bar_h = 9
        gap = 3
        bar2_y = S - bar_h
        bar1_y = bar2_y - gap - bar_h
        pixels = img.load()
        for bar_y in (bar1_y, bar2_y):
            mid_y = bar_y + bar_h // 2
            # No pixel in the bar should be fully opaque (fill_w=0)
            self.assertNotEqual(pixels[0, mid_y][3], 255, f'Expected no fill at x=0, y={mid_y}')

    def test_below_pace_produces_empty_bar(self):
        """Usage below time_pct (ahead of schedule) also produces an empty bar."""
        # pct=40 < time_pct=60 -> overage=0 -> no fill; same result as pct=60
        S = 64
        bar_h = 9
        gap = 3
        bar2_y = S - bar_h
        bar1_y = bar2_y - gap - bar_h

        img_ahead = tray_icon_mod.create_icon_image(40, 40, mode_top='overage', mode_bottom='overage', time_pct_top=60, time_pct_bottom=60)
        pixels = img_ahead.load()
        for bar_y in (bar1_y, bar2_y):
            mid_y = bar_y + bar_h // 2
            self.assertNotEqual(pixels[0, mid_y][3], 255, f'Expected no fill at x=0, y={mid_y}')

    def test_half_fill_at_midpoint_of_over_budget_range(self):
        """pct halfway between time_pct and 100% produces a half-filled bar."""
        # time_pct=60, pct=80 -> (80-60)/(100-60) = 0.5 -> fill_w = 32px
        img = tray_icon_mod.create_icon_image(80, 80, mode_top='overage', mode_bottom='overage', time_pct_top=60, time_pct_bottom=60)

        S = 64
        bar_h = 9
        gap = 3
        bar2_y = S - bar_h
        bar1_y = bar2_y - gap - bar_h
        pixels = img.load()
        for bar_y in (bar1_y, bar2_y):
            mid_y = bar_y + bar_h // 2
            # x=31 (last pixel of left half) should be filled (fg, alpha=255)
            self.assertEqual(pixels[31, mid_y][3], 255, f'Expected filled pixel at x=31, y={mid_y}')
            # x=32 (first pixel of right half) should not be filled (bg, alpha<255)
            self.assertNotEqual(pixels[32, mid_y][3], 255, f'Expected unfilled pixel at x=32, y={mid_y}')

    def test_full_bar_at_100_percent_usage(self):
        """100% usage fills the entire bar regardless of time_pct."""
        # time_pct=60, pct=100 -> (100-60)/(100-60) = 1.0 -> full bar
        img = tray_icon_mod.create_icon_image(100, 100, mode_top='overage', mode_bottom='overage', time_pct_top=60, time_pct_bottom=60)

        S = 64
        bar_h = 9
        gap = 3
        bar2_y = S - bar_h
        bar1_y = bar2_y - gap - bar_h
        pixels = img.load()
        for bar_y in (bar1_y, bar2_y):
            mid_y = bar_y + bar_h // 2
            self.assertEqual(pixels[S - 1, mid_y][3], 255, f'Expected fully filled bar at y={mid_y}')

    def test_mixed_modes_top_overage_bottom_utilization(self):
        """Top bar in overage mode, bottom bar in utilization mode produces valid image."""
        img = tray_icon_mod.create_icon_image(80, 50, mode_top='overage', mode_bottom='utilization', time_pct_top=60, time_pct_bottom=None)

        self.assertEqual(img.size, (64, 64))
        self.assertEqual(img.mode, 'RGBA')


class TestCreateIconImageTimeMarker(unittest.TestCase):
    """Tests for the reset-time marker and warning fill on utilization-mode bars.

    The marker is a MARKER_WIDTH-wide vertical line in the icon foreground
    color, centered at the elapsed-time position, clamped to the icon bounds,
    and drawn only in utilization mode. The bar fill switches to the warning
    color (fg_warn) when usage is ahead of the elapsed time or fully
    exhausted, mirroring the popup's warning fill.
    """

    def setUp(self):
        tray_icon_mod.load_font.cache_clear()

    def tearDown(self):
        tray_icon_mod.load_font.cache_clear()

    @staticmethod
    def _bar_mid_rows():
        """Return the vertical center row of each bar."""
        bar2_y = tray_icon_mod.ICON_SIZE - tray_icon_mod.BAR_HEIGHT
        bar1_y = bar2_y - tray_icon_mod.BAR_GAP - tray_icon_mod.BAR_HEIGHT
        return (bar1_y + tray_icon_mod.BAR_HEIGHT // 2, bar2_y + tray_icon_mod.BAR_HEIGHT // 2)

    def test_marker_solid_on_unfilled_track(self):
        """Marker ahead of the fill is drawn in solid fg on the track."""
        # pct=20 -> fill ends at x=12; time_pct=50 -> marker at x=30..33
        img = tray_icon_mod.create_icon_image(20, 10, time_pct_top=50, time_pct_bottom=50)

        fg = tray_icon_mod.ICON_LIGHT['fg']
        pixels = img.load()
        for mid_y in self._bar_mid_rows():
            self.assertEqual(pixels[32, mid_y], fg, f'Expected solid marker pixel at x=32, y={mid_y}')

    def test_fill_plain_when_on_pace(self):
        """Usage at or below the elapsed time keeps the plain fg fill."""
        # pct=20 <= time_pct=50 -> no warning
        img = tray_icon_mod.create_icon_image(20, 20, time_pct_top=50, time_pct_bottom=50)

        fg = tray_icon_mod.ICON_LIGHT['fg']
        pixels = img.load()
        for mid_y in self._bar_mid_rows():
            self.assertEqual(pixels[5, mid_y], fg, f'Expected plain fill pixel at x=5, y={mid_y}')

    def test_fill_warns_when_usage_ahead(self):
        """Usage ahead of the elapsed time switches the fill to fg_warn, marker stays fg."""
        # pct=70 -> fill ends at x=43; time_pct=40 -> marker at x=23..26 inside the fill
        img = tray_icon_mod.create_icon_image(70, 70, time_pct_top=40, time_pct_bottom=40)

        fg = tray_icon_mod.ICON_LIGHT['fg']
        fg_half = tray_icon_mod.ICON_LIGHT['fg_half']
        fg_warn = tray_icon_mod.ICON_LIGHT['fg_warn']
        pixels = img.load()
        for mid_y in self._bar_mid_rows():
            self.assertEqual(pixels[5, mid_y], fg_warn, f'Expected warn fill pixel at x=5, y={mid_y}')
            self.assertEqual(pixels[24, mid_y], fg, f'Expected marker pixel inside fill at x=24, y={mid_y}')
            self.assertEqual(pixels[35, mid_y], fg_warn, f'Expected warn fill pixel at x=35, y={mid_y}')
            self.assertEqual(pixels[50, mid_y], fg_half, f'Expected track pixel at x=50, y={mid_y}')

    def test_fill_warns_at_full_usage(self):
        """100% usage warns even when the elapsed time is also at 100%."""
        # pct=100, time_pct=100 -> warn via the >=100 rule; marker at x=60..63
        img = tray_icon_mod.create_icon_image(100, 100, time_pct_top=100, time_pct_bottom=100)

        fg = tray_icon_mod.ICON_LIGHT['fg']
        fg_warn = tray_icon_mod.ICON_LIGHT['fg_warn']
        pixels = img.load()
        for mid_y in self._bar_mid_rows():
            self.assertEqual(pixels[5, mid_y], fg_warn, f'Expected warn fill pixel at x=5, y={mid_y}')
            self.assertEqual(pixels[63, mid_y], fg, f'Expected marker pixel at x=63, y={mid_y}')

    def test_fill_warns_at_full_usage_without_time_pct(self):
        """100% usage warns even when no elapsed time is known (no marker drawn)."""
        img = tray_icon_mod.create_icon_image(100, 100)

        fg = tray_icon_mod.ICON_LIGHT['fg']
        fg_warn = tray_icon_mod.ICON_LIGHT['fg_warn']
        pixels = img.load()
        for mid_y in self._bar_mid_rows():
            self.assertEqual(pixels[5, mid_y], fg_warn, f'Expected warn fill pixel at x=5, y={mid_y}')
            for x in range(64):
                self.assertNotEqual(pixels[x, mid_y], fg, f'Unexpected marker pixel at x={x}, y={mid_y}')

    def test_marker_at_fill_edge_stays_solid(self):
        """Usage exactly at the elapsed time keeps a plain fill with a solid fg marker."""
        # pct=50 -> fill ends at x=32; time_pct=50 -> marker at x=30..33; no warning (strictly greater)
        img = tray_icon_mod.create_icon_image(50, 50, time_pct_top=50, time_pct_bottom=50)

        fg = tray_icon_mod.ICON_LIGHT['fg']
        pixels = img.load()
        for mid_y in self._bar_mid_rows():
            self.assertEqual(pixels[5, mid_y], fg, f'Expected plain fill pixel at x=5, y={mid_y}')
            for x in range(30, 34):
                self.assertEqual(pixels[x, mid_y], fg, f'Expected solid marker pixel at x={x}, y={mid_y}')

    def test_no_marker_without_time_pct(self):
        """time_pct=None leaves the unfilled track translucent everywhere."""
        # pct=20 -> fill ends at x=12; everything beyond must stay fg_half
        img = tray_icon_mod.create_icon_image(20, 10)

        pixels = img.load()
        for mid_y in self._bar_mid_rows():
            for x in range(13, 64):
                self.assertNotEqual(pixels[x, mid_y][3], 255, f'Unexpected solid pixel at x={x}, y={mid_y}')

    def test_marker_clamped_at_period_start(self):
        """time_pct=0 keeps the marker inside the left icon edge."""
        img = tray_icon_mod.create_icon_image(0, 0, time_pct_top=0, time_pct_bottom=0)

        fg = tray_icon_mod.ICON_LIGHT['fg']
        pixels = img.load()
        for mid_y in self._bar_mid_rows():
            self.assertEqual(pixels[0, mid_y], fg, f'Expected marker pixel at x=0, y={mid_y}')

    def test_marker_clamped_at_period_end(self):
        """time_pct=100 keeps the marker inside the right icon edge."""
        img = tray_icon_mod.create_icon_image(0, 0, time_pct_top=100, time_pct_bottom=100)

        fg = tray_icon_mod.ICON_LIGHT['fg']
        pixels = img.load()
        for mid_y in self._bar_mid_rows():
            self.assertEqual(pixels[63, mid_y], fg, f'Expected marker pixel at x=63, y={mid_y}')

    def test_overage_mode_draws_no_marker_and_no_warn(self):
        """Overage mode encodes pace in the fill itself - no marker, no warning color."""
        # pct=80, time_pct=50 -> overage fill ends at x=38; a marker would sit at x=30..33
        img = tray_icon_mod.create_icon_image(80, 80, mode_top='overage', mode_bottom='overage', time_pct_top=50, time_pct_bottom=50)

        fg = tray_icon_mod.ICON_LIGHT['fg']
        pixels = img.load()
        for mid_y in self._bar_mid_rows():
            for x in range(30, 34):
                self.assertEqual(pixels[x, mid_y], fg, f'Expected plain fill pixel at x={x}, y={mid_y}')

    def test_marker_uses_light_taskbar_palette(self):
        """Light taskbar draws the marker with the ICON_DARK palette."""
        img = tray_icon_mod.create_icon_image(20, 10, light_taskbar=True, time_pct_top=50, time_pct_bottom=50)

        fg = tray_icon_mod.ICON_DARK['fg']
        pixels = img.load()
        for mid_y in self._bar_mid_rows():
            self.assertEqual(pixels[32, mid_y], fg, f'Expected marker pixel at x=32, y={mid_y}')

    def test_fill_warns_on_light_taskbar(self):
        """Light taskbar uses the ICON_DARK palette: warn fill with the fg marker on top."""
        # pct=100 -> full fill in fg_warn; time_pct=50 -> marker at x=30..33 in fg
        img = tray_icon_mod.create_icon_image(100, 100, light_taskbar=True, time_pct_top=50, time_pct_bottom=50)

        fg = tray_icon_mod.ICON_DARK['fg']
        fg_warn = tray_icon_mod.ICON_DARK['fg_warn']
        pixels = img.load()
        for mid_y in self._bar_mid_rows():
            self.assertEqual(pixels[32, mid_y], fg, f'Expected marker pixel at x=32, y={mid_y}')
            self.assertEqual(pixels[5, mid_y], fg_warn, f'Expected warn fill pixel at x=5, y={mid_y}')


class TestCreateIconImageGradientFill(unittest.TestCase):
    """Tests for create_icon_image() with BAR_GRADIENT_FILL enabled."""

    def setUp(self):
        tray_icon_mod.load_font.cache_clear()
        patcher = patch('usage_monitor_for_claude.tray_icon.BAR_GRADIENT_FILL', True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        tray_icon_mod.load_font.cache_clear()

    @staticmethod
    def _bar_mid_rows():
        bar2_y = tray_icon_mod.ICON_SIZE - tray_icon_mod.BAR_HEIGHT
        bar1_y = bar2_y - tray_icon_mod.BAR_GAP - tray_icon_mod.BAR_HEIGHT
        return (bar1_y + tray_icon_mod.BAR_HEIGHT // 2, bar2_y + tray_icon_mod.BAR_HEIGHT // 2)

    def test_fill_uses_gradient_between_fg_and_fg_warn(self):
        """With the setting enabled, the fill fades from fg to fg_warn instead of switching abruptly."""
        img = tray_icon_mod.create_icon_image(100, 100, time_pct_top=100, time_pct_bottom=100)

        fg, fg_warn = tray_icon_mod.ICON_LIGHT['fg'], tray_icon_mod.ICON_LIGHT['fg_warn']
        pixels = img.load()
        for mid_y in self._bar_mid_rows():
            self.assertEqual(pixels[5, mid_y], tray_icon_mod._gradient_color(5, fg, fg_warn), f'Expected gradient fill pixel at x=5, y={mid_y}')

    def test_overage_mode_also_uses_gradient(self):
        """Overage mode's fill also fades from fg to fg_warn when the setting is enabled."""
        img = tray_icon_mod.create_icon_image(80, 80, mode_top='overage', mode_bottom='overage', time_pct_top=50, time_pct_bottom=50)

        fg, fg_warn = tray_icon_mod.ICON_LIGHT['fg'], tray_icon_mod.ICON_LIGHT['fg_warn']
        pixels = img.load()
        for mid_y in self._bar_mid_rows():
            for x in range(30, 34):
                self.assertEqual(pixels[x, mid_y], tray_icon_mod._gradient_color(x, fg, fg_warn), f'Expected gradient fill pixel at x={x}, y={mid_y}')


class TestCreateStatusImage(unittest.TestCase):
    """Tests for create_status_image()."""

    def setUp(self):
        tray_icon_mod.load_font.cache_clear()

    def tearDown(self):
        tray_icon_mod.load_font.cache_clear()

    def test_returns_64x64_rgba_image(self):
        """Status icon is always 64x64 RGBA."""
        img = tray_icon_mod.create_status_image('!')

        self.assertEqual(img.size, (64, 64))
        self.assertEqual(img.mode, 'RGBA')

    @patch.object(tray_icon_mod, 'load_font')
    def test_uses_size_46_font(self, mock_font):
        """Status text uses size 46 font."""
        mock_font.return_value = _real_font()

        tray_icon_mod.create_status_image('?')

        mock_font.assert_called_with(46)

    def test_light_taskbar_variant(self):
        """Light taskbar produces a valid image."""
        img = tray_icon_mod.create_status_image('!', light_taskbar=True)

        self.assertEqual(img.size, (64, 64))


class TestDrawStatusDot(unittest.TestCase):
    """Tests for draw_status_dot()."""

    def _blank_image(self):
        return Image.new('RGBA', (tray_icon_mod.ICON_SIZE, tray_icon_mod.ICON_SIZE), tray_icon_mod.TRANSPARENT)

    def _center(self):
        outer_r = tray_icon_mod.STATUS_DOT_RADIUS + tray_icon_mod.STATUS_DOT_RING_WIDTH
        c = tray_icon_mod.ICON_SIZE - 1 - outer_r
        return c, c

    def test_none_indicator_draws_green_dot(self):
        """'none' indicator draws the green dot color at the center."""
        img = self._blank_image()
        tray_icon_mod.draw_status_dot(img, 'none')

        cx, cy = self._center()
        self.assertEqual(img.load()[cx, cy], tray_icon_mod.STATUS_DOT_COLORS['none'])

    def test_minor_indicator_draws_orange_dot(self):
        """'minor' indicator draws the orange dot color at the center."""
        img = self._blank_image()
        tray_icon_mod.draw_status_dot(img, 'minor')

        cx, cy = self._center()
        self.assertEqual(img.load()[cx, cy], tray_icon_mod.STATUS_DOT_COLORS['minor'])

    def test_major_indicator_draws_red_dot(self):
        """'major' indicator draws the red dot color at the center."""
        img = self._blank_image()
        tray_icon_mod.draw_status_dot(img, 'major')

        cx, cy = self._center()
        self.assertEqual(img.load()[cx, cy], tray_icon_mod.STATUS_DOT_COLORS['major'])

    def test_critical_indicator_draws_red_dot(self):
        """'critical' indicator draws the same red as 'major'."""
        img = self._blank_image()
        tray_icon_mod.draw_status_dot(img, 'critical')

        cx, cy = self._center()
        self.assertEqual(img.load()[cx, cy], tray_icon_mod.STATUS_DOT_COLORS['critical'])
        self.assertEqual(tray_icon_mod.STATUS_DOT_COLORS['critical'], tray_icon_mod.STATUS_DOT_COLORS['major'])

    def test_none_value_draws_gray_dot(self):
        """indicator=None (status API unreachable or not yet fetched) draws gray."""
        img = self._blank_image()
        tray_icon_mod.draw_status_dot(img, None)

        cx, cy = self._center()
        self.assertEqual(img.load()[cx, cy], tray_icon_mod.STATUS_DOT_COLOR_UNKNOWN)

    def test_unrecognized_indicator_draws_gray_dot(self):
        """Any unrecognized indicator string falls back to gray."""
        img = self._blank_image()
        tray_icon_mod.draw_status_dot(img, 'some_future_value')

        cx, cy = self._center()
        self.assertEqual(img.load()[cx, cy], tray_icon_mod.STATUS_DOT_COLOR_UNKNOWN)

    def test_ring_uses_dark_taskbar_contrast_color(self):
        """Default (dark taskbar) draws the ring in white."""
        img = self._blank_image()
        tray_icon_mod.draw_status_dot(img, 'none', light_taskbar=False)

        cx, cy = self._center()
        ring_pixel = img.load()[cx, cy - tray_icon_mod.STATUS_DOT_RADIUS - 1]
        self.assertEqual(ring_pixel, tray_icon_mod.ICON_LIGHT['fg'])

    def test_ring_uses_light_taskbar_contrast_color(self):
        """light_taskbar=True draws the ring in black."""
        img = self._blank_image()
        tray_icon_mod.draw_status_dot(img, 'none', light_taskbar=True)

        cx, cy = self._center()
        ring_pixel = img.load()[cx, cy - tray_icon_mod.STATUS_DOT_RADIUS - 1]
        self.assertEqual(ring_pixel, tray_icon_mod.ICON_DARK['fg'])

    def test_dot_sits_in_bottom_right_corner(self):
        """The dot overlay does not touch pixels away from the bottom-right corner."""
        img = self._blank_image()
        tray_icon_mod.draw_status_dot(img, 'major')

        self.assertEqual(img.load()[0, 0], tray_icon_mod.TRANSPARENT)
        self.assertEqual(img.load()[0, tray_icon_mod.ICON_SIZE - 1], tray_icon_mod.TRANSPARENT)
        self.assertEqual(img.load()[tray_icon_mod.ICON_SIZE - 1, 0], tray_icon_mod.TRANSPARENT)


if __name__ == '__main__':
    unittest.main()

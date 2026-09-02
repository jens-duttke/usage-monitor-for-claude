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


def setUpModule():
    # Pin the default icon style so a local usage-monitor-settings.json with
    # 'icon_style' cannot flip the classic-style rendering tests.
    patcher = patch.object(tray_icon_mod, 'ICON_STYLE', 'number+bars')
    patcher.start()
    unittest.addModuleCleanup(patcher.stop)


def _real_font():
    """Return a real PIL font for rendering tests."""
    from PIL import ImageFont

    try:
        return ImageFont.truetype('arial.ttf', 20)
    except OSError:
        return ImageFont.load_default()


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


class TestCreateIconImageNumbersStyle(unittest.TestCase):
    """Tests for create_icon_image() with the 'numbers' icon style.

    The style replaces the big-number-plus-bars layout with two stacked
    percentage rows: row 1 shows pct_top, row 2 shows pct_bottom.  Each row
    applies the classic glyph rules per row (✕/$ when exhausted, clamp to
    99) and is always drawn in fg, like the classic glyph.
    """

    def setUp(self):
        tray_icon_mod.load_font.cache_clear()
        patcher = patch.object(tray_icon_mod, 'ICON_STYLE', 'numbers')
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        tray_icon_mod.load_font.cache_clear()

    @staticmethod
    def _row_ranges():
        """Return the y ranges of the top and bottom number rows."""
        row_h = tray_icon_mod.NUMBER_ROW_HEIGHT
        return (range(0, row_h), range(row_h, 2 * row_h))

    @staticmethod
    def _region_has_color(img, y_range, color):
        """Return True if any pixel in the given rows matches *color* exactly."""
        pixels = img.load()
        for y in y_range:
            for x in range(tray_icon_mod.ICON_SIZE):
                if pixels[x, y] == color:
                    return True
        return False

    def test_returns_64x64_rgba_image(self):
        """Numbers style still produces a 64x64 RGBA image."""
        img = tray_icon_mod.create_icon_image(47, 82)

        self.assertEqual(img.size, (64, 64))
        self.assertEqual(img.mode, 'RGBA')

    def test_no_bar_track_drawn(self):
        """The bar zones stay transparent - no fg_half track is drawn."""
        img = tray_icon_mod.create_icon_image(50, 50)

        pixels = img.load()
        for y in (48, 59):
            self.assertEqual(pixels[0, y][3], 0, f'Expected transparent pixel at x=0, y={y}')

    @patch.object(tray_icon_mod, 'load_font')
    def test_rows_use_font_40(self, mock_font):
        """Both rows request the size 40 digit font - the same size as the classic single number."""
        mock_font.return_value = _real_font()

        tray_icon_mod.create_icon_image(30, 20)

        mock_font.assert_any_call(40)

    @patch.object(tray_icon_mod, 'load_font')
    def test_both_rows_zero_shows_single_c(self, mock_font):
        """Both fields at 0% collapse to the single idle 'C' (size 42)."""
        mock_font.return_value = _real_font()

        tray_icon_mod.create_icon_image(0, 0)

        mock_font.assert_any_call(42)
        self.assertNotIn(call(40), mock_font.call_args_list)

    @patch.object(tray_icon_mod, 'load_font')
    def test_zero_row_beside_nonzero_shows_zero_digit(self, mock_font):
        """A single zero row renders '0' - only both-zero collapses to 'C'."""
        mock_font.return_value = _real_font()

        tray_icon_mod.create_icon_image(0, 50)

        mock_font.assert_any_call(40)
        self.assertNotIn(call(42), mock_font.call_args_list)

    @patch.object(tray_icon_mod, 'load_font')
    def test_fractional_usage_shows_rows_not_idle_c(self, mock_font):
        """Usage in (0, 0.5) renders two '0' rows - only exactly zero collapses to 'C'."""
        mock_font.return_value = _real_font()

        tray_icon_mod.create_icon_image(0.3, 0.3)

        mock_font.assert_any_call(40)
        self.assertNotIn(call(42), mock_font.call_args_list)

    @patch.object(tray_icon_mod, 'load_font')
    def test_exhausted_row_uses_symbol_font(self, mock_font):
        """An exhausted row without extra credits requests the size 34 symbol font for '✕'."""
        mock_font.return_value = _real_font()

        tray_icon_mod.create_icon_image(100, 20)

        mock_font.assert_any_call(34, symbol=True)

    @patch.object(tray_icon_mod, 'load_font')
    def test_exhausted_row_with_extra_usage_shows_dollar(self, mock_font):
        """With paid extra usage available the exhausted row shows '$' instead of '✕'."""
        mock_font.return_value = _real_font()

        tray_icon_mod.create_icon_image(100, 20, extra_usage_available=True)

        mock_font.assert_any_call(32)
        self.assertNotIn(call(34, symbol=True), mock_font.call_args_list)

    @patch.object(tray_icon_mod, 'load_font')
    def test_both_rows_exhausted_shows_single_large_cross(self, mock_font):
        """Both quotas exhausted collapse to one full-size '✕' instead of two half-size ones."""
        mock_font.return_value = _real_font()

        tray_icon_mod.create_icon_image(100, 100)

        mock_font.assert_any_call(36, symbol=True)
        self.assertNotIn(call(34, symbol=True), mock_font.call_args_list)

    @patch.object(tray_icon_mod, 'load_font')
    def test_both_rows_exhausted_with_extra_usage_shows_single_large_dollar(self, mock_font):
        """Both quotas exhausted with extra usage collapse to one full-size '$'."""
        mock_font.return_value = _real_font()

        tray_icon_mod.create_icon_image(100, 100, extra_usage_available=True)

        mock_font.assert_any_call(42)
        self.assertNotIn(call(32), mock_font.call_args_list)

    def test_top_row_unaffected_by_bottom_exhaustion(self):
        """Exhaustion applies per row - the top row keeps its number when only the bottom is exhausted."""
        img_exhausted = tray_icon_mod.create_icon_image(20, 100)
        img_normal = tray_icon_mod.create_icon_image(20, 5)

        row_h = tray_icon_mod.NUMBER_ROW_HEIGHT
        top_exhausted = img_exhausted.crop((0, 0, 64, row_h)).tobytes()
        top_normal = img_normal.crop((0, 0, 64, row_h)).tobytes()
        self.assertEqual(top_exhausted, top_normal)

    def test_rows_stay_fg_when_ahead_of_time(self):
        """Rows are always drawn in fg - being ahead of the elapsed time does not recolor them."""
        # top: 70% used at 40% elapsed - would warn on a bar, but not here
        img = tray_icon_mod.create_icon_image(70, 20, time_pct_top=40, time_pct_bottom=40)

        fg = tray_icon_mod.ICON_LIGHT['fg']
        fg_warn = tray_icon_mod.ICON_LIGHT['fg_warn']
        top_rows, bottom_rows = self._row_ranges()
        self.assertTrue(self._region_has_color(img, top_rows, fg), 'Expected fg digits in the top row')
        self.assertTrue(self._region_has_color(img, bottom_rows, fg), 'Expected fg digits in the bottom row')
        self.assertFalse(self._region_has_color(img, range(0, 64), fg_warn), 'Unexpected fg_warn pixels in numbers style')

    def test_exhausted_glyph_drawn_in_fg(self):
        """The exhausted '✕' is drawn in fg, matching the classic glyph."""
        img = tray_icon_mod.create_icon_image(100, 20)

        fg = tray_icon_mod.ICON_LIGHT['fg']
        fg_warn = tray_icon_mod.ICON_LIGHT['fg_warn']
        top_rows, _bottom_rows = self._row_ranges()
        self.assertTrue(self._region_has_color(img, top_rows, fg), 'Expected fg glyph in the exhausted top row')
        self.assertFalse(self._region_has_color(img, range(0, 64), fg_warn), 'Unexpected fg_warn pixels in numbers style')

    def test_time_pct_has_no_effect(self):
        """Elapsed-time values do not change the numbers-style rendering."""
        img_with_time = tray_icon_mod.create_icon_image(70, 20, time_pct_top=40, time_pct_bottom=40)
        img_without_time = tray_icon_mod.create_icon_image(70, 20)

        self.assertEqual(img_with_time.tobytes(), img_without_time.tobytes())

    def test_99_5_renders_like_99_per_row(self):
        """Utilization in [99.5, 100) clamps to '99' in both rows."""
        reference_top = tray_icon_mod.create_icon_image(99.0, 10.0)
        reference_bottom = tray_icon_mod.create_icon_image(10.0, 99.0)
        for pct in (99.5, 99.9, 99.99):
            with self.subTest(pct=pct):
                self.assertEqual(tray_icon_mod.create_icon_image(pct, 10.0).tobytes(), reference_top.tobytes())
                self.assertEqual(tray_icon_mod.create_icon_image(10.0, pct).tobytes(), reference_bottom.tobytes())

    def test_overage_mode_suffix_ignored(self):
        """The overage bar mode has no effect in numbers style."""
        img_overage = tray_icon_mod.create_icon_image(70, 20, mode_top='overage', time_pct_top=40, time_pct_bottom=40)
        img_plain = tray_icon_mod.create_icon_image(70, 20, time_pct_top=40, time_pct_bottom=40)

        self.assertEqual(img_overage.tobytes(), img_plain.tobytes())

    def test_light_taskbar_uses_dark_palette(self):
        """Light taskbar draws the digits with the ICON_DARK palette."""
        img = tray_icon_mod.create_icon_image(50, 50, light_taskbar=True)

        fg = tray_icon_mod.ICON_DARK['fg']
        top_rows, bottom_rows = self._row_ranges()
        self.assertTrue(self._region_has_color(img, top_rows, fg), 'Expected ICON_DARK fg digits in the top row')
        self.assertTrue(self._region_has_color(img, bottom_rows, fg), 'Expected ICON_DARK fg digits in the bottom row')


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


if __name__ == '__main__':
    unittest.main()

# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- The detail popup can now be pinned open and moved while pinned, so usage details stay visible during long Claude Code sessions (thanks to [@nmxi](https://github.com/nmxi) for the contribution)
- [New `compact_hide` setting](https://github.com/jens-duttke/usage-monitor-for-claude/issues/55) shrinks the pinned popup to a compact view by hiding chosen sections (account, extra usage, Claude Code versions, status footer) and individual usage bars while it is pinned, so you can keep just the bars you care about on screen; when only the usage bars remain, the "Usage" heading is dropped as well
- Reset times now follow your Windows clock format automatically, showing 24-hour (14:30) or 12-hour (2:30 PM) without any setup; override with the `time_format` setting (thanks to [@rohitjalan142](https://github.com/rohitjalan142) for the contribution)

### Fixed

- [The status footer no longer cuts off text in several languages](https://github.com/jens-duttke/usage-monitor-for-claude/issues/53) - the "next update" line was too long to fit the popup width in Spanish, French, Italian, Portuguese, Ukrainian, and Indonesian and got truncated; the affected phrases are now shorter so the full status fits on one line, and a long error message now shows in full on hover

[Show all code changes](https://github.com/jens-duttke/usage-monitor-for-claude/compare/v1.16.0...HEAD)

## [1.16.0] - 2026-06-13

### Added

- Tray icon bars now mirror the detail popup's pace cues: each bar in `utilization` mode shows a thin marker at the elapsed-time position of the quota period, and the bar fill turns red once usage moves ahead of the elapsed time (or reaches 100%), so you can tell at a glance whether you are ahead of or behind the clock without opening the popup. A new `fg_warn` color in the `icon_light`/`icon_dark` settings controls the warning fill (thanks to [@timyjsong](https://github.com/timyjsong) for the contribution)
- The five-hour session bar in the detail popup is now subdivided into five equal hour sections by subtle dividers, matching the day dividers on the weekly bars, so you can gauge your position within the session window at a glance (thanks to [@timyjsong](https://github.com/timyjsong) for the contribution)

### Fixed

- [Profile requests no longer ignore the rate-limit backoff](https://github.com/jens-duttke/usage-monitor-for-claude/issues/48) - while the API is returning HTTP 429, opening the popup could keep firing account-profile requests against the already rate-limited endpoint and prolong the backoff; profile fetches now wait out the backoff window like usage fetches do

[Show all code changes](https://github.com/jens-duttke/usage-monitor-for-claude/compare/v1.15.1...v1.16.0)

## [1.15.1] - 2026-05-17

### Fixed

- Popup window now appears at the correct screen corner on high-DPI displays and on multi-monitor setups where the primary monitor is not positioned at virtual x=0; previously the popup could render oversized and overflow the screen edges at 150%/200% scaling, or land at the wrong edge when secondary monitors sat to the left of the primary (thanks to [@jnwildfire](https://github.com/jnwildfire) for the contribution)

[Show all code changes](https://github.com/jens-duttke/usage-monitor-for-claude/compare/v1.15.0...v1.15.1)

## [1.15.0] - 2026-05-01

### Added

- `on_startup_command` event - run a custom command once after the first successful API update following app start (also after using the **Restart** menu option). Receives per-quota utilization and reset timestamps as environment variables, so a command can decide what to do based on which sessions are active - for example, send a Claude Code ping when no five-hour session is running yet
- [Dim usage bars when data is stale](https://github.com/jens-duttke/usage-monitor-for-claude/discussions/28) - the usage section fades to 40% opacity when no successful update has been received for longer than the poll interval, clearly indicating that the displayed data may be outdated
- Account switch notification - switching to a different Claude account now shows an "Account Switched" notification with the new account's email address instead of a misleading "Quota Reset" notification
- Overage bar mode for tray icon bars - each entry in `icon_fields` now accepts an optional `:overage` suffix (e.g. `"five_hour:overage"`) to switch that bar to an over-budget view: the bar is empty when usage is at or below the time marker (on pace or ahead) and fills proportionally as usage climbs toward 100%, making it immediately visible how far you have overrun your expected pace
- Tray icon now distinguishes between "blocked" and "pay-as-you-go" states: a `$` replaces the `C`/percentage when any displayed quota is at 100% but your account still has paid extra-usage credits available, warning that further requests will now consume credits; a `✕` appears only when you are fully blocked (either no extra usage enabled or all credits spent). The `✕` also triggers when the bottom bar reaches 100%, not only the top bar

### Changed

- Tray icon now shows the usage percentage as soon as there is any usage; the `C` placeholder appears only while the top quota is still at 0% (previously the `C` stayed visible up to 50%)

### Fixed

- Usage bars are now always shown in red when they reach 100%, regardless of the time marker position
- Auto-refresh of the OAuth token now works for users who installed Claude Code via npm - the CLI is discovered via PATH and `%APPDATA%\npm`, not only the native Anthropic installer path (thanks to [@timyjsong](https://github.com/timyjsong) for the contribution)

[Show all code changes](https://github.com/jens-duttke/usage-monitor-for-claude/compare/v1.14.0...v1.15.0)

## [1.14.0] - 2026-03-27

### Added

- Verbose mode (`--verbose`) - prints system diagnostics (OS, DPI, WebView2, .NET, Python, dependencies, credentials) to the terminal, making it easy to troubleshoot startup issues without a Python installation

### Changed

- Running from source (`python -m usage_monitor_for_claude`) no longer shows log output by default - use `--verbose` to enable diagnostics

[Show all code changes](https://github.com/jens-duttke/usage-monitor-for-claude/compare/v1.13.1...v1.14.0)

## [1.13.1] - 2026-03-27

### Fixed

- App no longer crashes when the API returns `null` instead of an object for a quota field, e.g. `five_hour: null` (thanks to [@2wplayer](https://github.com/2wplayer) for reporting [#26](https://github.com/jens-duttke/usage-monitor-for-claude/issues/26))

[Show all code changes](https://github.com/jens-duttke/usage-monitor-for-claude/compare/v1.13.0...v1.13.1)

## [1.13.0] - 2026-03-21

### Added

- [Show app version in popup](https://github.com/jens-duttke/usage-monitor-for-claude/discussions/20) - the popup footer now shows the app version (e.g. "1.13.0") in the bottom-right corner
- [Dynamic quota bars](https://github.com/jens-duttke/usage-monitor-for-claude/discussions/12) - the popup now automatically detects and displays all usage fields from the API response; no code change needed when Anthropic adds new quota types. Includes configurable `popup_fields` setting and per-variant alert threshold overrides
- [Configurable tray icon bars](https://github.com/jens-duttke/usage-monitor-for-claude/discussions/11) - new `icon_fields` setting lets you choose which two usage fields are shown in the tray icon (e.g. `["five_hour", "seven_day_sonnet"]`)
- [Configurable tooltip fields](https://github.com/jens-duttke/usage-monitor-for-claude/discussions/10) - new `tooltip_fields` setting lets you choose which usage fields appear in the tray tooltip (e.g. `["five_hour", "seven_day_sonnet"]`)
- Support for the `CLAUDE_CONFIG_DIR` environment variable - the app now reads credentials and settings from a custom Claude config directory when set, falling back to `~/.claude/` as before
- Event commands now receive `USAGE_MONITOR_VERSION` with the running app version, so scripts can use it without hardcoding
- Configurable `bar_divider` color for midnight dividers on weekly progress bars

### Changed

- Improved visibility of midnight dividers on weekly bars
- Time marker color default changed from solid white to slightly transparent (`#fffc`) with a subtle shadow for better contrast on colored bars

[Show all code changes](https://github.com/jens-duttke/usage-monitor-for-claude/compare/v1.12.0...v1.13.0)

## [1.12.0] - 2026-03-20

### Added

- "Project on GitHub" link in the tray context menu to quickly open the project repository
- Live status timer in popup - shows "Updated Xs ago" counting up every second instead of a static timestamp, with "Next update in ..." countdown after 60 seconds
- Tray tooltip now includes the server's error message (e.g. "Rate limited") alongside the HTTP error

### Fixed

- Context menu hover effect not showing on displays with DPI scaling above 100%
- Popup no longer shows an icon in the taskbar while open
- Popup appearing at the wrong position after changing DPI scaling without restarting the app

[Show all code changes](https://github.com/jens-duttke/usage-monitor-for-claude/compare/v1.11.0...v1.12.0)

## [1.11.0] - 2026-03-20

### Added

- Single-instance guard - if the app is already running, a dialog shows the running version and asks whether to replace it (thanks to [@GitHubEtienne](https://github.com/GitHubEtienne) for reporting [#6](https://github.com/jens-duttke/usage-monitor-for-claude/issues/6))

### Fixed

- Popup no longer dismisses immediately or appears off-screen on displays with DPI scaling above 100% (thanks to [@GitHubEtienne](https://github.com/GitHubEtienne) for reporting [#6](https://github.com/jens-duttke/usage-monitor-for-claude/issues/6) and [@igorrr01](https://github.com/igorrr01) for testing)

[Show all code changes](https://github.com/jens-duttke/usage-monitor-for-claude/compare/v1.10.0...v1.11.0)

## [1.10.0] - 2026-03-18

### Added

- New color settings `fg_link` (link text) and `bar_marker` (time-position marker on progress bars) for finer theme control

### Changed

- Context-specific titles: popup shows "Usage Monitor for Claude", tooltip shows "Claude Usage", and context menu shows "Show Claude Usage" instead of the generic "Account & Usage" everywhere
- Popup window rebuilt with HTML/CSS rendering (via Edge WebView2) replacing tkinter - smoother bar animations with CSS transitions, no flickering on updates, and more flexible layout
- Executable size reduced by more than a third (from ~20 MB to ~12.5 MB) by removing unused image codecs and bundled modules

[Show all code changes](https://github.com/jens-duttke/usage-monitor-for-claude/compare/v1.9.0...v1.10.0)

## [1.9.0] - 2026-03-15

### Added

- Day dividers on the weekly usage bar - subtle gaps at local midnight boundaries visually group usage into day segments

### Changed

- `on_reset_command` and `on_threshold_command` now accept an array of command strings to run multiple commands per event (single strings still work)
- `on_reset_command` now fires promptly even when the computer is idle or locked, so automated workflows (e.g. resuming a Claude session) are not delayed until the user returns

[Show all code changes](https://github.com/jens-duttke/usage-monitor-for-claude/compare/v1.8.0...v1.9.0)

## [1.8.0] - 2026-03-15

### Added

- `on_reset_command` and `on_threshold_command` settings to run shell commands when usage events occur (e.g. push notifications, agent orchestration), with event details passed as environment variables. The reset command fires on any usage drop and includes the previous utilization so your script can decide when to act
- "Restart" option in the tray context menu to reload settings without manually closing and reopening the app
- "Test event commands" submenu to fire configured event commands with sample data for quick verification

### Fixed

- Brief console window flash when checking CLI version or refreshing the authentication token

[Show all code changes](https://github.com/jens-duttke/usage-monitor-for-claude/compare/v1.7.0...v1.8.0)

## [1.7.0] - 2026-03-14

### Added

- Ukrainian language support (thanks to [@Actpohomoc](https://github.com/Actpohomoc) for the contribution)
- Configurable alert notifications for extra usage (paid overage) via `alert_thresholds_extra_usage` setting (default: 50%, 80%, 95%)

### Changed

- Usage bars now turn red only when usage passes the time marker (usage ahead of elapsed time), instead of always at 80%
- **Breaking:** Setting `bar_fg_high` renamed to `bar_fg_warn`

[Show all code changes](https://github.com/jens-duttke/usage-monitor-for-claude/compare/v1.6.0...v1.7.0)

## [1.6.0] - 2026-03-10

### Added

- `language` setting to manually override the auto-detected UI language (e.g., `"language": "ja"`)
- Live countdown for reset times in the popup - timers now tick down between API polls instead of staying frozen

### Fixed

- Popup sections could appear in wrong order when usage data was not yet available at startup

[Show all code changes](https://github.com/jens-duttke/usage-monitor-for-claude/compare/v1.5.0...v1.6.0)

## [1.5.0] - 2026-03-08

### Added

- Idle and lock detection - polling pauses when the computer is idle (default: 300 seconds of no keyboard/mouse input) or locked, and resumes immediately when activity returns. Configurable via the `idle_pause` setting (set to `0` to disable)
- Automatic token refresh - when the OAuth session expires, the app runs `claude update` in the background to renew the token without user intervention
- Claude Code version display in the detail popup showing installed versions for CLI, VS Code, Cursor, and Windsurf
- Notification when `claude update` installs a newer CLI version
- Clickable changelog link in the Claude Code section of the detail popup, opening the official Claude Code changelog on GitHub
- User-configurable `max_backoff` setting to cap rate-limit backoff duration (default 15 minutes)
- Terminal logging when running via `python -m usage_monitor_for_claude` - shows API calls, skip reasons, and results (silent in EXE builds)

### Changed

- Increased default polling intervals to reduce API rate-limit errors (`poll_interval`: 120 to 180 seconds, `poll_fast`: 60 to 120 seconds)
- Numeric settings (`poll_interval`, `poll_fast`, etc.) now require integer values - fractional numbers like `120.5` are no longer accepted

### Removed

- "Refresh now" context menu entry - automatic polling makes manual refresh unnecessary, and it could trigger API rate-limit errors

### Fixed

- A successful token refresh followed by a transient API error (e.g. HTTP 500) no longer permanently blocks the new token from being used
- Eliminated race condition where opening the popup could trigger a redundant API call alongside the poll loop, causing HTTP 429 rate-limit errors
- Opening the popup during an active rate-limit backoff no longer triggers an additional API call - the popup shows cached data instead
- Prevented duplicate profile fetches when multiple threads check the account profile simultaneously
- Clicking the tray icon while the popup is open no longer causes the popup to briefly close and immediately reopen
- Fixed double separator line in the popup when usage data is unavailable (e.g. API error on startup)

[Show all code changes](https://github.com/jens-duttke/usage-monitor-for-claude/compare/v1.4.0...v1.5.0)

## [1.4.0] - 2026-03-05

### Changed

- Rate-limit errors (HTTP 429) now use exponential backoff instead of the short error interval, preventing the app from making the problem worse by polling faster
- API error messages now include the server's error detail (e.g. "Rate limited.") when available

### Fixed

- API requests could be permanently rejected (HTTP 429) due to endpoint restrictions on the server side

[Show all code changes](https://github.com/jens-duttke/usage-monitor-for-claude/compare/v1.3.0...v1.4.0)

## [1.3.0] - 2026-03-02

### Added

- Configurable usage alerts when quota exceeds defined thresholds (e.g., 80%, 95%), with separate settings for session and weekly quotas
- Time-aware alert mode (on by default) - suppresses notifications when usage is on track with elapsed time; `alert_time_aware_below` controls up to which threshold this applies, so high thresholds can always fire
- Extra usage section in the detail popup when extra usage is enabled on your account, with automatic currency symbol detection from the system locale (overridable via `currency_symbol` in the settings file)
- Status line in the popup showing when data was last updated and whether a refresh is in progress or failed

### Changed

- Server errors (HTTP 5xx) now show a specific "temporarily unavailable" message instead of the generic HTTP error
- Popup opens immediately with cached data instead of waiting for the API response; errors are shown in the status line while usage bars remain visible
- Popup grows away from the taskbar edge regardless of taskbar position (bottom, top, left, or right)

[Show all code changes](https://github.com/jens-duttke/usage-monitor-for-claude/compare/v1.2.0...v1.3.0)

## [1.2.0] - 2026-03-01

### Added

- Optional settings file (`usage-monitor-settings.json`) to customize polling intervals, popup colors, and icon colors

### Changed

- The code has been split into smaller, focused modules. Running from source now uses `python -m usage_monitor_for_claude`

### Fixed

- No longer sends repeated API requests after a 401 auth error; polls only re-read the credentials file until the token actually changes

[Show all code changes](https://github.com/jens-duttke/usage-monitor-for-claude/compare/v1.1.0...v1.2.0)

## [1.1.0] - 2026-02-28

### Added

- Tray icon supports the Windows light theme
- Session expiry detection with distinct "C!" tray icon when the Anthropic API returns HTTP 401, instead of showing a generic error
- Windows toast notification when quota resets after near-exhaustion (session >95% or weekly >98%), so users know Claude is available again without manually checking
- Adaptive polling that aligns to imminent quota resets for near-immediate feedback when quota refreshes
- Simplified Chinese (zh-CN) and Traditional Chinese (zh-TW) translations

### Changed

- Reassigned tray icon symbols for clearer meaning: "✕" for depleted quota, "!" for errors, "C!" for expired session

### Fixed

- Updated repository URL in setup instructions

[Show all code changes](https://github.com/jens-duttke/usage-monitor-for-claude/compare/v1.0.0...v1.1.0)

## [1.0.0] - 2026-02-26

Initial release.

### Added

- Windows system tray tool displaying live Claude.ai rate-limit usage
- Authentication via Claude Code OAuth token
- Adaptive polling intervals based on current usage levels
- Session (5h) and weekly (7d) limits shown as progress bars in tray icon and detail popup
- Dark-themed detail popup with usage breakdown
- PyInstaller build tooling (spec file + build script)
- 10-language i18n support

[Show all code changes](https://github.com/jens-duttke/usage-monitor-for-claude/releases/tag/v1.0.0)

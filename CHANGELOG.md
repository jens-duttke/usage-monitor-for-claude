# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Single-instance guard - if the app is already running, a dialog shows the running version and asks whether to replace it (thanks to [@GitHubEtienne](https://github.com/GitHubEtienne) for reporting [#6](https://github.com/jens-duttke/usage-monitor-for-claude/issues/6))

### Fixed

- Popup no longer dismisses immediately or appears off-screen on displays with DPI scaling above 100% (thanks to [@GitHubEtienne](https://github.com/GitHubEtienne) for reporting [#6](https://github.com/jens-duttke/usage-monitor-for-claude/issues/6) and [@igorrr01](https://github.com/igorrr01) for testing)

[Show all code changes](https://github.com/jens-duttke/usage-monitor-for-claude/compare/v1.10.0...HEAD)

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

# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- [WinGet](https://learn.microsoft.com/windows/package-manager/) is now documented as an alternative way to install and update the app (`winget install jens-duttke.usage-monitor-for-claude`), together with a note on what the community repository verifies about a package and what it does not - the direct download stays the authoritative source
- Every release now lists the SHA256 of `UsageMonitorForClaude.exe`, so a download can be verified against the official release page - including the file WinGet installs, independently of its validation pipeline

[Show all code changes](https://github.com/jens-duttke/usage-monitor-for-claude/compare/v1.21.0...HEAD)

## [1.21.0] - 2026-08-21

### Added

- Extra usage without a monthly limit (uncapped pay-as-you-go overage, the usual state for Team and Enterprise plans) now appears in the popup as the amount spent - previously the Extra Usage section stayed hidden unless a monthly limit was configured, silently hiding real spending (thanks to [@joeklittle](https://github.com/joeklittle) for the contribution)
- New `alert_extra_usage_spent` setting - absolute spending amounts in your billing currency (e.g. `[50, 100, 150]`) that trigger a notification when extra-usage spending crosses them; complements the percentage thresholds and is the only alert that can fire for uncapped extra usage, where no percentage exists (thanks to [@joeklittle](https://github.com/joeklittle) for the contribution)
- [New `icon_style` setting](https://github.com/jens-duttke/usage-monitor-for-claude/issues/78) - set it to `"numbers"` to show both `icon_fields` values as two stacked percentages on the tray icon instead of one percentage with two bars; each row shows `✕` or `$` when its quota is exhausted (thanks to [@Searcus](https://github.com/Searcus) for the suggestion)

### Fixed

- With uncapped extra usage enabled, an exhausted quota now shows the "extra usage active" tray indicator instead of the exhausted glyph - work continues on paid overage, so the icon no longer suggests Claude has stopped (thanks to [@joeklittle](https://github.com/joeklittle) for the contribution)
- The startup and double-click event commands no longer report `USAGE_MONITOR_EXTRA_LIMIT` as a zero amount for uncapped extra usage - the variable is now omitted when there is no monthly limit (thanks to [@joeklittle](https://github.com/joeklittle) for the contribution)
- On systems with a non-UTF-8 code page (Traditional Chinese, Japanese, Korean and others), the tray icon no longer stops updating after an automatic token renewal - the Claude CLI's output is now read as UTF-8 instead of with the system code page, which crashed the update loop as soon as the CLI printed a non-ASCII character (thanks to [@daweiliutw-ctrl](https://github.com/daweiliutw-ctrl) for reporting [#80](https://github.com/jens-duttke/usage-monitor-for-claude/issues/80))
- After an account switch, the tray icon and popup now show the new account's usage right away - previously, when the switch happened while a usage request was already running, the "account switched" notification appeared next to the previous account's numbers, which stayed on screen until the next scheduled poll

[Show all code changes](https://github.com/jens-duttke/usage-monitor-for-claude/compare/v1.20.0...v1.21.0)

## [1.20.0] - 2026-07-17

### Added

- [Multi-account support](https://github.com/jens-duttke/usage-monitor-for-claude/discussions/23) - launch additional instances with `--config-dir="<path>"` to monitor a second Claude account side by side; each instance reads its own credentials and settings, gets its own tray tooltip prefix and autostart entry, and keeps its `--config-dir` across restarts (thanks to [@hybrid2102](https://github.com/hybrid2102) for the contribution)
- [Custom CLI command](https://github.com/jens-duttke/usage-monitor-for-claude/issues/65) - set `cli_command` (e.g. `{"WSL": ["wsl", "/home/<user>/.local/bin/claude"]}`) to list the Claude Code version of an install the app cannot detect on its own, such as one running inside WSL, alongside the native CLI and the IDE extensions

### Changed

- When a custom config directory is in effect (`--config-dir` or `CLAUDE_CONFIG_DIR`), a `usage-monitor-settings.json` in that directory now takes priority over the one next to the EXE, so each instance can have its own settings

### Fixed

- Closing a pinned popup no longer keeps its system-wide input hooks installed - previously every pin-and-close cycle left another set of hooks behind, gradually adding input lag machine-wide until the app was restarted
- The popup no longer stays invisible (and permanently refuses to open again until restart) when its rendered content happens to be exactly 400 pixels tall
- Starting the app while another instance runs with different rights (e.g. one of them "as administrator") now shows the usual "already running" dialog instead of silently starting a second instance with a second tray icon and doubled API polling
- Answering "Yes" in the "already running" dialog now verifies that the old instance is really gone before starting - if it could not be terminated (for example because it runs with administrator rights), an error message appears instead of both instances silently running side by side
- An account switch is no longer missed for the rest of the session when the profile request fails once around the switch - previously that also suppressed the "account switched" notification and could fire a false "quota reset" notification and reset command instead
- Opening the popup roughly 3 to 5 minutes before a quota reset no longer delays the reset-confirming poll - the tray and the "quota reset" notification/command now react a few seconds after the reset instead of up to two minutes late
- On Chinese, Hindi, and Indonesian Windows systems the app now starts in the system language instead of silently falling back to English (the shipped zh-CN, zh-TW, hi, and id translations were never picked up by the automatic language detection)
- A credentials file with an unexpected structure (e.g. `claudeAiOauth` left empty by a logout, or a file rewritten by another tool) no longer crashes the app and kills polling until restart - it is treated as "no token available right now"
- A profile response with an empty account or organization section no longer crashes the poll loop or the popup
- Configuring `tooltip_fields` or `icon_fields` with a response key that is not a quota field (e.g. `limits`) no longer freezes the tray on stale data - the entry is skipped in the tooltip and rendered as 0% in the icon
- An IDE extensions folder that exists but cannot be read (permission denied, broken junction) no longer breaks the popup or its live updates - the folder is skipped in the Claude Code version list
- Confirming the "already running" dialog after the old instance already exited on its own can no longer terminate an unrelated process that happened to receive the same process ID
- A settings file saved as UTF-8 with BOM (the default of older PowerShell and Notepad versions) is now accepted instead of being rejected with an "Invalid JSON" error that discarded all settings
- Notifications deferred while you were away can no longer stay stuck in the queue for hours (or get lost to a rare crash) when you return at just the wrong moment - they now appear promptly once you are back
- Setting an event command to an empty string (`"on_double_click_command": ""`) now disables it like `[]` does - previously it still activated the double-click machinery, delaying every single click by the double-click interval and launching an empty shell on double-click
- Two quotas resetting at the same time (e.g. a weekly window together with its per-model limit) now produce a single "quota reset" notification instead of identical back-to-back toasts
- When the retry after an expired-token refresh is answered with a rate limit (HTTP 429), the app now honors the server's requested wait time and shows the rate-limit state instead of keeping the credentials-error icon and re-polling the already limited endpoint too early
- Setting the system clock backwards (manual correction, time sync, resuming a virtual machine snapshot) no longer freezes the tray on stale data for the duration of the jump - polling re-anchors to the new clock right away
- A pinned popup no longer stops receiving live updates after a single transient failure (it could previously show stale bars for days with only the clock still ticking) - a failed update is retried on the next tick
- The tray icon no longer permanently stops following light/dark theme switches after a single failed re-render (e.g. during an Explorer restart)
- Two content-height changes arriving in quick succession (e.g. toggling the compact view right as a data update lands) can no longer leave the popup clipped or oversized until the next content change
- When the set of quota bars changes while the popup is open but their number stays the same (e.g. an account switch between two plans), the bars now rebuild with the correct labels instead of showing the new values under the old quota names
- The tray icon shows "99" instead of a clipped, three-digit "100" while utilization is between 99.5% and 100% - "100" stays reserved for the actually-exhausted state
- A tray bar in `overage` mode no longer flips to a plain utilization fill in the short window between a quota reset and the confirming poll - it keeps its overage reading (empty while within budget)
- A `currency_symbol` override that happens to match the system's currency symbol now works - previously it was silently ignored and the billing currency reported by the API won; an empty override now consistently means "no symbol"
- On a weekly usage bar spanning a daylight-saving changeover, the day dividers after the changeover now stay on the actual local midnights instead of drifting by one hour
- The `--verbose` diagnostics now redact the Windows username from paths reliably - previously a differently-cased path (e.g. a lowercase `CLAUDE_CONFIG_DIR`) slipped through unredacted, and a neighboring user profile could be partially mangled
- On Windows 10 versions older than 1703 the app no longer dies at startup with an unhandled error dialog - it now starts with the legacy DPI behavior instead
- The popup's time marker and day dividers no longer shift by one pixel (with a visible slide animation on the marker) after the first live data update
- [Notification icon](https://github.com/jens-duttke/usage-monitor-for-claude/issues/67) - alerts and reset notifications now show the app logo instead of the current tray icon, so the icon no longer says "you have nothing left" when a limit was just reset or is only partway used

[Show all code changes](https://github.com/jens-duttke/usage-monitor-for-claude/compare/v1.19.0...v1.20.0)

## [1.19.0] - 2026-07-14

### Added

- New `on_double_click_command` event - run a custom command when you double-click the tray icon, while a single click still opens the usage popup. Handy for launching a companion tool like [Agent Monitor for Claude](https://github.com/jens-duttke/agent-monitor-for-claude) straight from the tray. Since a double-click is a user-driven action, a command that fails (non-zero exit code) shows its error output in a dialog instead of failing silently
- [Turn off the Claude update notification](https://github.com/jens-duttke/usage-monitor-for-claude/issues/64) - set the new `notify_claude_update` setting to `false` to stop the notification shown when a background token refresh installs a new Claude CLI version

### Changed

- The **Test event commands** menu now prints each command's exit code, stdout, and stderr once it finishes (visible when running from source or with `--verbose`), and pops up an error dialog with stderr when a command exits with a non-zero code, so a command that silently does nothing - a wrong path, for example - is easy to diagnose
- Switching your Claude account now updates the tray icon and popup right away instead of at the next poll (previously up to several minutes, and slower still when the old token had already been rejected and triggered a background `claude update`) - the new account's usage loads as soon as the credentials change
- After your access token expires and gets refreshed, the app now recovers usage and account info as soon as the new token appears, instead of waiting for the next poll or needing a restart

[Show all code changes](https://github.com/jens-duttke/usage-monitor-for-claude/compare/v1.18.1...v1.19.0)

## [1.18.1] - 2026-07-09

### Fixed

- Usage again refreshes promptly right after a session limit resets when the detail popup was opened, or you returned from idle, shortly before the reset - such a fetch no longer delays the reset-confirming poll by up to a full update interval, so the tray and popup stop showing the exhausted state late

[Show all code changes](https://github.com/jens-duttke/usage-monitor-for-claude/compare/v1.18.0...v1.18.1)

## [1.18.0] - 2026-07-02

### Added

- Per-model weekly limits (for example a Fable limit) now appear as their own usage bar, tooltip entry, and alert - Claude's newer usage data reports model-scoped limits in a format the app did not read before, so such a limit would otherwise stay invisible until it blocked you
- Extra usage amounts now show in the account's actual billing currency and precision - Claude's usage data now reports the currency and decimal places, so the amount no longer guesses the symbol from the Windows locale and stays correct even when the billing currency differs from the system's

### Fixed

- Usage now refreshes right after a session limit resets instead of up to a few minutes late - the poll that confirms the reset is timed to land just after it, so the tray icon and popup stop showing the old, exhausted state
- The reset time no longer vanishes from the popup during the last minute before a reset - it now shows a "Reset imminent" note (matching Claude's own usage screen) instead of leaving the line blank

[Show all code changes](https://github.com/jens-duttke/usage-monitor-for-claude/compare/v1.17.0...v1.18.0)

## [1.17.0] - 2026-06-27

### Added

- The detail popup can now be pinned open and moved while pinned, so usage details stay visible during long Claude Code sessions (thanks to [@nmxi](https://github.com/nmxi) for the contribution)
- [New `compact_hide` setting](https://github.com/jens-duttke/usage-monitor-for-claude/issues/55) shrinks the pinned popup to a compact view by hiding chosen sections (account, extra usage, Claude Code versions, status footer) and individual usage bars while it is pinned, so you can keep just the bars you care about on screen; when only the usage bars remain, the "Usage" heading is dropped as well
- Reset times now follow your Windows clock format automatically, showing 24-hour (14:30) or 12-hour (2:30 PM) without any setup; override with the `time_format` setting (thanks to [@rohitjalan142](https://github.com/rohitjalan142) for the contribution)

### Fixed

- [The status footer no longer cuts off text in several languages](https://github.com/jens-duttke/usage-monitor-for-claude/issues/53) - the "next update" line was too long to fit the popup width in Spanish, French, Italian, Portuguese, Ukrainian, and Indonesian and got truncated; the affected phrases are now shorter so the full status fits on one line, and a long error message now shows in full on hover

[Show all code changes](https://github.com/jens-duttke/usage-monitor-for-claude/compare/v1.16.0...v1.17.0)

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

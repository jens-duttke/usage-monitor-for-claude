# Project Guidelines

Apply Python best practices and clean code principles. Only change code relevant to the prompt.
Prioritize readability and auditability - users handle credentials and must be able to verify the code is safe at a glance.

## Platform
- Windows-only application - no `sys.platform` checks or cross-platform guards needed
- Windows APIs (`ctypes.windll`, `winreg`) can be used unconditionally

## Popup Window & DPI
- The popup uses pywebview with a WinForms host window and Edge WebView2
- pywebview 6.x `resize()` **and** `move()` both expect **logical pixels** (pywebview applies DPI scaling internally for both)
- `_tray_position()` still receives physical pixel dimensions (needed to calculate position against Win32 physical coordinates) and returns **logical coordinates** for `move()` - never change this to physical
- `_tray_position()` uses `Shell_TrayWnd` + `MonitorFromWindow` + `GetMonitorInfoW` to find the monitor that owns the taskbar, then compares `work.left > mon.left` (not `> 0`) to detect a left-side taskbar - this correctly handles multi-monitor layouts where the primary monitor is not at virtual x=0
- Never replace `resize()`/`move()` with direct `SetWindowPos` calls for tray-anchored positioning - pywebview's internal scaling means raw Win32 calls would fight with pywebview's coordinate handling. The one exception is the pinned-popup drag (next bullet)
- The pinned-popup drag (`_begin_drag`/`_drag`/`_end_drag`) deliberately uses raw `SetWindowPos` with **physical** cursor coordinates (`GetCursorPos` minus the grab offset captured on mouse-down), not pywebview's `move()`. Reason: `move()` and JS `screenX` deltas are scaled by a single monitor's DPI, which jumps at a monitor boundary and makes the cursor drift off the window and the size break. After a drag that crosses a DPI boundary, `_end_drag` re-asserts the size once via `resize()` against the destination monitor's DPI. Do not collapse this back to `move()` - it reintroduces the mixed-DPI drift
- The taskbar icon is hidden via Win32 extended styles (`WS_EX_TOOLWINDOW` + remove `WS_EX_APPWINDOW`). Do **not** use WinForms `ShowInTaskbar = False` - it recreates the native window handle, which crashes WebView2 from background threads

## Tray Icon Interaction
- pystray has no native double-click support (it fires the default menu item on every `WM_LBUTTONUP`). Double-click is added only when `on_double_click_command` is set: `_install_double_click_handler()` swaps the `WM_NOTIFY` entry in pystray's private `_message_handlers` table (matched by identity against `icon._on_notify`) for `_on_tray_message`. This reaches into pystray internals - if a pystray upgrade renames `_message_handlers`/`_on_notify`, this is where it breaks
- With a command configured, the single click (popup) is deferred by `GetDoubleClickTime()` via a `threading.Timer` and cancelled when the second click arrives; the trailing `WM_LBUTTONUP` that always follows a `WM_LBUTTONDBLCLK` is swallowed via `_swallow_next_up`. All tray-message state is guarded by `_click_lock`, and `_fire_single_click()` re-checks the timer under the lock so a double-click landing exactly as the timer fires still suppresses the popup
- When no `on_double_click_command` is set, the handler is **not** installed - pystray's instant single-click popup must stay untouched (no double-click delay). Do not make the deferral unconditional
- `WM_NOTIFY` and other message handlers (right-click menu) must still fall through to the saved `_pystray_on_notify`

## Event Commands
- Event commands run fire-and-forget with output discarded (`run_event_command` in `command.py`). User-driven actions - the "Test event commands" menu handlers and `on_double_click_command` - pass `capture_output=True`, which captures stdout/stderr, prints them, and raises an error message box when the command exits non-zero, so a wrong path is not swallowed silently. Automatic events (`on_reset_command`, `on_threshold_command`, `on_startup_command`) must stay silent (no `capture_output`) - a background event must never pop a dialog. A new event command belongs on whichever side matches: user-driven surfaces failures, automatic stays silent
- `capture_output` waits for the command on a daemon thread, so the caller (a tray/menu/poll thread) is never blocked, even when the command launches a long-running app

## Claude CLI
- The `cli_command` setting (name -> base command, e.g. a WSL install) is **display only**: `find_installations()` lists each entry *in addition to* the auto-detected native CLI and the IDE extensions, all of which stay exactly as they were. It must never gain a second job - not the token refresh, not the API User-Agent, not authentication of any kind
- Reason it must stay out of the refresh: `refresh_token()` works only as a side effect - `claude update` makes the CLI renew the expired token *in its own credentials file*, and `cache._try_token_refresh()` then re-reads `CLAUDE_CREDENTIALS` and gives up when the token is unchanged. A WSL CLI keeps its credentials inside WSL (`/home/<user>/.claude/`), so routing the refresh through it would renew a token this app never reads: the Windows file stays untouched, the unchanged-token check always trips, the refresh can never succeed - and the user's WSL install gets updated unasked from a poll thread. The native CLI is also why it stays listed: it is the install the app actually authenticates with
- `cli_version()` caches per binary **mtime**, so an update is picked up automatically. A `cli_command` has no local file to stat (`wsl ...` cannot be mapped to a Windows path reliably), so `_command_version()` caches per command tuple for the **process lifetime**: updating that CLI shows up after an app restart. Do not re-probe per read - the popup's `_update_loop` calls `find_installations()` on every data change, which would boot WSL every few minutes. Never cache a failed run (timeout/OSError/lost stream) - that would pin an empty version for the whole session
- Both version helpers run through `_parse_version()`. Never write an unparsed string into a version cache - it is rendered as a version in the popup
- Every subprocess call in `claude_cli.py` goes through `_run_cli()`, which pins `encoding='utf-8', errors='replace'`. The Claude CLI is a Node application and writes UTF-8 whatever the Windows code page is; letting `subprocess` decode with the ambient locale codec instead raises inside the reader thread it uses to drain the pipe, and the stream then comes back as `None` despite `capture_output=True`. The loss is per-stream, so an ASCII version number next to a non-ASCII npm warning leaves stdout intact and drops only stderr. Do not add a bare `subprocess.run` here, and do not extend this to `command.py` - that one runs arbitrary user commands, where a `.bat` emits the OEM code page and the ambient codec is the correct assumption
- `_run_cli()` raises `OSError` when a stream comes back `None` rather than substituting `''`. That is what routes a lost stream into each caller's existing error handling with no per-call-site branching: `refresh_token()` reports an error instead of concatenating `None`, and both version probes fall back to their `except` and return `''` **uncached**, so the next poll retries. Substituting `''` instead would cache the empty version and turn a transient failure into a sticky one

## Notifications
- Windows shows the process's app icon in the toast header. Without an explicit identity that icon is the live tray icon, which reflects the most-exhausted quota - so a "quota reset" or partial-usage toast would carry the exhausted `✕` glyph and contradict its own text. `notification_identity.register_notification_identity()` gives the process a fixed identity instead: it registers `HKCU\Software\Classes\AppUserModelId\<AUMID>` with `DisplayName` + `IconUri` and then calls `SetCurrentProcessExplicitAppUserModelID`, so every toast shows the neutral app logo regardless of the tray icon. No Start Menu shortcut is needed - the registry registration alone is enough for the legacy `Shell_NotifyIcon` toast
- `register_notification_identity()` runs once in `__main__.py`, right after the single-instance check and **before any window is created** (the AppUserModelID must be set before the process presents UI). It re-registers on every startup because a frozen build extracts the logo to a fresh `sys._MEIPASS` directory each run, changing its path
- The logo is a **multi-size `.ico`** (`notification_logo.ico`, 16-256 px), never a single PNG: Windows renders the toast header icon small and downscales a single large image so badly that the "C" looks jagged; a multi-size `.ico` lets Windows pick a crisp dedicated frame (the same reason the tray/EXE icon stays sharp). `IconUri` accepts `.ico`. The asset is derived from `usage_monitor_for_claude.ico` with the usage bars emptied (empty-bars = "capacity available"); it is a separate notification-only asset, so the tray/EXE icon is unchanged. It is bundled via the spec `datas`
- Registration is best-effort and must never block startup: a missing logo file or a registry write error makes it return early and keep the default identity (the tray icon) - better than an empty/placeholder icon. Setting the AUMID only *after* the registry write succeeds avoids Windows showing its generic 3-square placeholder (which is what an AUMID registered without a resolvable icon produces)

## Quota Fields
- Never hardcode API quota field names (e.g. `five_hour`, `seven_day_sonnet`) in display logic, alert handling, or reset detection - new fields must be auto-detected from the API response structure
- A quota field is any dict entry with `utilization` and `resets_at` keys; `extra_usage` has a separate structure and is handled independently
- Quota fields can be `null` in the API response (e.g. when a quota type is not enabled for the account) - always use `(data.get('key') or {})` instead of `data.get('key', {})` when chaining `.get()` calls, because the latter returns `None` when the key exists with a `null` value
- Labels, periods, and sort order are derived from the field name via `parse_field_name()` - no per-field mapping tables
- Model-scoped limits (e.g. a weekly Fable limit) arrive only inside the `limits` array via `scope.model`, not as top-level fields - `_merge_scoped_limits()` in `api.py` normalizes each into a synthetic top-level field (e.g. `seven_day_fable`) so all of the above applies unchanged. The period prefix is derived from the same-`group` non-scoped limit's shared `resets_at` (never hardcoded); an existing top-level field is never overwritten, and inactive scoped limits (no `resets_at`) are still surfaced at 0%
- Locale files use template keys (`session_label`, `weekly_label`, `notify_threshold_generic`) - never add per-field translation keys

## Polling & Reset Alignment
- `cache.update()` enforces a hard `POLL_FAST` cooldown - no successful fetch happens more often than every `POLL_FAST` seconds. All poll scheduling is built around this floor; `_align_to_reset()` never returns an interval below `POLL_FAST` (enforced by a test invariant)
- The one exception to that floor is `cache.update(force=True)`, used only after a confirmed account switch: the poll loop watches the credentials access token and, when it changes to a token whose account UUID differs (probed via `ensure_profile(bypass_rate_limit=True)`, `_account_switched()`), forces a single immediate fetch that bypasses both the cooldown and the 429 backoff. Safe because the newly selected account has no polling history and cannot be the source of either throttle; the old account's reset alignment is moot once its data is replaced, so the danger-window rule does not apply
- A switch can also land *while a fetch is in flight*, which pairs the previous account's usage with the new account's profile. Two rules keep that from being mistaken for a completed switch: the poll loop reads its `token_seen` baseline **before** `self.update()` (reading it after would swallow the change and skip the forced refetch entirely), and `update()` discards the identity comparison when `UpdateResult.token` - the token the successful fetch was sent with - no longer matches the credentials file. That guard must return **before** `ensure_profile()`, so the popup keeps showing the old account's name next to its own numbers instead of the new name next to stale ones. Every baseline (`_prev_account_uuid`, `_prev_utilization`, `_notified_thresholds`) stays untouched, so the forced refetch reports the switch together with the new account's data. Never let `_record_success()` infer that token by re-reading the credentials - it must be the one captured before the request
- On a 401, `_try_token_refresh()` retries with the current credentials token directly (skipping the slow `claude update` subprocess) whenever it already differs from the token that failed - the account-switch / out-of-band-refresh case - and only runs the CLI refresh when the token is unchanged. This keeps an account switch from stalling on a subprocess of up to 60s while the old (already revoked) token returns 401
- When a 401 leaves the token blocked (`_last_failed_token`) - e.g. the stored access token expired and `claude update` did not renew it - the poll-loop token watcher retries as soon as the credentials token changes, even for the same account (`self._last_response.get('auth_error')` branch, a non-forced update so cooldown/backoff still apply). A token refreshed out of band then recovers both usage and profile promptly instead of only at the next error-cadence poll or after a restart
- Invariant: no discretionary fetch may land in the "danger window" - the last `POLL_FAST - RESET_BUFFER` seconds before a quota reset. A fetch there consumes the cooldown and forces the reset-confirming poll to overshoot the reset. The reset-aligned cadence poll owns the post-reset confirmation
- The cadence scheduler (`_align_to_reset`) already never schedules a poll into the danger window. Discretionary fetches must defer to it when a reset is within `POLL_FAST`: the popup skips its background refresh (`_should_refresh_usage()`), and the idle-return path realigns via `_reset_aligned_poll_target()` instead of polling immediately. Cold start (no data yet) is the only allowed exception
- The poll-loop push-forward (which avoids a redundant fetch right after a popup fetch) reacts only to an actual new fetch (`last_success_time` advanced) and never moves a poll past a reset-aligned slot

## Security & Transparency
- All URLs and API endpoints as top-level constants - no dynamic URL construction
- Network communication exclusively with `api.anthropic.com` - no other destinations
- Credentials used only in HTTP Authorization headers - never log, store, or transmit elsewhere
- No file write operations - the app writes no files. The only system state it changes is two `HKCU` registry values: the notification identity (`notification_identity.py`) and the autostart entry (`autostart.py`). Any new persistent write needs a matching update in `README.md` and `PRIVACY.md` - the "writes no files" claim is part of the audit story and must never become inaccurate
- No `eval()`, `exec()`, `compile()`, or dynamic imports - no dynamic code execution
- No obfuscation - no base64-encoded strings, no encoded URLs or tokens
- Modular package architecture in `usage_monitor_for_claude/` - small focused modules are easier to audit than one large file
- Security-critical code (credentials, API calls) isolated in `api.py` - the only module handling credentials
- Pure data files (translations, config) stay separate - they contain no logic or credential access
- Minimal, well-known dependencies only (e.g., requests, Pillow, pystray)

## Type Hints & Documentation
- Module docstring as very first element in file (title with equals underline, blank line, description)
- Always include `from __future__ import annotations` as first import (after module docstring)
- Type hints in function signatures only, not in docstrings
- numpydoc (NumPy-style) docstrings for all public functions, classes, and non-trivial methods
- Skip docstrings for trivial/self-explanatory methods (1-3 lines where the name fully describes the behavior)
- Never mention changes, improvements, or type hints in comments or docstrings
- `# type: ignore` only with specific error code and short reason: `# type: ignore[code]  # reason`

## Formatting
- PEP8-based with extended line length of 140-160 characters (flexible for arg parsing when alignment improves readability)
- Function signatures and calls on one line when reasonable
- Never use deep indentation to align with previous line's opening bracket/parenthesis
- When breaking lines, use standard 4-space indentation from statement start
- Single quotes (`'`) default, double (`"`) when containing single quotes, triple-double (`"""`) for docstrings
- Use hyphens (`-`) for dashes in text, never em dashes (`—`) or en dashes (`–`)

## Spacing
- Two blank lines between top-level functions/classes, one between methods
- Blank lines separate logical blocks (after guards, before returns)

## Imports
- Three groups separated by blank lines: standard library, third-party, local
- Within groups: `import` before `from...import`, sorted alphabetically
- Relative imports within the `usage_monitor_for_claude` package (e.g. `from .api import ...`), except `__main__.py` which requires absolute imports for PyInstaller compatibility
- Absolute imports for external packages, avoid wildcards

## Structure
- Main exported functions first, then helpers in logical order
- In library modules: prefix non-exported helpers with underscore; in executable scripts: no underscore prefix (everything is internal)
- `__all__` for library modules; omit for executable scripts

## Style
- Prefer functional/modular code over classes
- Isolate side effects in dedicated modules (e.g. `api.py`, `command.py`) - keep helper and utility functions pure
- Descriptive, self-explanatory variable and parameter names, no global variables - no ambiguous names like `other`, `data2`, `flag`. Every name must be immediately clear without reading the surrounding code
- Comments only for complex/non-obvious code and math operations - never about improvements or changes

## List Comprehensions
- Avoid complex comprehensions with multiple conditions or long expressions
- Use explicit loops with guard clauses when: multiple conditions, repeated function calls per item, or unclear logic

## Validation & Errors
- Validate inputs at function start with assertions or exceptions
- Early returns and guard clauses

## PyInstaller / Build
- Spec file: `usage_monitor_for_claude.spec` - all build config lives there
- When adding new data files (translations, configs, assets): add them to the `datas` list in the spec file
- When adding new imports: check if PyInstaller detects them automatically; if not, add to `hiddenimports`
- Never exclude standard library modules that are transitive dependencies (e.g., `email` is needed by `urllib3`/`requests`)
- After any dependency change, verify the `excludes` list doesn't break transitive imports

## README
- Keep the feature list and descriptions in `README.md` in sync when adding, changing, or removing user-facing features
- When adding or removing a `locale/*.json` file, update the language count and the parenthesized list in the "N languages (...)" feature bullet to match the actual locale files - both the number and the names must stay in sync
- The feature list follows the user's decision journey - place new features in the appropriate tier:
  1. **Getting started** (barrier to entry): Portable, Zero configuration
  2. **Daily visible value** (what the user sees every day): Live tray icon, Detail popup, Claude Code versions
  3. **Proactive protection** (alerts and automation): Smart alerts, Event commands
  4. **Visual quality** (richer understanding of data): Time marker
  5. **Reliability** (it just keeps working): Automatic token refresh, Adaptive polling
  6. **Reach and preferences** (secondary concerns): 13 languages, Customizable
- Write feature descriptions from the user's perspective - lead with the problem solved or value gained, not the implementation. Ask: "why would someone choose this tool because of this feature?"
- Unique features (no competing tool has them) deserve a standalone bullet; convenience improvements that could be described as sub-details of an existing feature belong in that feature's description instead

## Changelog
- Update `CHANGELOG.md` for every user-facing change (new features, bug fixes, behavior changes, UI changes)
- Do not add changelog entries for internal refactors, code style changes, or documentation-only changes unless they affect the user
- Changes to `CLAUDE.md` and the `.claude/commands/` files are invisible to users - never mention them in changelog entries or commit messages
- Use the `/changelog` command to write the entry - it holds the format, grouping (Added/Changed/Fixed/Removed), user-perspective wording, issue/discussion linking, and the "did the bug ship in the last release?" check

## Releasing
- Cut releases with the `/releasing` command - it bumps the version (`__version__` in `usage_monitor_for_claude/__init__.py` plus all four fields in `version_info.py`), rolls `CHANGELOG.md`, runs the tests, and prepares the `gh release create` notes. Per the git rule it never tags or publishes - it hands the final command to you to run

## Distribution
- Every release is pushed to the WinGet community repository by `.github/workflows/winget.yml`. That manifest is **not owned** by this project: anyone may submit a version for the package id, and the automated validation checks the installer *domain* (`github.com`), never the repository path behind it - a manifest pointing at a foreign account is caught only by a moderator reading the diff. The package's own first version (1.16.0) was in fact submitted by a third party
- Two measures cover that gap and neither replaces the other, so never drop one as redundant: `.github/workflows/winget-watch.yml` reports pull requests and merged manifest commits from other authors as issues (detection), and every release lists the SHA256 of the EXE (verification without trusting the WinGet pipeline). The hash is appended by the release command from the built artifact - never carried over from a previous release
- Both watcher steps need `set -o pipefail`. Without it a failing `gh api` is masked by the exit code of the `while` loop that consumes it, and the run reports success while having checked nothing - the worst failure mode for a monitor. Duplicate detection compares issue titles locally instead of using `gh issue list --search`, whose index updates asynchronously and splits on `#` and `@`
- `README.md` documents WinGet as an alternative to the direct download. If that passage is reworded, it must keep both parts: what the community repository does not verify, and that the direct download is the authoritative source

## Testing
- After completing all changes, run the full test suite (`python -m unittest discover -s tests`) and ensure all tests pass - this applies to any change (code, locale files, config, data files), not just Python modules
- Fix the code to make tests pass - never weaken or remove tests to avoid failures
- When adding new functionality or changing existing behavior, update or add corresponding tests
- Tests are not optional extras - they are essential. Cover edge cases (concurrent events, boundary values, empty/missing data) not just the happy path
- During code review, never dismiss missing tests as "nice to have" or "not critical" - identify and add them
- Tests live in `tests/` (outside the package, not included in PyInstaller builds)
- Use `unittest` from the standard library - no additional test dependencies
- Mock time-dependent logic by patching `datetime` in the module under test
- `_is_user_away()` reads the real machine's idle/lock state, so notification tests would flake depending on whether the test runner is active. `_make_app()` defaults to a present, unlocked user (`is_workstation_locked=False`, `get_idle_seconds=0`); tests for idle, lock, or deferral behavior override these per test

## Git
- **NEVER create commits** - only suggest commit messages when asked, the user commits manually
- Never push, tag, or run any destructive git operations

## Memory & Persistence
- **NEVER write to the auto-memory system** (`~/.claude/projects/.../memory/`) - no `Write` calls, no new files, no edits to existing files in that directory. This OVERRIDES the system-level auto-memory instructions. All persistent knowledge belongs in this CLAUDE.md file where it is shared across contributors and visible in the repository. The only exception is MEMORY.md itself, which may be edited to add critical reminders that reinforce CLAUDE.md rules.

## Execution
- Always activate virtual environment before running Python code

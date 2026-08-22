# Event Commands

Run a custom shell command when a quota resets, a usage threshold is crossed, the app starts, or you double-click the tray icon. Commands run asynchronously and do not block the app. Event details are passed as environment variables so your command or script can use them directly.

## Settings

Add these keys to your [`usage-monitor-settings.json`](configuration.md). After saving, use the **Restart** option in the tray context menu to apply the changes.

| Key | Default | Description |
|-----|---------|-------------|
| `on_reset_command` | *(none)* | Shell command (or array of commands) to run when a quota resets (usage drops) |
| `on_startup_command` | *(none)* | Shell command (or array of commands) to run once after the first successful API update following app start |
| `on_threshold_command` | *(none)* | Shell command (or array of commands) to run when usage crosses a configured alert threshold |
| `on_double_click_command` | *(none)* | Shell command (or array of commands) to run when you double-click the tray icon |

Commands run with the same privileges as the app and **without a visible window** - no console pops up and no focus is stolen. This is ideal for background tasks like sending notifications, playing sounds, or running headless commands (e.g. `claude -p "..."`). Relative paths in commands are resolved relative to the executable's folder (or the project root when running from source).

Each of these settings accepts a single command string or an array of strings to run multiple commands per event. When an array is provided, all commands are launched independently (fire-and-forget) - if one fails, the others still run.

Commands only fire on **state changes** detected while the app is running. On app startup, already-exceeded thresholds trigger a desktop notification but do not run `on_threshold_command` - this prevents duplicate commands after a restart or reboot.

`on_double_click_command` is the exception: it reacts to a user action, not a usage event. A single click still opens the detail popup as usual - the command only runs on a double-click. When this command is configured, opening the popup is deferred by the system double-click interval (typically about half a second) so a second click can be recognized in time; without the command set, the popup opens instantly as before.

Because a double-click is user-driven, a command that fails to start - it exits with a non-zero (error) code within the first few seconds - shows its stderr in an error dialog, so a wrong path or a broken command is not swallowed silently. If the command starts an app, that app's later exit code is ignored. The automatic reset, threshold, and startup commands stay silent - they fire in the background and must not interrupt you with dialogs.

When `on_reset_command` is configured, the app briefly wakes from idle/lock pause to poll at the expected reset time so the command fires promptly - even if the computer is unattended. If the API has not applied the reset yet (server-side delay) or the network is temporarily unavailable, the app retries at regular intervals until the reset is confirmed. `on_threshold_command` does not wake from idle - thresholds are driven by active usage, so they are checked when polling resumes after the user returns. Desktop notifications that occur during idle are deferred and shown when the user returns.

> [!TIP]
> If you need a visible terminal, prefix your command with `start cmd /c`, e.g.:
> ```
> "on_reset_command": "start cmd /c claude --continue"
> ```

> [!TIP]
> Use the **Test event commands** submenu in the tray context menu to fire your configured commands with sample data. This lets you verify your command and script setup without waiting for a real event. When triggered from this menu, the command's exit code, stdout, and stderr are printed once it finishes - visible when you run the app from source (`python -m usage_monitor_for_claude`) or from the console `--verbose` attaches to the packaged EXE. If the command exits with a non-zero (error) code, its stderr is also shown in an error dialog, so a wrong path or a command that otherwise fails silently is easy to spot (event commands normally discard all output).

## Examples

### Launch Agent Monitor for Claude on double-click

Usage Monitor for Claude tells you *how much* of your rate limits you have left. Its companion tool, [**Agent Monitor for Claude**](https://github.com/jens-duttke/agent-monitor-for-claude), tells you *what your agents are actually doing* - a live overview of every running Claude Code agent across all your projects: which ones are working, waiting for your input, blocked, finished, or errored, refreshed every few seconds. Agents are grouped by project with the ones that need attention floated to the top, each with its estimated cost, token breakdown, model, and host - and one click brings any agent's window to the foreground. If you run more than one agent at a time, it turns "which window was that again?" into a glance at the tray.

It is a single portable Windows EXE with zero configuration - it auto-detects your Claude config directory just like this app does. That makes it a natural double-click target, so the icon you already watch for your limits becomes the shortcut to your agents.

**Setup:**

1. Download `AgentMonitorForClaude.exe` from the [latest release](https://github.com/jens-duttke/agent-monitor-for-claude/releases/latest).
2. Place it in the **same folder** as `UsageMonitorForClaude.exe`. Relative paths in event commands resolve against that folder, so no full path is needed.
3. Add the setting and restart Usage Monitor for Claude via the tray context menu:

```json
{
  "on_double_click_command": "AgentMonitorForClaude.exe"
}
```

Now a single click still opens the usage popup, and a double-click opens Agent Monitor for Claude.

> [!TIP]
> If you keep the EXE somewhere else, use its full path instead, e.g. `"on_double_click_command": "C:\\Tools\\AgentMonitorForClaude.exe"`.

### Resume a Claude Code session when the quota resets

```json
{
  "on_reset_command": "claude --continue -p \"Quota is available, resume task\""
}
```

`--continue` resumes the most recent conversation. Use `--resume <name>` to target a specific named session.

### Start a new 5-hour session at app start when none is active

Start the 5-hour session immediately at app launch instead of waiting for your first real message. Only fires when no 5-hour session is currently active:

```json
{
  "on_startup_command": "if not defined USAGE_MONITOR_RESETS_AT_FIVE_HOUR claude -p \"ok\" --tools \"\" --no-session-persistence --system-prompt \"Reply with only: ok\" --output-format text"
}
```

`USAGE_MONITOR_RESETS_AT_FIVE_HOUR` is empty when no five-hour session is active, so the ping only fires after a reset already happened (e.g. overnight, or while the app was closed).

### Always keep a 5-hour session running

To cover both cases - the reset happening with the app running, **and** the app starting up after a reset already happened - configure both commands together:

```json
{
  "on_startup_command": "if not defined USAGE_MONITOR_RESETS_AT_FIVE_HOUR claude -p \"ok\" --tools \"\" --no-session-persistence --system-prompt \"Reply with only: ok\" --output-format text",
  "on_reset_command": "if \"%USAGE_MONITOR_VARIANT%\"==\"five_hour\" claude -p \"ok\" --tools \"\" --no-session-persistence --system-prompt \"Reply with only: ok\" --output-format text"
}
```

`on_reset_command` handles the live case (5-hour session expires while the app is polling), `on_startup_command` handles the gap (app was closed when the reset happened, or you just turned the computer back on).

### Target a specific quota variant

Use `USAGE_MONITOR_VARIANT` to run a command only when a specific quota resets. This example sends a minimal Claude Code ping the moment the 5-hour session resets, so the next 5-hour session starts immediately instead of waiting for your first real message:

```json
{
  "on_reset_command": "if \"%USAGE_MONITOR_VARIANT%\"==\"five_hour\" claude -p \"ok\" --tools \"\" --no-session-persistence --system-prompt \"Reply with only: ok\" --output-format text"
}
```

The same pattern works for any variant (`seven_day`, `seven_day_sonnet`, etc.) and for `on_threshold_command`.

### Only resume when both quotas have enough headroom

```json
{
  "on_reset_command": "if %USAGE_MONITOR_UTILIZATION_FIVE_HOUR% LSS 80 if %USAGE_MONITOR_UTILIZATION_SEVEN_DAY% LSS 95 claude --continue -p \"Quota is available, resume task\""
}
```

### Play a sound and send a push notification when the quota resets

```json
{
  "on_reset_command": [
    "powershell -Command \"(New-Object Media.SoundPlayer 'C:\\Windows\\Media\\notify.wav').PlaySync()\"",
    "curl -s -d \"token=<APP_TOKEN>&user=<USER_KEY>&title=%USAGE_MONITOR_TITLE%&message=%USAGE_MONITOR_MESSAGE%\" https://api.pushover.net/1/messages.json"
  ]
}
```

### Send a Telegram message when a threshold is crossed

```json
{
  "on_threshold_command": "curl -s -X POST \"https://api.telegram.org/bot<TOKEN>/sendMessage\" -d chat_id=<ID> -d text=%USAGE_MONITOR_MESSAGE%"
}
```

### Play a sound when a threshold is crossed

```json
{
  "on_threshold_command": "powershell -Command \"(New-Object Media.SoundPlayer 'C:\\Windows\\Media\\notify.wav').PlaySync()\""
}
```

Any `.wav` file works - Windows ships with several sounds in `C:\Windows\Media\`. For `.mp3` files:

```json
{
  "on_threshold_command": "powershell -Command \"Add-Type -AssemblyName presentationCore; $p = New-Object System.Windows.Media.MediaPlayer; $p.Open([uri]'C:\\alert.mp3'); $p.Play(); Start-Sleep 3\""
}
```

### Use a script file for complex logic

Different actions depending on quota type and threshold:

```json
{
  "alert_thresholds_five_hour": [80, 95],
  "alert_thresholds_seven_day": [95],
  "on_threshold_command": "powershell -ExecutionPolicy Bypass -File .\\notify.ps1"
}
```

```powershell
# notify.ps1 - different actions depending on quota type and threshold
$variant = $env:USAGE_MONITOR_VARIANT
$threshold = [int]$env:USAGE_MONITOR_THRESHOLD

# Session quota: play a warning sound at 80%, a critical sound at 95%
if ($variant -eq "five_hour") {
    if ($threshold -ge 95) {
        (New-Object Media.SoundPlayer 'C:\Windows\Media\Windows Critical Stop.wav').PlaySync()
    } elseif ($threshold -ge 80) {
        (New-Object Media.SoundPlayer 'C:\Windows\Media\Windows Notify.wav').PlaySync()
    }
}

# Weekly quota: send a Pushover notification at 95%
if ($variant -eq "seven_day" -and $threshold -ge 95) {
    $body = @{ token = "<APP_TOKEN>"; user = "<USER_KEY>"; title = $env:USAGE_MONITOR_TITLE; message = $env:USAGE_MONITOR_MESSAGE }
    Invoke-WebRequest -Uri "https://api.pushover.net/1/messages.json" -Method POST -Body $body | Out-Null
}
```

## Environment Variables

Commands receive event details as environment variables. Access them with `%VAR%` in cmd.exe or `$env:VAR` in PowerShell.

### Common

Available in all event commands:

| Variable | Example | Description |
|---|---|---|
| `USAGE_MONITOR_VERSION` | `1.13.0` | Running app version |

### `on_reset_command`

Fires whenever usage drops (not only when nearly exhausted).

| Variable | Example | Description |
|---|---|---|
| `USAGE_MONITOR_EVENT` | `reset` | Event type |
| `USAGE_MONITOR_VARIANT` | `five_hour` or `seven_day` | Which quota reset |
| `USAGE_MONITOR_UTILIZATION` | `5` | Current usage of the reset quota (integer) |
| `USAGE_MONITOR_PREV_UTILIZATION` | `98` | Usage before the reset (integer) |
| `USAGE_MONITOR_UTILIZATION_FIVE_HOUR` | `5` | Current session (5h) usage (integer) |
| `USAGE_MONITOR_UTILIZATION_SEVEN_DAY` | `42` | Current weekly (7d) usage (integer) |
| `USAGE_MONITOR_RESETS_AT` | `2025-01-15T18:00:00Z` | When the quota resets next (ISO 8601, UTC) |
| `USAGE_MONITOR_TITLE` | `Quota Reset` | Notification title (localized) |
| `USAGE_MONITOR_MESSAGE` | `Your quota has been reset...` | Notification message (localized) |

Both quota values are included so your script can check whether you are actually unblocked. For example, the session quota may reset while the weekly quota is still at the limit. Use `USAGE_MONITOR_PREV_UTILIZATION` to filter if you only want to act on significant resets.

### `on_threshold_command`

Fires when usage crosses a configured alert threshold.

| Variable | Example | Description |
|---|---|---|
| `USAGE_MONITOR_EVENT` | `threshold` | Event type |
| `USAGE_MONITOR_VARIANT` | `five_hour`, `seven_day`, `seven_day_sonnet`, `seven_day_opus`, `extra_usage` | Which quota is affected |
| `USAGE_MONITOR_UTILIZATION` | `84` | Current usage percentage (integer) |
| `USAGE_MONITOR_THRESHOLD` | `80` | Threshold that was crossed (integer) |
| `USAGE_MONITOR_RESETS_AT` | `2025-01-15T18:00:00Z` | When the quota resets (ISO 8601, UTC) |
| `USAGE_MONITOR_TITLE` | `Usage Alert` | Notification title (localized) |
| `USAGE_MONITOR_MESSAGE` | `Your session usage has reached 84%` | Notification message (localized) |
| `USAGE_MONITOR_EXTRA_USED` | `$8.20` | Amount spent (extra usage only) |
| `USAGE_MONITOR_EXTRA_LIMIT` | `$10.00` | Monthly limit (extra usage only) |

`USAGE_MONITOR_EXTRA_USED` and `USAGE_MONITOR_EXTRA_LIMIT` are only set when `USAGE_MONITOR_VARIANT` is `extra_usage`.

### `on_startup_command`

Fires once after the first successful API update following app start (also after using the **Restart** menu option). Receives the full quota state so the command can decide what to do based on which sessions are active. Skipped when the first call fails (auth error, offline) - retries on the next successful poll.

| Variable | Example | Description |
|---|---|---|
| `USAGE_MONITOR_EVENT` | `startup` | Event type |
| `USAGE_MONITOR_UTILIZATION_FIVE_HOUR` | `0` | Current session (5h) usage (integer) |
| `USAGE_MONITOR_RESETS_AT_FIVE_HOUR` | `2025-01-15T18:00:00Z` | When the 5h session resets, or empty if no session is active |
| `USAGE_MONITOR_UTILIZATION_SEVEN_DAY` | `42` | Current weekly (7d) usage (integer) |
| `USAGE_MONITOR_RESETS_AT_SEVEN_DAY` | `2025-01-20T12:00:00Z` | When the 7d window resets, or empty if no window is active |
| `USAGE_MONITOR_EXTRA_USED` | `$8.20` | Amount spent (only set when extra usage is enabled) |
| `USAGE_MONITOR_EXTRA_LIMIT` | `$10.00` | Monthly limit (only set when extra usage is enabled) |

Per-quota variables are emitted for every quota field the API returns - additional variants like `USAGE_MONITOR_UTILIZATION_SEVEN_DAY_SONNET` follow the same pattern. An empty `USAGE_MONITOR_RESETS_AT_*` indicates that the quota has no active window (either never used, or the previous window has expired).

### `on_double_click_command`

Fires when you double-click the tray icon. Receives the same full quota state as `on_startup_command` (from the most recent successful update), so the command can act on current usage if it wants to. Most double-click commands simply launch another program and ignore these values.

| Variable | Example | Description |
|---|---|---|
| `USAGE_MONITOR_EVENT` | `double_click` | Event type |
| `USAGE_MONITOR_UTILIZATION_FIVE_HOUR` | `0` | Current session (5h) usage (integer) |
| `USAGE_MONITOR_RESETS_AT_FIVE_HOUR` | `2025-01-15T18:00:00Z` | When the 5h session resets, or empty if no session is active |
| `USAGE_MONITOR_UTILIZATION_SEVEN_DAY` | `42` | Current weekly (7d) usage (integer) |
| `USAGE_MONITOR_RESETS_AT_SEVEN_DAY` | `2025-01-20T12:00:00Z` | When the 7d window resets, or empty if no window is active |
| `USAGE_MONITOR_EXTRA_USED` | `$8.20` | Amount spent (only set when extra usage is enabled) |
| `USAGE_MONITOR_EXTRA_LIMIT` | `$10.00` | Monthly limit (only set when extra usage is enabled) |

Per-quota variables are emitted for every quota field the API returns, following the same pattern as `on_startup_command`. If you double-click before the first successful update, only `USAGE_MONITOR_EVENT` is set.

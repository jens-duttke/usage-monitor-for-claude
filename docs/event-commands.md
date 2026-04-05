# Event Commands

Run a custom shell command when a quota resets or a usage threshold is crossed. Commands run asynchronously and do not block the app. Event details are passed as environment variables so your command or script can use them directly.

## Settings

Add these keys to your [`usage-monitor-settings.json`](configuration.md). After saving, use the **Restart** option in the tray context menu to apply the changes.

| Key | Default | Description |
|-----|---------|-------------|
| `on_reset_command` | *(none)* | Shell command (or array of commands) to run when a quota resets (usage drops) |
| `on_threshold_command` | *(none)* | Shell command (or array of commands) to run when usage crosses a configured alert threshold |

Commands run with the same privileges as the app and **without a visible window** - no console pops up and no focus is stolen. This is ideal for background tasks like sending notifications, playing sounds, or running headless commands (e.g. `claude -p "..."`). Relative paths in commands are resolved relative to the executable's folder (or the project root when running from source).

Both settings accept a single command string or an array of strings to run multiple commands per event. When an array is provided, all commands are launched independently (fire-and-forget) - if one fails, the others still run.

Both settings also accept an **object keyed by quota variant** to run different commands per quota type, or to target only specific variants:

```json
{
  "on_reset_command": {
    "five_hour": "claude -p \"ok\" --tools \"\" --no-session-persistence --system-prompt \"Reply with only: ok\" --output-format text",
    "seven_day": "echo weekly reset"
  }
}
```

With a variant object, a command only fires when the matching quota variant resets or crosses a threshold. Variants not listed in the object are silently ignored. Each variant's value can be a string or an array of strings.

Commands only fire on **state changes** detected while the app is running. On app startup, already-exceeded thresholds trigger a desktop notification but do not run `on_threshold_command` - this prevents duplicate commands after a restart or reboot.

When `on_reset_command` is configured, the app briefly wakes from idle/lock pause to poll at the expected reset time so the command fires promptly - even if the computer is unattended. If the API has not applied the reset yet (server-side delay) or the network is temporarily unavailable, the app retries at regular intervals until the reset is confirmed. `on_threshold_command` does not wake from idle - thresholds are driven by active usage, so they are checked when polling resumes after the user returns. Desktop notifications that occur during idle are deferred and shown when the user returns.

> [!TIP]
> If you need a visible terminal, prefix your command with `start cmd /c`, e.g.:
> ```
> "on_reset_command": "start cmd /c claude --continue"
> ```

> [!TIP]
> Use the **Test event commands** submenu in the tray context menu to fire your configured commands with sample data. This lets you verify your command and script setup without waiting for a real event.

## Examples

### Quota trigger - ping Claude the moment the 5h session resets

Send a minimal Claude Code request the instant the 5-hour quota resets so the
next cooldown window starts immediately, without waiting for you to send your
first real message. Using the variant object ensures the ping only fires on
session resets - not on weekly or Sonnet quota resets.

```json
{
  "on_reset_command": {
    "five_hour": "claude -p \"ok\" --tools \"\" --no-session-persistence --system-prompt \"Reply with only: ok\" --output-format text"
  }
}
```

The app is already idle-aware and aligns its polling to imminent reset times,
so the trigger fires within seconds of the actual reset even when the computer
has been idle.

### Resume a Claude Code session when the quota resets

```json
{
  "on_reset_command": "claude --continue -p \"Quota is available, resume task\""
}
```

`--continue` resumes the most recent conversation. Use `--resume <name>` to target a specific named session.

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

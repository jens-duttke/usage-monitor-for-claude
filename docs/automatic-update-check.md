# Automatic Update Check

The app itself never contacts GitHub - it communicates exclusively with `api.anthropic.com`. Update checking is available as an optional script that you run via [event commands](event-commands.md) - one for Windows, one for Linux.

The script queries the GitHub Releases API and shows a desktop notification if a newer release exists.

## Setup

### 1. Save the script

Save it next to your `UsageMonitorForClaude.exe`, or in the project root when running from source.

#### Windows

Save as `check-update.ps1`:

```powershell
$currentVersion = if ($env:USAGE_MONITOR_VERSION) { $env:USAGE_MONITOR_VERSION } else { '0.0.0' }

$releaseUrl = 'https://api.github.com/repos/jens-duttke/usage-monitor-for-claude/releases/latest'

try {
    $release = Invoke-RestMethod -Uri $releaseUrl -TimeoutSec 10
    $latest = $release.tag_name -replace '^v', ''

    if ([version]$latest -gt [version]$currentVersion) {
        # Windows requires a registered app ID for toast notifications
        $notifierAppId = 'Microsoft.Windows.ControlPanel'

        [Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] > $null
        [Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom, ContentType = WindowsRuntime] > $null

        $xml = [Windows.Data.Xml.Dom.XmlDocument]::new()
        $xml.LoadXml("<toast activationType='protocol' launch='$($release.html_url)'>
            <visual><binding template='ToastGeneric'>
                <text>Usage Monitor for Claude</text>
                <text>Version $latest available (current: $currentVersion)</text>
            </binding></visual>
        </toast>")

        [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($notifierAppId).Show(
            [Windows.UI.Notifications.ToastNotification]::new($xml)
        )
    }
}
catch {
    # Silently ignore - update check is optional
}
```

Clicking the notification opens the release page in your browser.

#### Linux

Save as `check-update.sh` and make it executable with `chmod +x check-update.sh`:

```sh
#!/usr/bin/env sh
set -eu

current="${USAGE_MONITOR_VERSION:-0.0.0}"
release_api='https://api.github.com/repos/jens-duttke/usage-monitor-for-claude/releases/latest'

# Any failure exits quietly - a failed update check must never disturb the app.
release=$(curl -fsS --max-time 10 "$release_api") || exit 0
info=$(printf '%s' "$release" | python3 -c \
    'import json, sys; r = json.load(sys.stdin); print(r["tag_name"].lstrip("v"), r["html_url"])') || exit 0

latest=${info%% *}
url=${info#* }

# sort -V orders version strings; stop when the newest of the two is the one running.
newest=$(printf '%s\n%s\n' "$current" "$latest" | sort -V | tail -n 1)
[ "$newest" = "$current" ] && exit 0

notify-send \
    --app-name='Usage Monitor for Claude' \
    --icon=software-update-available \
    'Usage Monitor for Claude' \
    "Version $latest available (current: $current)
$url"
```

The release page appears as a link in the notification. GNOME's notification server does not support clickable actions, so the URL goes into the message rather than behind a button.

Needs `curl`; `python3`, `sort` and `notify-send` are already there on any system that runs the app.

### 2. Configure the event command

Add the script to your [`usage-monitor-settings.json`](configuration.md). The app sets `USAGE_MONITOR_VERSION` for all event commands, so the script never needs a hardcoded version.

**Check on every quota reset** (session resets roughly every 5 hours):

```json
{
  "on_reset_command": "powershell -ExecutionPolicy Bypass -File .\\check-update.ps1"
}
```

On Linux:

```json
{
  "on_reset_command": "./check-update.sh"
}
```

**Check only on weekly resets** (once every 7 days):

```json
{
  "on_reset_command": "powershell -ExecutionPolicy Bypass -File .\\check-update.ps1 && if not \"%USAGE_MONITOR_VARIANT%\"==\"seven_day\" exit /b"
}
```

On Linux:

```json
{
  "on_reset_command": "[ \"$USAGE_MONITOR_VARIANT\" = seven_day ] && ./check-update.sh"
}
```

> [!NOTE]
> If you already have an `on_reset_command`, use an array to run both:
> ```json
> {
>   "on_reset_command": [
>     "your-existing-command",
>     "./check-update.sh"
>   ]
> }
> ```

### 3. Restart the app

Use the **Restart** option in the tray context menu to load the new settings.

## How it works

1. On each configured event, the app launches the script as a background process (no console window, no focus stealing)
2. The script sends a single HTTPS request to `https://api.github.com/repos/jens-duttke/usage-monitor-for-claude/releases/latest`
3. If the latest release tag is newer than `USAGE_MONITOR_VERSION`, a desktop notification appears
4. The notification carries the release page - on Windows behind a click, on Linux as a link in the message
5. If the request fails (no internet, API down, rate-limited), the script exits silently

## Customizing the notification appearance (Windows)

Toast notifications display the icon and name of a registered Windows app. The script uses `Microsoft.Windows.ControlPanel` by default, which shows as "Settings" with a gear icon.

To use a different app's appearance, change the `$notifierAppId` value in the script. Find available app IDs on your system with:

```powershell
Get-StartApps
```

Some examples:

| `$notifierAppId` | Appears as |
|---|---|
| `Microsoft.Windows.ControlPanel` | Settings (gear icon) |
| `{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe` | Windows PowerShell |

On Linux, change the `--icon` value to any icon name from your theme.

## Security notes

- The script is a plain text file - you can read and verify every line before using it
- The only network request goes to `api.github.com` (GitHub's public API, no authentication required)
- No data is sent to GitHub beyond the standard HTTPS request
- The script never writes files, modifies settings, or downloads executables
- Errors are silently ignored - a failed update check never affects the app

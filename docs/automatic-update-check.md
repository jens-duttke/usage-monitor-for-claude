# Automatic Update Check

The app itself never contacts GitHub - it communicates exclusively with `api.anthropic.com`. Update checking is available as an optional PowerShell script that you run via [event commands](event-commands.md).

The script queries the GitHub Releases API and shows a Windows toast notification if a newer release exists. Clicking it opens the download page in your browser.

## Setup

### 1. Save the script

Save the following as `check-update.ps1` next to your `UsageMonitorForClaude.exe` (or in the project root when running from source):

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

The app sets `USAGE_MONITOR_VERSION` for all event commands, so the script never needs a hardcoded version.

### 2. Configure the event command

Add the script to your [`usage-monitor-settings.json`](configuration.md). Choose when to check based on your preference:

**Check on every quota reset** (session resets roughly every 5 hours):

```json
{
  "on_reset_command": "powershell -ExecutionPolicy Bypass -File .\\check-update.ps1"
}
```

**Check only on weekly resets** (once every 7 days):

```json
{
  "on_reset_command": "powershell -ExecutionPolicy Bypass -File .\\check-update.ps1 && if not \"%USAGE_MONITOR_VARIANT%\"==\"seven_day\" exit /b"
}
```

> [!NOTE]
> If you already have an `on_reset_command`, use an array to run both:
> ```json
> {
>   "on_reset_command": [
>     "your-existing-command",
>     "powershell -ExecutionPolicy Bypass -File .\\check-update.ps1"
>   ]
> }
> ```

### 3. Restart the app

Use the **Restart** option in the tray context menu to load the new settings.

## How it works

1. On each configured event, the app launches the script as a background process (no console window, no focus stealing)
2. The script sends a single HTTPS request to `https://api.github.com/repos/jens-duttke/usage-monitor-for-claude/releases/latest`
3. If the latest release tag is newer than `USAGE_MONITOR_VERSION`, a toast notification appears
4. Clicking the notification opens the GitHub release page in your default browser
5. If the request fails (no internet, API down, rate-limited), the script exits silently

## Customizing the notification appearance

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

## Security notes

- The script is a plain text file - you can read and verify every line before using it
- The only network request goes to `api.github.com` (GitHub's public API, no authentication required)
- No data is sent to GitHub beyond the standard HTTPS request
- The script never writes files, modifies settings, or downloads executables
- Errors are silently ignored - a failed update check never affects the app

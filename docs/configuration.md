# Configuration

All settings work out of the box - no configuration file is needed. To customize behavior, create a file called `usage-monitor-settings.json` with only the keys you want to change:

```json
{
  "poll_interval": 180,
  "bar_fg": "#00cc66",
  "bar_fg_warn": "#ff6600"
}
```

The app searches for this file in these locations (first match wins):

1. **`$CLAUDE_CONFIG_DIR/usage-monitor-settings.json`** (only if a custom config directory is set via `--config-dir` or `CLAUDE_CONFIG_DIR` and differs from `~/.claude/`) - so each instance can have its own settings
2. **Next to the EXE** (or project root when running from source)
3. **`~/.claude/usage-monitor-settings.json`**

The app never creates or modifies this file. Settings are read at startup - after editing the file, use the **Restart** option in the tray context menu to apply changes.

## Alert thresholds

Configure usage percentage thresholds that trigger desktop notifications. Session and weekly quotas have separate thresholds since their time horizons differ significantly. Set to an empty array `[]` to disable alerts for a specific quota type.

| Key | Default | Description |
|-----|---------|-------------|
| `alert_thresholds_five_hour` | `[50, 80, 95]` | Thresholds (%) for Session (5hr) |
| `alert_thresholds_seven_day` | `[95]` | Thresholds (%) for Weekly quotas (7 day and all variants) |
| `alert_thresholds_extra_usage` | `[50, 80, 95]` | Thresholds (%) for Extra Usage (paid overage) |
| `alert_extra_usage_spent` | `[]` | Absolute Extra Usage spending amounts (in your billing currency, e.g. `[50, 100, 150]` for dollars) that trigger a notification - the only alert that works when extra usage has no monthly limit |
| `alert_time_aware` | `true` | Only alert when usage outpaces elapsed time |
| `alert_time_aware_below` | `90` | Time-aware check applies only to thresholds below this value; thresholds at or above always fire |

Threshold lookup uses a fallback chain: exact match (e.g. `alert_thresholds_seven_day_opus`), then base period (e.g. `alert_thresholds_seven_day`), then no alerts. This lets you configure stricter thresholds per variant when needed:

```json
{
    "alert_thresholds_seven_day_opus": [50, 80, 95]
}
```

## Work-hour pacing

By default the time marker advances around the clock, including nights and weekends. Set an active schedule to count only your working hours on quotas of a day or longer.

| Key | Default | Description |
|-----|---------|-------------|
| `active_schedule` | not set | Working hours per weekday in local 24-hour time. Not set means wall-clock pacing |
| `active_schedule_fields` | not set | Which quotas use the schedule. Not set means every quota of a day or longer; `[]` means none |

```json
{
    "active_schedule": {
        "mon": [9, 17],
        "tue": [[9, 12], [13, 17]],
        "wed": [9, 17],
        "thu": [9, 17],
        "fri": [9, 15]
    }
}
```

Use the weekday keys `mon` through `sun`. A missing weekday or `[]` has no working hours. A day takes one `[start, end]` slot, or a list of slots for a split day. Fractional hours are allowed: `9.5` means 09:30. A start must be from 0 to below 24, an end from 0 to 24. Start and end must differ - use `[0, 24]` for a full day. If the end is before the start, the slot runs past midnight: `"fri": [22, 6]` lasts until Saturday 06:00. Overlapping slots count once.

With a schedule, the popup marker, the red warning color, time-aware alerts and the tray icon marker (including `overage` mode) all use the share of working time that has passed. The marker stands still outside your working hours. The dividers mark the start of each working day instead of midnight; a lunch break adds no divider. Slots follow the local time of your computer, including daylight saving changes. If a quota period contains no working time at all, the bar falls back to wall-clock pacing and midnight dividers. Holidays and vacations are not supported. The five-hour session always uses wall-clock time.

To limit the schedule to particular quotas, list their names in `active_schedule_fields`. A `*` matches any part of a name. Unlike in `popup_fields`, `-field` exclusions are not available:

```json
{
    "active_schedule_fields": ["seven_day", "seven_day_*"]
}
```

## Update notification

When a background token refresh installs a new Claude CLI version, the app shows a desktop notification reporting the version change. Set this to `false` to suppress that notification.

| Key | Default | Description |
|-----|---------|-------------|
| `notify_claude_update` | `true` | Show a notification when a background token refresh installs a new Claude CLI version |

## Claude CLI command

The popup lists the Claude Code version of the natively installed CLI and of each IDE extension it finds. Installs it cannot see - most commonly a Claude Code running inside WSL - are missing from that list. Use `cli_command` to have their versions reported as well.

The value is an object mapping a display name to the base command as an array of arguments (the app appends `--version` itself). Each entry is listed in the popup under the name you give it, **in addition to** the native CLI and the IDE extensions.

| Key | Default | Description |
|-----|---------|-------------|
| `cli_command` | *(none)* | Object mapping a display name to a base command (array of strings) whose Claude Code version is reported alongside the auto-detected ones, e.g. a WSL install |

```json
{
    "cli_command": {
        "WSL": ["wsl", "/home/<user>/.local/bin/claude"]
    }
}
```

An entry only appears once its command reports a version, so if it stays missing, run the command yourself in a terminal - `wsl /home/<user>/.local/bin/claude --version` has to print a version number.

- **This setting is display only.** Automatic token refresh keeps using the natively installed CLI, whose credentials this app reads.
- **The version is read once per app start.** After updating Claude Code inside WSL, restart the app to see the new version.

## Tooltip fields

The tray tooltip shows a quick usage summary when you hover over the icon. By default, it displays the session (5h) and weekly (7d) quotas. Use `tooltip_fields` to choose which usage fields appear in the tooltip.

| Key | Default | Description |
|-----|---------|-------------|
| `tooltip_fields` | `["five_hour", "seven_day"]` | Which usage fields to show in the tray tooltip, in order |

Must be an array of non-empty strings. Duplicates are silently removed. An empty array `[]` is valid (tooltip shows only the title, no usage fields). Unknown field names are accepted. A field gives up its line when it is `null` or missing from the API response, for example the session quota on a plan without one, and also when it names a quota type that does not apply to your account. The next bar of the detail popup then takes the line, as for the [tray icon bars](#tray-icon-bars). If no other quota is left, the line is left out.

Each line shows the quota and its percentage, e.g. `5h: 42%` or `Sonnet 7d: 12%`. Reset times are in the detail popup. On Windows the tooltip holds at most 127 characters - lines that do not fit are left out.

**Known field names:** `five_hour`, `seven_day`, `seven_day_sonnet`, `seven_day_opus`, `seven_day_cowork`, `seven_day_oauth_apps`

**Example** - show session and Sonnet quota in the tooltip:

```json
{
    "tooltip_fields": ["five_hour", "seven_day_sonnet"]
}
```

## Popup fields

The popup shows usage bars for all active quota types by default. Use `popup_fields` to control which bars appear and in what order.

| Key | Default | Description |
|-----|---------|-------------|
| `popup_fields` | `["*"]` | Which usage fields to show in the popup, in order. `"*"` includes all remaining active quota types in default order; `"-field_name"` excludes a field |

Must be an array of non-empty strings. `"*"` may appear at most once. Duplicates are silently removed. Unknown field names are accepted. Bare `"-"` and `"-*"` are invalid. A field is skipped when it is `null` or missing from the API response, and also when it names a quota type that does not apply to your account - the API lists some under internal code names before they take effect.

Prefix a field name with `"-"` to hide its bar, for example `"-iguana_necktie"`. Exclusions match exact field names, not patterns, and override both explicit inclusions and `"*"` regardless of their position in the list. Unknown exclusions have no effect. A list containing only exclusions shows no bars; include `"*"` to keep the other active quota types.

**Known field names:** `five_hour`, `seven_day`, `seven_day_sonnet`, `seven_day_opus`, `seven_day_cowork`, `seven_day_oauth_apps`

**Default order** (used for `"*"` and when no setting is present): shorter periods first (`hour` before `day`), base field before variants, variants alphabetically.

**Examples:**

| Setting | Result |
|---------|--------|
| *(not set)* | All active quota types in default order |
| `["five_hour", "seven_day_sonnet", "*"]` | Session first, then Sonnet, then all remaining |
| `["five_hour", "seven_day"]` | Only these two, everything else hidden |
| `["*", "-iguana_necktie"]` | All active quota types except `iguana_necktie` |
| `["five_hour", "*", "-five_hour"]` | All active quota types except the session quota, even though it was explicitly included |
| `["*"]` | Same as not set |

```json
{
    "popup_fields": ["five_hour", "seven_day_sonnet", "*"]
}
```

## Compact pinned view

The detail popup can be pinned open (pin button in the header) so it stays visible and can be dragged anywhere. Use `compact_hide` to strip the pinned popup down to just the usage bars you care about - the entries listed here are hidden **only while the popup is pinned**, and reappear when you unpin it.

| Key | Default | Description |
|-----|---------|-------------|
| `compact_hide` | `[]` | Sections and usage bars to hide while the popup is pinned |

Must be an array of non-empty strings. Duplicates are silently removed. Unknown names are accepted and simply have no effect. With the default empty list, pinning changes nothing about what is shown.

Entries can be either a **section key** or a **usage field name**:

**Section keys:** `account` (email and plan), `extra_usage` (paid overage bar and prepaid credit balance), `claude_code` (installed versions), `status` (the footer with the update time). The usage bar section itself cannot be hidden as a whole - hide individual bars by their field name instead. When nothing but the usage bars is left, the "Usage" heading is dropped automatically.

**Usage field names:** any quota field, e.g. `five_hour`, `seven_day`, `seven_day_sonnet`, `seven_day_opus`, `seven_day_cowork`, `seven_day_oauth_apps`. This hides that single bar from the [`popup_fields`](#popup-fields) selection while the popup is pinned.

**Example** - pin to a minimal view with only the session and weekly bars:

```json
{
    "compact_hide": ["account", "extra_usage", "claude_code", "status", "seven_day_sonnet", "seven_day_opus"]
}
```

## Tray icon bars

The tray icon displays two small progress bars. By default, these show the session (5h) and weekly (7d) quotas. Use `icon_fields` to choose which two API fields are displayed, and `icon_style` to switch the icon layout.

| Key | Default | Description |
|-----|---------|-------------|
| `icon_fields` | `["five_hour", "seven_day"]` | Which two usage fields to show as icon bars. The first entry is the top bar (also determines the icon text), the second is the bottom bar |
| `icon_style` | `"number+bars"` | Icon layout: `"number+bars"` shows the first field's percentage above two progress bars; `"numbers"` shows both fields as two stacked percentages without bars |

Must be an array of exactly 2 non-empty strings. Unknown field names are accepted. A field gives up its place when it is `null` or missing from the API response, for example the session quota on a plan without one, and also when it names a quota type that does not apply to your account. The remaining field then moves to the top if needed, and the next bar of the detail popup fills the free place. A bar shows 0% only when no other quota is left.

**Known field names:** `five_hour`, `seven_day`, `seven_day_sonnet`, `seven_day_opus`, `seven_day_cowork`, `seven_day_oauth_apps`

Each entry can optionally include a display mode suffix using colon syntax: `"field_name:mode"`.

**Available bar display modes:**

| Mode | Description |
|------|-------------|
| `utilization` | *(default)* Fills left-to-right proportional to current usage |
| `overage` | Shows how far usage has entered the over-budget zone: empty when usage is at or below the time marker (on pace or ahead), half-filled when usage is halfway between the time marker and 100%, full when usage reaches 100% |

In `utilization` mode, each bar also shows a thin vertical marker at the elapsed-time position of the quota period - the same information as the time marker in the detail popup. When usage is ahead of the elapsed time (or fully exhausted), the bar fill switches to the warning color (`fg_warn` in [Tray icon colors](#tray-icon-colors)), matching the popup's red warning fill.

**The `"numbers"` style** replaces the bars with a second percentage: the first `icon_fields` entry becomes the top row, the second the bottom row. Each row follows the same rules as the classic icon text - an exhausted quota shows `✕` (or `$` when paid extra usage is still available), and when both are exhausted the icon collapses to a single full-size `✕`/`$`. The time marker, the warning color, and the `:overage` suffix have no effect in this style; while both quotas are at 0% the icon shows the usual idle "C".

**Example** - show session and weekly usage as two stacked percentages:

```json
{
    "icon_style": "numbers"
}
```

**Example** - show session in overage mode and weekly in default mode:

```json
{
    "icon_fields": ["five_hour:overage", "seven_day"]
}
```

**Example** - show session and Sonnet quota (default utilization mode):

```json
{
    "icon_fields": ["five_hour", "seven_day_sonnet"]
}
```

## Event commands

Run a shell command when a usage event occurs. See [Event Commands](event-commands.md) for examples and available environment variables.

| Key | Default | Description |
|-----|---------|-------------|
| `on_reset_command` | *(none)* | Shell command (or array of commands) to run when a quota resets (usage drops) |
| `on_startup_command` | *(none)* | Shell command (or array of commands) to run once after the first successful API update following app start |
| `on_threshold_command` | *(none)* | Shell command (or array of commands) to run when usage crosses a configured alert threshold |
| `quick_action_command` | *(none)* | Shell command (or array of commands) to run when you trigger the quick action (e.g. launch [Agent Monitor for Claude](https://github.com/jens-duttke/agent-monitor-for-claude)). Triggered by a double-click on the tray icon, or by the **Run Quick Action** menu entry where the desktop keeps the click. Formerly `on_double_click_command`, which still works |

## Polling intervals

| Key | Default | Description |
|-----|---------|-------------|
| `poll_interval` | `180` | Seconds between API updates |
| `poll_fast` | `120` | Seconds when usage is actively increasing |
| `poll_fast_extra` | `2` | Extra fast polls after usage stops increasing |
| `poll_error` | `30` | Seconds after a transient error (5xx, network). Rate-limit errors (429) use exponential backoff instead |
| `max_backoff` | `900` | Maximum backoff in seconds for rate-limit errors (15 min) |
| `idle_pause` | `300` | Seconds of inactivity before polling slows down to `idle_interval` (0 = disable). A locked workstation slows down immediately. An open detail popup keeps the normal cadence unless the lock screen or a screensaver covers it |
| `idle_interval` | `900` | Seconds between API updates while nobody is at the machine (15 min). Quota resets and account switches are still picked up as they happen |

## Language

| Key | Default | Description |
|-----|---------|-------------|
| `language` | *(auto-detected)* | Override the UI language with a language code. Available: `de`, `en`, `es`, `fr`, `hi`, `id`, `it`, `ja`, `ko`, `pt-BR`, `uk`, `zh-CN`, `zh-TW` |

## Time Format

By default, reset times follow your system's clock format (the 24-hour or 12-hour / AM-PM setting from your regional preferences), so no configuration is needed. Set this key to override the auto-detected format.

| Key | Default | Description |
|-----|---------|-------------|
| `time_format` | *(auto-detected from your system)* | Clock format for reset times: `"24h"` (e.g. `14:30`) or `"12h"` (e.g. `2:30 PM`) |

## Currency

The app shows extra usage amounts in the billing currency the Anthropic API reports for your account (its symbol and decimal precision), falling back to your system locale's currency symbol when the API does not report one. An override set here always wins. Number formatting (decimal separator, symbol position) always follows your system locale.

| Key | Default | Description |
|-----|---------|-------------|
| `currency_symbol` | *(from API, else locale)* | Override the displayed currency symbol (e.g., `"$"`, `"€"`, `"¥"`) |

## Tray icon colors

Override individual channels as RGBA arrays `[R, G, B, A]` (0-255). Unspecified keys keep their defaults.

| Key | Default | Description |
|-----|---------|-------------|
| `icon_light` | `{"fg": [255,255,255,255], "fg_half": [255,255,255,80], "fg_dim": [255,255,255,140], "fg_warn": [224,80,80,255]}` | Light icons for dark taskbar |
| `icon_dark` | `{"fg": [0,0,0,255], "fg_half": [0,0,0,80], "fg_dim": [0,0,0,140], "fg_warn": [224,80,80,255]}` | Dark icons for light taskbar |

## Popup colors

| Key | Default | Description |
|-----|---------|-------------|
| `bg` | `"#1e1e1e"` | Background |
| `fg` | `"#cccccc"` | Text |
| `fg_dim` | `"#888888"` | Dimmed text (labels, reset times) |
| `fg_heading` | `"#ffffff"` | Section headings |
| `fg_link` | `"#4a9eff"` | Link text (e.g. changelog) |
| `bar_bg` | `"#333333"` | Progress bar background |
| `bar_fg` | `"#4a9eff"` | Progress bar fill |
| `bar_fg_warn` | `"#e05050"` | Progress bar fill when usage outpaces elapsed time, error text |
| `bar_divider` | `"#000c"` | Time dividers on progress bars (hour marks on the session bar, midnights on weekly bars, or workday starts when an active schedule applies) |
| `bar_marker` | `"#fffc"` | Time-position marker on progress bars |

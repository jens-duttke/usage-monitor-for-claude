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

1. **Next to the EXE** (or project root when running from source)
2. **`$CLAUDE_CONFIG_DIR/usage-monitor-settings.json`** (only if `CLAUDE_CONFIG_DIR` is set and differs from `~/.claude/`)
3. **`~/.claude/usage-monitor-settings.json`**

The app never creates or modifies this file. To start, create an empty file and add keys as needed. Settings are read at startup - after editing the file, use the **Restart** option in the tray context menu to apply changes.

## Alert thresholds

Configure usage percentage thresholds that trigger Windows notifications. Session and weekly quotas have separate thresholds since their time horizons differ significantly. Set to an empty array `[]` to disable alerts for a specific quota type.

| Key | Default | Description |
|-----|---------|-------------|
| `alert_thresholds_five_hour` | `[50, 80, 95]` | Thresholds (%) for Session (5hr) |
| `alert_thresholds_seven_day` | `[95]` | Thresholds (%) for Weekly quotas (7 day and all variants) |
| `alert_thresholds_extra_usage` | `[50, 80, 95]` | Thresholds (%) for Extra Usage (paid overage) |
| `alert_time_aware` | `true` | Only alert when usage outpaces elapsed time |
| `alert_time_aware_below` | `90` | Time-aware check applies only to thresholds below this value; thresholds at or above always fire |

Threshold lookup uses a fallback chain: exact match (e.g. `alert_thresholds_seven_day_opus`), then base period (e.g. `alert_thresholds_seven_day`), then no alerts. This lets you configure stricter thresholds per variant when needed:

```json
{
    "alert_thresholds_seven_day_opus": [50, 80, 95]
}
```

## Tooltip fields

The tray tooltip shows a quick usage summary when you hover over the icon. By default, it displays the session (5h) and weekly (7d) quotas. Use `tooltip_fields` to choose which usage fields appear in the tooltip.

| Key | Default | Description |
|-----|---------|-------------|
| `tooltip_fields` | `["five_hour", "seven_day"]` | Which usage fields to show in the tray tooltip, in order |

Must be an array of non-empty strings. Duplicates are silently removed. An empty array `[]` is valid (tooltip shows only the title, no usage fields). Unknown field names are accepted - if a field is `null` or missing from the API response, it is simply skipped.

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
| `popup_fields` | `["*"]` | Which usage fields to show in the popup, in order. `"*"` is a wildcard meaning "all remaining non-null fields in default order" |

Must be an array of non-empty strings. `"*"` may appear at most once. Duplicates are silently removed. Unknown field names are accepted - if a field is `null` or missing from the API response, it is simply skipped.

**Known field names:** `five_hour`, `seven_day`, `seven_day_sonnet`, `seven_day_opus`, `seven_day_cowork`, `seven_day_oauth_apps`

**Default order** (used for `"*"` and when no setting is present): shorter periods first (`hour` before `day`), base field before variants, variants alphabetically.

**Examples:**

| Setting | Result |
|---------|--------|
| *(not set)* | All non-null fields in default order |
| `["five_hour", "seven_day_sonnet", "*"]` | Session first, then Sonnet, then all remaining |
| `["five_hour", "seven_day"]` | Only these two, everything else hidden |
| `["*"]` | Same as not set |

```json
{
    "popup_fields": ["five_hour", "seven_day_sonnet", "*"]
}
```

## Tray icon bars

The tray icon displays two small progress bars. By default, these show the session (5h) and weekly (7d) quotas. Use `icon_fields` to choose which two API fields are displayed.

| Key | Default | Description |
|-----|---------|-------------|
| `icon_fields` | `["five_hour", "seven_day"]` | Which two usage fields to show as icon bars. The first entry is the top bar (also determines the icon text), the second is the bottom bar |

Must be an array of exactly 2 non-empty strings. Unknown field names are accepted - if a field is `null` or missing from the API response, the bar shows 0%.

**Known field names:** `five_hour`, `seven_day`, `seven_day_sonnet`, `seven_day_opus`, `seven_day_cowork`, `seven_day_oauth_apps`

Each entry can optionally include a display mode suffix using colon syntax: `"field_name:mode"`.

**Available bar display modes:**

| Mode | Description |
|------|-------------|
| `utilization` | *(default)* Fills left-to-right proportional to current usage |
| `overage` | Shows how far usage has entered the over-budget zone: empty when usage is at or below the time marker (on pace or ahead), half-filled when usage is halfway between the time marker and 100%, full when usage reaches 100% |

In `utilization` mode, each bar also shows a thin vertical marker at the elapsed-time position of the quota period - the same information as the time marker in the detail popup. When usage is ahead of the elapsed time (or fully exhausted), the bar fill switches to the warning color (`fg_warn` in [Tray icon colors](#tray-icon-colors)), matching the popup's red warning fill.

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

## Polling intervals

| Key | Default | Description |
|-----|---------|-------------|
| `poll_interval` | `180` | Seconds between API updates |
| `poll_fast` | `120` | Seconds when usage is actively increasing |
| `poll_fast_extra` | `2` | Extra fast polls after usage stops increasing |
| `poll_error` | `30` | Seconds after a transient error (5xx, network). Rate-limit errors (429) use exponential backoff instead |
| `max_backoff` | `900` | Maximum backoff in seconds for rate-limit errors (15 min) |
| `idle_pause` | `300` | Seconds of inactivity before polling pauses (0 = disable). Polling also pauses when the workstation is locked |

## Language

| Key | Default | Description |
|-----|---------|-------------|
| `language` | *(auto-detected)* | Override the UI language with a language code. Available: `de`, `en`, `es`, `fr`, `hi`, `id`, `it`, `ja`, `ko`, `pt-BR`, `uk`, `zh-CN`, `zh-TW` |

## Currency

The Anthropic API does not include currency information, so the app detects the currency symbol from your Windows locale settings. If your Windows locale currency differs from the currency Anthropic bills you in, you can override just the symbol here. Number formatting (decimal separator, symbol position) always follows your system locale.

| Key | Default | Description |
|-----|---------|-------------|
| `currency_symbol` | *(auto-detected)* | Override the auto-detected currency symbol (e.g., `"$"`, `"€"`, `"¥"`) |

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
| `bar_divider` | `"#000c"` | Midnight divider on weekly progress bars |
| `bar_marker` | `"#fffc"` | Time-position marker on progress bars |

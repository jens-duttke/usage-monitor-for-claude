# MCP Server

While the app is running, Claude Code and Claude Desktop can ask it how much of your quota is left. A built-in [MCP](https://modelcontextprotocol.io) server offers one tool for this, `get_usage`. It is off until you turn it on.

## Setup

Turn the server on in your [settings file](configuration.md#mcp-server), then choose **Restart** in the tray menu:

```json
{
  "mcp_server": true
}
```

Claude starts the app's EXE a second time with `--mcp`. That process opens no window, passes Claude's requests on to the running app and ends when Claude closes it.

If the app is not running or `mcp_server` is off, the tool reports an error. Turn on **Start at login** in the tray menu so the app is always running when Claude asks.

### Claude Code

Run this once, with the path to your EXE:

```bash
claude mcp add usage-monitor-for-claude --scope user -- "C:\Path\To\UsageMonitorForClaude.exe" --mcp
```

New Claude Code sessions then have the tool.

### Claude Desktop

Add this to `claude_desktop_config.json` (**Settings > Developer > Edit Config**), with the path to your EXE:

```json
{
  "mcpServers": {
    "usage-monitor-for-claude": {
      "command": "C:\\Path\\To\\UsageMonitorForClaude.exe",
      "args": ["--mcp"]
    }
  }
}
```

If your file already has an `mcpServers` section, add the entry inside it. Then quit Claude Desktop completely and start it again.

### Running from source

Use the Python interpreter of your virtual environment as the command (`pythonw.exe` on Windows, so no console window opens), with the arguments `-m usage_monitor_for_claude --mcp` and the environment variable `PYTHONPATH` set to the checkout:

```bash
claude mcp add usage-monitor-for-claude --scope user --env PYTHONPATH=<checkout> -- <checkout>/.venv/Scripts/pythonw.exe -m usage_monitor_for_claude --mcp
```

In `claude_desktop_config.json` the same goes into `"command"`, `"args"` and `"env"`.

### Several accounts

If you run one instance per account with [`--config-dir`](../README.md#features), each instance answers for its own account. Add `--config-dir` to the `--mcp` command with the same path, e.g. `--mcp "--config-dir=C:\Users\me\.claude-work"`, and register it under a name of its own.

Without `--config-dir`, the `--mcp` process follows the `CLAUDE_CONFIG_DIR` environment variable. A Claude Code started for another account therefore reaches that account's instance by itself. To always reach the default account, add `--config-dir` with the path of your default config directory (usually `C:\Users\<name>\.claude`).

### Letting Claude use it

Claude does not call the tool on its own initiative every time. If you want it checked before expensive work, say so in your `CLAUDE.md`:

```markdown
Before starting expensive work such as launching several agents, call get_usage and tell me if the quota looks tight.
```

## What `get_usage` returns

The numbers come from the app's last update, so calling the tool sends no extra request to Anthropic.

```json
{
  "quotas": [
    {
      "field": "five_hour",
      "used_percent": 42.0,
      "remaining_percent": 58.0,
      "resets_at": "2026-11-02T19:00:00+01:00",
      "resets_in_minutes": 95,
      "expected_percent": 60.0,
      "vs_expected": -18.0
    }
  ],
  "data_age_seconds": 74,
  "error": null
}
```

| Field | Meaning |
|---|---|
| `field` | Name of the quota in the API response, e.g. `five_hour`, `seven_day`, `seven_day_sonnet` |
| `used_percent` | How much of the quota is used. 100 means it is used up |
| `remaining_percent` | How much is left, never below 0 |
| `resets_at` | When the quota resets, in your local time. `null` if no period is running |
| `resets_in_minutes` | Minutes until that reset. `null` if no period is running. Negative if the reset time has passed but the app has not fetched the new period yet. `used_percent` then still belongs to the period that just ended |
| `expected_percent` | Position of the time marker on the bar. It counts only your [working hours](configuration.md#work-hour-pacing) if they apply to this quota. `null` if unknown |
| `vs_expected` | `used_percent` minus `expected_percent`. Above 0 means you are ahead of the marker. `null` if unknown |
| `data_age_seconds` | Age of the numbers in seconds. `null` before the first successful update |
| `error` | `null` if the last update succeeded. Otherwise the error message from the popup, e.g. an expired login. The numbers are then from the last successful update, and `data_age_seconds` tells how old they are |

The tool lists the same quotas as the popup, in the same order. Whatever you leave out or exclude with [`popup_fields`](configuration.md#popup-fields) is missing here too.

## Access

The `--mcp` process reaches the app over a channel of your operating system that only your own user account can open: a named pipe on Windows, a socket file in your session's runtime directory on Linux. The app opens it only while `mcp_server` is on. Programs running under your account can read the data listed above. The tool returns nothing else: no token, no email address, no account name.

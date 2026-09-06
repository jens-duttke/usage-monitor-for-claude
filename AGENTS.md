# AGENTS.md - Claude&CodexUsage
<!-- Project-level instructions for AI coding agents -->

## Overview
* Desktop tray application monitoring Claude Code quota utilization, reset countdowns, and prepaid balances.
* Supported operating systems: Windows 10 and Windows 11 (64-bit). macOS is not supported; a community fork covers it.
* Core technologies: Python (standard library + minimal dependencies: requests, Pillow, pystray, truststore, pywebview, pyinstaller).

## Repository Structure
* `usage_monitor_for_claude/`: Core application package.
  * `app.py`: Application lifecycle, tray icon coordination, polling loop, event triggers.
  * `api.py`: Anthropic API client, credential parsing, TLS store injection via truststore.
  * `cache.py`: Quota data caching, reset alignment, rate limit backoff.
  * `claude_cli.py`: Local and WSL Claude Code CLI discovery, version probing.
  * `command.py`: Event command execution and environment setup.
  * `formatting.py`: Formatting for percentages, dates, duration, currencies.
  * `i18n.py`: Locale and translation loader (`locale/*.json`).
  * `instance_id.py`: Multi-instance identifier derivation and validation.
  * `popup.py`: Webview detail window lifecycle and API bridge.
  * `settings.py`: Configuration persistence and default settings.
  * `tray_icon.py`: Pystray tray icon rendering, menu dispatch.
  * `verbose.py`: Diagnostics and logging helpers.
  * `platforms/`: Windows-specific system abstractions.
    * `__init__.py`: Re-exports the Windows platform API.
    * `win32.py`: System integration (autostart, idle time, lock state, notifications, theme).
    * `instance_win32.py`: Process single-instance locking.
    * `popup_win32.py`: Host window setup, DPI calculation, and placement.
  * `popup/`: HTML, CSS, JS frontend assets embedded into pywebview.
* `tests/`: Test suite using standard library `unittest`.
* `locale/`: JSON translation files.
* `docs/`: Technical and end-user documentation.
* `build.py`: Standalone PyInstaller build script (`dist/Claude&CodexUsage.exe`).

## Critical Architectural Constraints

### 1. Platform Boundary Isolation
* Windows-only APIs (`ctypes.windll`, `winreg`, `msvcrt`, and `subprocess.CREATE_NO_WINDOW`) stay inside `usage_monitor_for_claude/platforms/`.
* The platform package has no runtime OS dispatch: this fork targets Windows exclusively.

### 2. Popup Window & DPI Scaling
* Windows (`popup_win32.py`):
  -> pywebview 6.x `resize()` and `move()` take logical pixels (scaled internally by pywebview).
  -> `_tray_position()` computes using physical screen coordinates but must return logical coordinates for `move()`.
  -> Never use WinForms `ShowInTaskbar = False` (recreates the native HWND and crashes WebView2).
  -> Never replace tray-anchored `move()` with raw `SetWindowPos` (only the pinned-popup drag uses `SetWindowPos` to avoid multi-DPI jitter).
* The Windows popup host uses pywebview 6.x with a WinForms host and Edge WebView2.

### 3. Security & Auditing
* Network access is strictly restricted to `api.anthropic.com` (and Windows certificate chain verification).
* API endpoints must remain top-level constants; no dynamic endpoint generation.
* Credentials must only be used in HTTP Authorization headers; never log, persist, or expose tokens.
* No dynamic execution: never use `eval()`, `exec()`, `compile()`, or dynamic imports.
* Persistent state:
  -> Windows: only `HKCU` registry entries for notification identity and autostart.

### 4. Quota Parsing & Polling Logic
* Do not hardcode API quota keys. Detect quotas dynamically by inspecting dict entries containing `utilization` and `resets_at`.
* Safe dictionary traversal: always use `(data.get('key') or {})` instead of `data.get('key', {})` to avoid `None` on explicit null fields.
* Polling cadence:
  -> Enforce `POLL_FAST` cooldown floor; `_align_to_reset()` must never return an interval below `POLL_FAST`.
  -> Never schedule discretionary polls inside the danger window (`POLL_FAST - RESET_BUFFER` prior to a reset).
  -> Idle mode slows polling to `IDLE_INTERVAL` (15 minutes) without freezing the scheduler loop.

### 5. Claude CLI & Event Commands
* `cli_command` configuration (WSL / custom paths) is strictly for display/version probing; never route token refresh through it.
* Subprocess execution in `claude_cli.py` must use `_run_cli()` with `encoding='utf-8', errors='replace'`.
* Background event commands run silent; user-initiated test commands capture output and display errors on failure.

## Code Style & Conventions
* Formatting: PEP 8 with line length extended to 140-160 characters.
* String quotes: Single quotes (`'`) preferred, double quotes (`"`) when containing single quotes.
* Punctuation: Use ASCII hyphens (`-`) for dashes; never use em dashes (`—`) or en dashes (`–`).
* Python typing:
  -> Module docstring as the first element in the file.
  -> `from __future__ import annotations` as the first import.
  -> Numpydoc docstrings for public classes and non-trivial functions.
  -> Avoid inline type documentation in docstrings; use Python type signatures.
* Coding paradigm: Favor modular/functional design over deep inheritance hierarchies.

## Testing & Verification
* Run the test suite:
  ```bash
  python -m unittest discover -s tests
  ```
* All changes must maintain 100% test suite pass rate without weakening existing assertions.
* Unit tests mock time-sensitive logic by patching `datetime` in the target module.
* Never test Windows console or GUI redirection solely against mocked ctypes; verify against real interpreter subprocesses.

## Operational Rules for Agents
* Never commit secrets, tokens, or private data.
* Do not generate commits or push tags directly; provide commit messages for manual user execution.
* Never use emojis in documentation, commit proposals, or code comments. Use text markers (`//`, `~`, `->`, `*`).
* Keep edits focused and minimal. Avoid modifying unrelated files or introducing unnecessary abstractions.

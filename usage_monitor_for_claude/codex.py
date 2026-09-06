"""
Codex Usage Provider
====================

Reads the Codex CLI app-server rate-limit snapshot without touching its
credential file.  The app-server owns authentication and returns the same
account-level data shown by ``/status`` in the interactive CLI.
"""
from __future__ import annotations

import json
import math
import os
import queue
import shutil
import subprocess
import threading
import time
from datetime import datetime, timezone
from typing import Any

__all__ = ['fetch_codex_usage']

_REQUEST_TIMEOUT = 10.0


def fetch_codex_usage() -> dict[str, Any] | None:
    """Return Codex ChatGPT plan quotas, or ``None`` when unavailable.

    Returns
    -------
    dict
        Normalized ``five_hour`` and ``seven_day`` quota entries, plus plan
        and credit metadata when the Codex CLI supplies them.
    None
        If Codex is not installed, is not authenticated, or its app-server
        protocol cannot be queried.
    """
    command = _codex_command()
    if command is None:
        return None

    process: subprocess.Popen[str] | None = None
    try:
        process = subprocess.Popen(
            [*command, 'app-server', '--stdio'],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding='utf-8',
            errors='replace',
        )
        response = _request_rate_limits(process)
    except (OSError, subprocess.SubprocessError, ValueError, json.JSONDecodeError):
        return None
    finally:
        if process is not None:
            _cleanup_process(process)

    return _normalize_response(response)


def _cleanup_process(process: subprocess.Popen[str]) -> None:
    """Stop an app-server process without allowing cleanup to block polling."""
    try:
        process.kill()
    except (OSError, subprocess.SubprocessError):
        pass

    try:
        process.wait(timeout=1.0)
    except subprocess.TimeoutExpired:
        try:
            process.kill()
        except (OSError, subprocess.SubprocessError):
            pass
        try:
            process.wait(timeout=1.0)
        except (OSError, subprocess.SubprocessError):
            pass
    except (OSError, subprocess.SubprocessError):
        pass

    for stream in (process.stdin, process.stdout, process.stderr):
        if stream is None:
            continue
        try:
            stream.close()
        except (OSError, ValueError):
            pass


def _codex_command() -> list[str] | None:
    """Locate the Codex executable without invoking a shell."""
    executable = shutil.which('codex')
    if executable:
        return [executable]

    local_app_data = os.environ.get('LOCALAPPDATA')
    if local_app_data:
        candidate = os.path.join(local_app_data, 'Programs', 'OpenAI', 'Codex', 'bin', 'codex.exe')
        if os.path.isfile(candidate):
            return [candidate]

    return None


def _request_rate_limits(process: subprocess.Popen[str]) -> dict[str, Any]:
    """Perform the minimal JSON-RPC handshake and rate-limit request."""
    assert process.stdin is not None
    assert process.stdout is not None

    messages = [
        {
            'jsonrpc': '2.0',
            'id': 1,
            'method': 'initialize',
            'params': {
                'clientInfo': {'name': 'usage-monitor-for-claude', 'version': '1.0'},
                'capabilities': {'experimentalApi': True},
            },
        },
        {'jsonrpc': '2.0', 'method': 'initialized', 'params': {}},
        {'jsonrpc': '2.0', 'id': 2, 'method': 'account/rateLimits/read', 'params': {}},
    ]
    for message in messages:
        process.stdin.write(json.dumps(message) + '\n')
    process.stdin.flush()

    lines: queue.Queue[str] = queue.Queue()

    def read_lines() -> None:
        for line in process.stdout:
            lines.put(line)

    threading.Thread(target=read_lines, daemon=True).start()
    deadline = time.monotonic() + _REQUEST_TIMEOUT
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError('Codex app-server rate-limit request timed out')
        try:
            line = lines.get(timeout=remaining)
        except queue.Empty as exc:
            raise TimeoutError('Codex app-server rate-limit request timed out') from exc
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(message, dict):
            continue
        if message.get('id') == 2:
            result = message.get('result')
            return result if isinstance(result, dict) else {}


def _normalize_response(response: dict[str, Any]) -> dict[str, Any] | None:
    """Convert the app-server response to the popup quota shape."""
    if not isinstance(response, dict):
        return None

    limits = response.get('rateLimitsByLimitId')
    snapshot = limits.get('codex') if isinstance(limits, dict) else None
    if not isinstance(snapshot, dict):
        snapshot = response.get('rateLimits')
    if not isinstance(snapshot, dict):
        return None

    normalized: dict[str, Any] = {}
    for source_key, target_key in (('primary', 'five_hour'), ('secondary', 'seven_day')):
        window = snapshot.get(source_key)
        if not isinstance(window, dict):
            continue
        used_percent = window.get('usedPercent')
        resets_at = window.get('resetsAt')
        if (
            not isinstance(used_percent, (int, float))
            or isinstance(used_percent, bool)
            or not isinstance(resets_at, (int, float))
            or isinstance(resets_at, bool)
        ):
            continue

        try:
            used_percent = float(used_percent)
            resets_at = float(resets_at)
            if not math.isfinite(used_percent) or not math.isfinite(resets_at):
                continue
            reset_text = datetime.fromtimestamp(resets_at, timezone.utc).isoformat().replace('+00:00', 'Z')
        except (OverflowError, OSError, ValueError):
            continue

        normalized[target_key] = {
            'utilization': max(0.0, min(100.0, float(used_percent))),
            'resets_at': reset_text,
        }

    if not normalized:
        return None

    normalized['codex_plan'] = snapshot.get('planType') or ''
    credits = snapshot.get('credits')
    if isinstance(credits, dict):
        normalized['codex_credits'] = credits
    return normalized

#!/usr/bin/env python3
"""ReMe hooks for Codex: capture stable hook fields and record via ReMe MCP.

This file deliberately keeps the transport/lifecycle shape of ReMe's official
Claude Code ``hooks/auto_memory.py`` wherever Codex allows it. The upstream
hook reads ``session_id`` and asks the ReMe server to resolve Claude's
transcript with ``auto_memory_cc``. Codex does not expose a stable transcript
format, so the Codex-specific adapter captures the documented
``UserPromptSubmit.prompt`` and ``Stop.last_assistant_message`` fields, then
calls ReMe's generic ``auto_memory`` tool with the resulting message list.

Recording is best-effort and never blocks Codex on the slow ReMe inner-agent
run. POSIX uses the same double-fork strategy as the official ReMe hook.
On Windows, every process owned by this adapter is launched without a console
window. Codex Desktop itself currently has an upstream console-flash limitation
for command hooks; see README.md for the exact boundary of this guarantee.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Kept from the official ReMe Claude Code hook: auto_memory drives an inner
# agent, so the detached call gets a generous ceiling.
_CALL_TIMEOUT = 600
_STATE_VERSION = 1
_MAX_TURNS = 500


def _plugin_root() -> str:
    """Resolve plugin root using Codex first, Claude-compatible env second."""
    return (
        os.environ.get("PLUGIN_ROOT")
        or os.environ.get("CLAUDE_PLUGIN_ROOT")
        or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    )


def _data_root() -> str:
    """Return a writable data directory; use host plugin-data env when available."""
    explicit = os.environ.get("PLUGIN_DATA") or os.environ.get("CLAUDE_PLUGIN_DATA")
    if explicit:
        return explicit
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home())
        return os.path.join(base, "ReMeCodex")
    xdg = os.environ.get("XDG_STATE_HOME")
    if xdg:
        return os.path.join(xdg, "reme-codex")
    return os.path.join(str(Path.home()), ".local", "state", "reme-codex")


def _server_url() -> str:
    """ReMe MCP endpoint. Prefer bundled .mcp.json so it stays in sync."""
    mcp_json = os.path.join(_plugin_root(), ".mcp.json")
    try:
        with open(mcp_json, encoding="utf-8") as f:
            url = json.load(f)["mcpServers"]["reme"]["url"]
            if url:
                return url
    except Exception:
        pass
    host = os.environ.get("REME_HOST", "127.0.0.1")
    port = os.environ.get("REME_PORT", "2333")
    return f"http://{host}:{port}/mcp"


def _log(session_id: str, status: str, detail: str = "") -> None:
    """Best-effort file logging; never surface failures to Codex."""
    try:
        log_dir = os.path.join(_data_root(), "logs")
        os.makedirs(log_dir, exist_ok=True)
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        line = f"{stamp} session={session_id} {status}"
        if detail:
            line += f" {detail}"
        with open(os.path.join(log_dir, "auto_memory_hook.log"), "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def _post(url: str, body: dict, headers: dict) -> "urllib.request.addinfourl":
    """Official ReMe hook transport helper, retained intentionally."""
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    return urllib.request.urlopen(req, timeout=_CALL_TIMEOUT)


def _read_jsonrpc(resp) -> dict | None:
    """Return the JSON-RPC envelope from a JSON or text/event-stream response."""
    ctype = resp.headers.get("content-type", "")
    body = resp.read().decode("utf-8", "replace")
    if "text/event-stream" in ctype:
        result = None
        for line in body.splitlines():
            line = line.strip()
            if not line.startswith("data:"):
                continue
            try:
                obj = json.loads(line[len("data:") :].strip())
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict) and ("result" in obj or "error" in obj):
                result = obj
        return result
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        return None


def _mcp_call(url: str, tool: str, arguments: dict) -> dict | None:
    """Minimal MCP Streamable HTTP client: initialize -> initialized -> tools/call.

    This is the same three-step client shape used by ReMe's official Claude Code
    auto-memory hook. Only clientInfo.name differs for observability.
    """
    base = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
    init = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "reme-codex-stop-hook", "version": "1.0"},
        },
    }
    with _post(url, init, base) as resp:
        mcp_session = resp.headers.get("mcp-session-id")
        _read_jsonrpc(resp)
    headers = dict(base)
    if mcp_session:
        headers["mcp-session-id"] = mcp_session

    try:
        with _post(
            url,
            {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
            headers,
        ) as resp:
            resp.read()
    except urllib.error.HTTPError:
        pass

    call = {
        "jsonrpc": "2.0",
        "id": 2,
        "method": "tools/call",
        "params": {"name": tool, "arguments": arguments},
    }
    with _post(url, call, headers) as resp:
        return _read_jsonrpc(resp)


def _daemonize() -> None:
    """Double-fork + setsid, retained from ReMe's official Claude Code hook."""
    if os.fork() > 0:
        os._exit(0)
    os.setsid()
    if os.fork() > 0:
        os._exit(0)
    devnull = os.open(os.devnull, os.O_RDWR)
    for fd in (0, 1, 2):
        os.dup2(devnull, fd)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _session_key(session_id: str) -> str:
    return hashlib.sha256(session_id.encode("utf-8", "replace")).hexdigest()


def _state_path(session_id: str) -> str:
    state_dir = os.path.join(_data_root(), "sessions")
    os.makedirs(state_dir, exist_ok=True)
    return os.path.join(state_dir, _session_key(session_id) + ".json")


def _jobs_dir() -> str:
    path = os.path.join(_data_root(), "jobs")
    os.makedirs(path, exist_ok=True)
    return path


def _load_json(path: str, default: Any) -> Any:
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _atomic_write_json(path: str, payload: Any) -> None:
    """Write state atomically so a detached worker never sees partial JSON."""
    directory = os.path.dirname(path)
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".reme-codex-", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass


def _new_state(session_id: str) -> dict[str, Any]:
    return {"version": _STATE_VERSION, "session_id": session_id, "turns": []}


def _load_state(session_id: str) -> dict[str, Any]:
    path = _state_path(session_id)
    state = _load_json(path, _new_state(session_id))
    if not isinstance(state, dict) or state.get("session_id") != session_id:
        return _new_state(session_id)
    if not isinstance(state.get("turns"), list):
        state["turns"] = []
    return state


def _find_turn(state: dict[str, Any], turn_id: str) -> dict[str, Any] | None:
    for turn in reversed(state.get("turns", [])):
        if isinstance(turn, dict) and turn.get("turn_id") == turn_id:
            return turn
    return None


def _capture_prompt(payload: dict[str, Any]) -> None:
    session_id = str(payload.get("session_id") or "").strip()
    turn_id = str(payload.get("turn_id") or "").strip()
    prompt = payload.get("prompt")
    if not session_id or not turn_id or not isinstance(prompt, str) or not prompt.strip():
        return

    state = _load_state(session_id)
    turn = _find_turn(state, turn_id)
    if turn is None:
        turn = {"turn_id": turn_id}
        state["turns"].append(turn)
    turn["prompt"] = prompt
    turn["prompt_at"] = _now_iso()
    # Bound local state. A long Codex session should not grow the hook file forever.
    state["turns"] = state["turns"][-_MAX_TURNS:]
    _atomic_write_json(_state_path(session_id), state)


def _messages_from_state(state: dict[str, Any]) -> list[dict[str, str]]:
    messages: list[dict[str, str]] = []
    for turn in state.get("turns", []):
        if not isinstance(turn, dict):
            continue
        prompt = turn.get("prompt")
        answer = turn.get("assistant")
        if not isinstance(prompt, str) or not prompt.strip():
            continue
        if not isinstance(answer, str) or not answer.strip():
            continue
        # Keep the MCP payload at ReMe's documented generic auto_memory shape:
        # role + content only. Local timestamps remain in our private state file.
        messages.append({"role": "user", "content": prompt})
        messages.append({"role": "assistant", "content": answer})
    return messages


def _make_job(session_id: str, messages: list[dict[str, str]]) -> str:
    job = {"session_id": session_id, "messages": messages, "created_at": _now_iso()}
    fd, path = tempfile.mkstemp(prefix="memory-", suffix=".json", dir=_jobs_dir())
    os.close(fd)
    _atomic_write_json(path, job)
    return path


def _record_stop(payload: dict[str, Any]) -> str | None:
    session_id = str(payload.get("session_id") or "").strip()
    turn_id = str(payload.get("turn_id") or "").strip()
    assistant = payload.get("last_assistant_message")
    if not session_id or not turn_id or not isinstance(assistant, str) or not assistant.strip():
        return None

    state = _load_state(session_id)
    turn = _find_turn(state, turn_id)
    if turn is None:
        # UserPromptSubmit should precede Stop. Do not guess from Codex's unstable
        # transcript_path if it did not; log and preserve best-effort semantics.
        _log(session_id, "skip-no-prompt", f"turn={turn_id}")
        return None
    turn["assistant"] = assistant
    turn["assistant_at"] = _now_iso()
    _atomic_write_json(_state_path(session_id), state)

    messages = _messages_from_state(state)
    if not messages:
        return None
    return _make_job(session_id, messages)


def _process_job(job_path: str) -> None:
    job = _load_json(job_path, {})
    session_id = str(job.get("session_id") or "") if isinstance(job, dict) else ""
    messages = job.get("messages") if isinstance(job, dict) else None
    if not session_id or not isinstance(messages, list) or not messages:
        try:
            os.unlink(job_path)
        except OSError:
            pass
        return

    url = _server_url()
    try:
        result = _mcp_call(url, "auto_memory", {"session_id": session_id, "messages": messages})
        if result is None:
            _log(session_id, "no-response")
        elif "error" in result:
            _log(session_id, "error", json.dumps(result["error"], ensure_ascii=False)[:500])
        else:
            _log(session_id, "ok")
    except urllib.error.URLError as exc:
        _log(session_id, "unreachable", str(getattr(exc, "reason", exc)))
    except Exception as exc:  # noqa: BLE001 - best-effort, never surface
        _log(session_id, "exception", repr(exc)[:500])
    finally:
        try:
            os.unlink(job_path)
        except OSError:
            pass


def _spawn_windows_worker(job_path: str) -> None:
    """Start the adapter-owned Windows worker without creating a console."""
    script = os.path.abspath(__file__)
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
    startupinfo = None
    if hasattr(subprocess, "STARTUPINFO"):
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= getattr(subprocess, "STARTF_USESHOWWINDOW", 1)
        startupinfo.wShowWindow = 0
    subprocess.Popen(
        [sys.executable, script, "--worker", job_path],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
        creationflags=creationflags,
        startupinfo=startupinfo,
        cwd=_plugin_root(),
    )


def _dispatch_job(job_path: str) -> None:
    """Detach the slow ReMe call, matching upstream behavior on POSIX."""
    if os.name == "nt":
        try:
            _spawn_windows_worker(job_path)
        except Exception as exc:  # noqa: BLE001
            job = _load_json(job_path, {})
            _log(str(job.get("session_id") or ""), "worker-spawn-error", repr(exc)[:500])
        return
    if hasattr(os, "fork"):
        _daemonize()
        _process_job(job_path)
        os._exit(0)
    # Unusual non-POSIX/non-Windows Python: retain correctness over asynchrony.
    _process_job(job_path)


def _read_payload() -> dict[str, Any]:
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except Exception:
        payload = {}
    return payload if isinstance(payload, dict) else {}


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(add_help=False)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--capture", action="store_true")
    mode.add_argument("--stop", action="store_true")
    mode.add_argument("--worker", metavar="JOB")
    args = parser.parse_args(argv)

    if args.worker:
        _process_job(args.worker)
        return

    payload = _read_payload()
    try:
        if args.capture:
            _capture_prompt(payload)
            return
        if args.stop:
            job_path = _record_stop(payload)
            if job_path:
                _dispatch_job(job_path)
    except Exception as exc:  # noqa: BLE001 - hooks must never disrupt Codex
        _log(str(payload.get("session_id") or ""), "hook-exception", repr(exc)[:500])


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""ReMe lifecycle hooks shared by Claude Code and Codex.

The MCP transport, Stop-hook behavior, and Claude transcript handling follow
ReMe's official Claude Code plugin. Claude Code sends only ``session_id`` and
lets ReMe's ``auto_memory_cc`` resolve and deduplicate its transcript. Codex has
no equivalent ReMe server-side adapter, so this plugin captures the documented
``UserPromptSubmit.prompt`` and ``Stop.last_assistant_message`` fields, emits
ReMe's official ``{role, name, content}`` shape, and queues only the completed
increment for generic ``auto_memory``.

Both paths pass the same ``memory_hint`` requiring generated memory to preserve
the conversation language. Codex writes are serialized by a detached FIFO
writer; Claude Code keeps the official detached ``auto_memory_cc`` flow. Hook
failures never block either host. Diagnostics use one UTF-8 log under
``~/.reme/log/reme-plugin.log``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

# Retained from ReMe's official Claude Code hook: auto_memory drives an inner
# agent and can legitimately take a while after the foreground hook has exited.
_CALL_TIMEOUT = 600
_STATE_VERSION = 3
_JOB_VERSION = 2
_MAX_TURNS = 500
_PROVISIONAL_TTL = timedelta(days=7)
_STATE_LOCK_TIMEOUT = 2.0
_STATE_LOCK_STALE = 30.0
_WRITER_LOCK_STALE = (_CALL_TIMEOUT * 2) + 120

# ReMe exposes this instruction through the official ``memory_hint`` argument.
# Keep it language-agnostic: the ReMe agent infers the language from the actual
# user-authored conversation instead of relying on brittle script detection.
_MEMORY_LANGUAGE_HINT = (
    "Preserve the conversation language when writing or updating memory. "
    "Infer the dominant natural language from the user-authored conversation, "
    "then use that same language for every generated memory title, frontmatter "
    "name and description, heading, summary, fact, procedure, and prose passage. "
    "Do not translate the memory into English or any other language. If the "
    "conversation intentionally mixes languages, preserve that mixture and use "
    "the dominant user language for connective prose. Keep code, commands, file "
    "paths, identifiers, API names, and verbatim quotations unchanged."
)


def _plugin_root() -> str:
    """Resolve the shared plugin root from either supported host."""
    return (
        os.environ.get("PLUGIN_ROOT")
        or os.environ.get("CLAUDE_PLUGIN_ROOT")
        or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    )


def _data_root() -> str:
    """Return writable private state storage; logs deliberately live elsewhere."""
    explicit = os.environ.get("PLUGIN_DATA") or os.environ.get("CLAUDE_PLUGIN_DATA")
    if explicit:
        return explicit
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home())
        return os.path.join(base, "ReMeMemoryPlugin")
    if sys.platform == "darwin":
        return os.path.join(str(Path.home()), "Library", "Application Support", "ReMeMemoryPlugin")
    xdg = os.environ.get("XDG_STATE_HOME")
    if xdg:
        return os.path.join(xdg, "reme-memory-plugin")
    return os.path.join(str(Path.home()), ".local", "state", "reme-memory-plugin")


def _log_path() -> str:
    return os.path.join(str(Path.home()), ".reme", "log", "reme-plugin.log")


def _server_url() -> str:
    """Prefer the bundled MCP configuration so hook and skill stay in sync."""
    mcp_json = os.path.join(_plugin_root(), ".mcp.json")
    try:
        with open(mcp_json, encoding="utf-8") as file:
            url = json.load(file)["mcpServers"]["reme"]["url"]
            if url:
                return url
    except Exception:
        pass
    host = os.environ.get("REME_HOST", "127.0.0.1")
    port = os.environ.get("REME_PORT", "2333")
    return f"http://{host}:{port}/mcp"


def _log(
    session_id: str,
    status: str,
    detail: str = "",
    *,
    component: str = "hook",
) -> None:
    """Append one UTF-8 diagnostic line without ever failing the hook."""
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"{stamp} [{component}] session={session_id} {status}"
    if detail:
        line += f" {detail}"
    try:
        path = _log_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as file:
            file.write(line + "\n")
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Official ReMe MCP transport shape
# ---------------------------------------------------------------------------


def _post(url: str, body: dict, headers: dict) -> "urllib.request.addinfourl":
    data = json.dumps(body).encode("utf-8")
    request = urllib.request.Request(url, data=data, headers=headers, method="POST")
    return urllib.request.urlopen(request, timeout=_CALL_TIMEOUT)


def _read_jsonrpc(response) -> dict | None:
    """Return the JSON-RPC envelope from JSON or text/event-stream."""
    content_type = response.headers.get("content-type", "")
    body = response.read().decode("utf-8", "replace")
    if "text/event-stream" in content_type:
        result = None
        for line in body.splitlines():
            line = line.strip()
            if not line.startswith("data:"):
                continue
            try:
                item = json.loads(line[len("data:") :].strip())
            except json.JSONDecodeError:
                continue
            if isinstance(item, dict) and ("result" in item or "error" in item):
                result = item
        return result
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _mcp_call(url: str, tool: str, arguments: dict) -> dict | None:
    """initialize -> initialized -> tools/call, matching the official hook."""
    base_headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    initialize = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "reme-memory-agent-hook", "version": "2.0"},
        },
    }
    with _post(url, initialize, base_headers) as response:
        mcp_session = response.headers.get("mcp-session-id")
        _read_jsonrpc(response)

    headers = dict(base_headers)
    if mcp_session:
        headers["mcp-session-id"] = mcp_session

    try:
        with _post(
            url,
            {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
            headers,
        ) as response:
            response.read()
    except urllib.error.HTTPError:
        pass

    call = {
        "jsonrpc": "2.0",
        "id": 2,
        "method": "tools/call",
        "params": {"name": tool, "arguments": arguments},
    }
    with _post(url, call, headers) as response:
        return _read_jsonrpc(response)


def _result_text(result: dict[str, Any]) -> str:
    content = result.get("content")
    if not isinstance(content, list):
        return ""
    texts: list[str] = []
    for block in content:
        if isinstance(block, dict) and isinstance(block.get("text"), str):
            texts.append(block["text"])
    return " | ".join(texts).strip()


def _looks_like_reme_failure(text: str) -> bool:
    """Detect failure answers that ReMe may expose as a normal MCP text result.

    ReMe's MCP service can be configured with ``tool_error_on_failure=false``
    (the upstream default), in which case ``Response.success=False`` is reduced
    to ``Response.answer`` and FastMCP reports ``isError=false``. Keep this
    detector deliberately narrow to avoid treating a normal agent answer as an
    error while still catching ReMe's explicit errors and Pydantic validation
    failures such as a missing AgentScope ``Msg.name``.
    """
    normalized = text.strip()
    lowered = normalized.casefold()
    if not normalized:
        return False
    if lowered.startswith(("error:", "failed:", "failure:")):
        return True
    if any(
        marker in lowered
        for marker in (
            "daily_list failed:",
            "frontmatter_update failed:",
            "move failed:",
            "session_id is required",
            "invalid session_id",
        )
    ):
        return True
    return "validation error for " in lowered and (
        "field required" in lowered or "[type=" in lowered
    )


def _tool_error(envelope: dict | None) -> str | None:
    """Return a useful error for JSON-RPC, MCP, and known ReMe failures."""
    if envelope is None:
        return "no JSON-RPC response"
    if "error" in envelope:
        return json.dumps(envelope["error"], ensure_ascii=False)[:1000]
    result = envelope.get("result")
    if not isinstance(result, dict):
        return "JSON-RPC response has no result object"
    text = _result_text(result)
    if result.get("isError") is True:
        return (text or json.dumps(result, ensure_ascii=False))[:1000]
    if _looks_like_reme_failure(text):
        return text[:1000]
    return None


# ---------------------------------------------------------------------------
# Private state, queue, and locks
# ---------------------------------------------------------------------------


def _daemonize() -> None:
    """Double-fork + setsid, retained from ReMe's official Claude Code hook."""
    if os.fork() > 0:
        os._exit(0)
    os.setsid()
    if os.fork() > 0:
        os._exit(0)
    devnull = os.open(os.devnull, os.O_RDWR)
    for descriptor in (0, 1, 2):
        os.dup2(devnull, descriptor)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _now_iso() -> str:
    return _now().isoformat().replace("+00:00", "Z")


def _local_now_iso() -> str:
    """Match AgentScope's local-wall-clock default while retaining an offset."""
    return datetime.now().astimezone().isoformat()


def _parse_iso(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _session_key(session_id: str) -> str:
    return hashlib.sha256(session_id.encode("utf-8", "strict")).hexdigest()


def _sessions_dir() -> str:
    path = os.path.join(_data_root(), "sessions")
    os.makedirs(path, exist_ok=True)
    return path


def _queue_dir() -> str:
    path = os.path.join(_data_root(), "queue")
    os.makedirs(path, exist_ok=True)
    return path


def _done_dir() -> str:
    path = os.path.join(_data_root(), "completed")
    os.makedirs(path, exist_ok=True)
    return path


def _failed_dir() -> str:
    path = os.path.join(_data_root(), "failed")
    os.makedirs(path, exist_ok=True)
    return path


def _state_path(session_id: str) -> str:
    return os.path.join(_sessions_dir(), _session_key(session_id) + ".json")


def _state_lock_path(session_id: str) -> str:
    return os.path.join(_sessions_dir(), _session_key(session_id) + ".lock")


def _job_path(job_id: str) -> str:
    return os.path.join(_queue_dir(), job_id + ".json")


def _done_path(job_id: str) -> str:
    return os.path.join(_done_dir(), job_id + ".json")


def _writer_lock_path() -> str:
    return os.path.join(_queue_dir(), "writer.lock")


def _load_json(path: str, default: Any) -> Any:
    try:
        with open(path, encoding="utf-8") as file:
            return json.load(file)
    except Exception:
        return default


def _atomic_write_json(path: str, payload: Any) -> None:
    """Write UTF-8 JSON atomically so readers never observe a partial file."""
    directory = os.path.dirname(path)
    os.makedirs(directory, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".reme-memory-", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as file:
            json.dump(payload, file, ensure_ascii=False, separators=(",", ":"))
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


@contextmanager
def _exclusive_lock(path: str, *, timeout: float, stale_after: float) -> Iterator[None]:
    """Cross-platform advisory lock using atomic O_EXCL creation."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    deadline = time.monotonic() + max(timeout, 0.0)
    descriptor: int | None = None
    while descriptor is None:
        try:
            descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.write(descriptor, f"pid={os.getpid()} created={_now_iso()}\n".encode("utf-8"))
            os.fsync(descriptor)
        except FileExistsError:
            try:
                if time.time() - os.path.getmtime(path) > stale_after:
                    os.unlink(path)
                    continue
            except FileNotFoundError:
                continue
            if time.monotonic() >= deadline:
                raise TimeoutError(path)
            time.sleep(0.02)
    try:
        yield
    finally:
        try:
            os.close(descriptor)
        except OSError:
            pass
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass


def _touch(path: str) -> None:
    try:
        os.utime(path, None)
    except OSError:
        pass


def _new_state(session_id: str) -> dict[str, Any]:
    return {"version": _STATE_VERSION, "session_id": session_id, "turns": []}


def _prune_state(state: dict[str, Any]) -> None:
    """Drop stale prompt-only turns and bound retained local state."""
    cutoff = _now() - _PROVISIONAL_TTL
    retained: list[dict[str, Any]] = []
    for turn in state.get("turns", []):
        if not isinstance(turn, dict):
            continue
        has_assistant = isinstance(turn.get("assistant"), str) and bool(turn["assistant"].strip())
        captured_at = _parse_iso(turn.get("prompt_at"))
        if not has_assistant and captured_at is not None and captured_at < cutoff:
            continue
        retained.append(turn)
    state["turns"] = retained[-_MAX_TURNS:]


def _load_state(session_id: str) -> dict[str, Any]:
    state = _load_json(_state_path(session_id), _new_state(session_id))
    if not isinstance(state, dict) or state.get("session_id") != session_id:
        state = _new_state(session_id)
    if not isinstance(state.get("turns"), list):
        state["turns"] = []
    state["version"] = _STATE_VERSION
    _prune_state(state)
    return state


def _find_turn(state: dict[str, Any], turn_id: str) -> dict[str, Any] | None:
    for turn in reversed(state.get("turns", [])):
        if isinstance(turn, dict) and turn.get("turn_id") == turn_id:
            return turn
    return None


def _capture_prompt(payload: dict[str, Any]) -> bool:
    session_id = str(payload.get("session_id") or "").strip()
    turn_id = str(payload.get("turn_id") or "").strip()
    prompt = payload.get("prompt")
    if not session_id or not turn_id or not isinstance(prompt, str) or not prompt.strip():
        return False

    try:
        with _exclusive_lock(
            _state_lock_path(session_id),
            timeout=_STATE_LOCK_TIMEOUT,
            stale_after=_STATE_LOCK_STALE,
        ):
            state = _load_state(session_id)
            turn = _find_turn(state, turn_id)
            if turn is None:
                turn = {"turn_id": turn_id}
                state["turns"].append(turn)
            if turn.get("prompt") != prompt:
                turn["prompt"] = prompt
                turn["prompt_at"] = _local_now_iso()
                # A changed provisional prompt invalidates any not-yet-completed job.
                memory = turn.get("memory")
                if isinstance(memory, dict) and memory.get("status") != "done":
                    old_job_id = str(memory.get("job_id") or "")
                    if old_job_id:
                        try:
                            os.unlink(_job_path(old_job_id))
                        except FileNotFoundError:
                            pass
                    turn.pop("memory", None)
            _prune_state(state)
            _atomic_write_json(_state_path(session_id), state)
        return True
    except TimeoutError:
        _log(session_id, "state-lock-timeout", f"turn={turn_id}")
        return False


def _stable_message_id(session_id: str, turn_id: str, role: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"reme-memory:{session_id}:{turn_id}:{role}"))


def _messages_for_turn(
    session_id: str,
    turn_id: str,
    turn: dict[str, Any],
) -> list[dict[str, str]]:
    prompt = turn.get("prompt")
    assistant = turn.get("assistant")
    if not isinstance(prompt, str) or not prompt.strip():
        return []
    if not isinstance(assistant, str) or not assistant.strip():
        return []

    # ReMe's official Claude adapter uses role + name + content, with name=role.
    # Stable IDs make a rare worker retry idempotent in AutoMemoryStep's dialog store.
    user: dict[str, str] = {
        "id": _stable_message_id(session_id, turn_id, "user"),
        "role": "user",
        "name": "user",
        "content": prompt,
    }
    assistant_message: dict[str, str] = {
        "id": _stable_message_id(session_id, turn_id, "assistant"),
        "role": "assistant",
        "name": "assistant",
        "content": assistant,
    }
    if isinstance(turn.get("prompt_at"), str):
        user["created_at"] = turn["prompt_at"]
    if isinstance(turn.get("assistant_at"), str):
        assistant_message["created_at"] = turn["assistant_at"]
    return [user, assistant_message]


def _payload_hash(messages: list[dict[str, str]], memory_hint: str) -> str:
    canonical = json.dumps(
        {"messages": messages, "memory_hint": memory_hint},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _job_id(session_id: str, turn_id: str, payload_hash: str) -> str:
    raw = f"{session_id}\0{turn_id}\0{payload_hash}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _record_stop(payload: dict[str, Any]) -> str | None:
    """Queue only the completed current turn; never replay the whole session."""
    session_id = str(payload.get("session_id") or "").strip()
    turn_id = str(payload.get("turn_id") or "").strip()
    assistant = payload.get("last_assistant_message")
    if not session_id or not turn_id or not isinstance(assistant, str) or not assistant.strip():
        return None

    try:
        with _exclusive_lock(
            _state_lock_path(session_id),
            timeout=_STATE_LOCK_TIMEOUT,
            stale_after=_STATE_LOCK_STALE,
        ):
            state = _load_state(session_id)
            turn = _find_turn(state, turn_id)
            if turn is None:
                _log(session_id, "skip-no-prompt", f"turn={turn_id}")
                return None

            if turn.get("assistant") != assistant:
                turn["assistant"] = assistant
                turn["assistant_at"] = _local_now_iso()

            messages = _messages_for_turn(session_id, turn_id, turn)
            if not messages:
                return None
            digest = _payload_hash(messages, _MEMORY_LANGUAGE_HINT)
            job_id = _job_id(session_id, turn_id, digest)
            path = _job_path(job_id)

            previous = turn.get("memory") if isinstance(turn.get("memory"), dict) else {}
            previous_job_id = str(previous.get("job_id") or "")
            if previous_job_id and previous_job_id != job_id and previous.get("status") != "done":
                try:
                    os.unlink(_job_path(previous_job_id))
                except FileNotFoundError:
                    pass

            if os.path.isfile(_done_path(job_id)) or (
                previous_job_id == job_id and previous.get("status") == "done"
            ):
                turn["memory"] = {
                    "job_id": job_id,
                    "payload_hash": digest,
                    "status": "done",
                    "completed_at": previous.get("completed_at") or _now_iso(),
                }
                _atomic_write_json(_state_path(session_id), state)
                _log(session_id, "skip-already-recorded", f"turn={turn_id} job={job_id[:12]}")
                return None

            if not os.path.isfile(path):
                job = {
                    "version": _JOB_VERSION,
                    "job_id": job_id,
                    "session_id": session_id,
                    "turn_id": turn_id,
                    "messages": messages,
                    "memory_hint": _MEMORY_LANGUAGE_HINT,
                    "created_at": _now_iso(),
                    "attempts": 0,
                }
                _atomic_write_json(path, job)

            turn["memory"] = {
                "job_id": job_id,
                "payload_hash": digest,
                "status": "queued",
                "queued_at": previous.get("queued_at") or _now_iso(),
            }
            _atomic_write_json(_state_path(session_id), state)
            _log(session_id, "queued", f"turn={turn_id} job={job_id[:12]}", component="queue")
            return path
    except TimeoutError:
        _log(session_id, "state-lock-timeout", f"turn={turn_id}")
        return None


def _mark_turn(job: dict[str, Any], status: str, *, error: str = "") -> None:
    session_id = str(job.get("session_id") or "")
    turn_id = str(job.get("turn_id") or "")
    job_id = str(job.get("job_id") or "")
    if not session_id or not turn_id or not job_id:
        return
    try:
        with _exclusive_lock(
            _state_lock_path(session_id),
            timeout=_STATE_LOCK_TIMEOUT,
            stale_after=_STATE_LOCK_STALE,
        ):
            state = _load_state(session_id)
            turn = _find_turn(state, turn_id)
            if turn is None:
                return
            memory = turn.get("memory") if isinstance(turn.get("memory"), dict) else {}
            if str(memory.get("job_id") or "") != job_id:
                return
            memory["status"] = status
            if status == "done":
                memory["completed_at"] = _now_iso()
                memory.pop("last_error", None)
            elif error:
                memory["last_error"] = error[:1000]
                memory["last_attempt_at"] = _now_iso()
            turn["memory"] = memory
            _atomic_write_json(_state_path(session_id), state)
    except TimeoutError:
        _log(session_id, "state-lock-timeout", f"turn={turn_id} status={status}")


def _move_invalid_job(path: str, reason: str) -> None:
    base = os.path.basename(path)
    target = os.path.join(_failed_dir(), base)
    try:
        os.replace(path, target)
    except OSError:
        try:
            os.unlink(path)
        except OSError:
            pass
    _log("", "invalid-job", f"file={base} reason={reason}", component="queue")


def _queued_jobs() -> list[str]:
    entries: list[tuple[str, str]] = []
    try:
        names = os.listdir(_queue_dir())
    except OSError:
        return []
    for name in names:
        if not name.endswith(".json"):
            continue
        path = os.path.join(_queue_dir(), name)
        job = _load_json(path, None)
        if not isinstance(job, dict):
            _move_invalid_job(path, "not-json-object")
            continue
        created_at = str(job.get("created_at") or "")
        entries.append((created_at, path))
    entries.sort(key=lambda item: (item[0], item[1]))
    return [path for _, path in entries]


def _validate_job(job: Any) -> str | None:
    if not isinstance(job, dict):
        return "job is not an object"
    for key in ("job_id", "session_id", "turn_id"):
        if not isinstance(job.get(key), str) or not job[key]:
            return f"missing {key}"
    memory_hint = job.get("memory_hint")
    if not isinstance(memory_hint, str) or not memory_hint.strip():
        return "missing memory_hint"
    messages = job.get("messages")
    if not isinstance(messages, list) or len(messages) != 2:
        return "messages must contain one user/assistant turn"
    for expected_role, message in zip(("user", "assistant"), messages, strict=True):
        if not isinstance(message, dict):
            return "message is not an object"
        if message.get("role") != expected_role or message.get("name") != expected_role:
            return f"invalid {expected_role} role/name"
        if not isinstance(message.get("content"), str) or not message["content"].strip():
            return f"empty {expected_role} content"
        if not isinstance(message.get("id"), str) or not message["id"]:
            return f"missing {expected_role} id"
    return None


def _process_job(path: str) -> tuple[bool, str]:
    job = _load_json(path, None)
    problem = _validate_job(job)
    if problem:
        _move_invalid_job(path, problem)
        return True, ""  # invalid file was quarantined; let later jobs proceed

    assert isinstance(job, dict)
    session_id = job["session_id"]
    turn_id = job["turn_id"]
    job_id = job["job_id"]

    if os.path.isfile(_done_path(job_id)):
        _mark_turn(job, "done")
        try:
            os.unlink(path)
        except OSError:
            pass
        return True, ""

    job["attempts"] = int(job.get("attempts") or 0) + 1
    job["last_attempt_at"] = _now_iso()
    _atomic_write_json(path, job)
    _mark_turn(job, "processing")

    try:
        envelope = _mcp_call(
            _server_url(),
            "auto_memory",
            {
                "session_id": session_id,
                "messages": job["messages"],
                "memory_hint": job["memory_hint"],
            },
        )
        error = _tool_error(envelope)
        if error:
            _log(
                session_id,
                "auto-memory-error",
                f"turn={turn_id} job={job_id[:12]} error={error}",
                component="reme",
            )
            job["last_error"] = error
            _atomic_write_json(path, job)
            _mark_turn(job, "retry", error=error)
            return False, error

        marker = {
            "job_id": job_id,
            "session_id": session_id,
            "turn_id": turn_id,
            "completed_at": _now_iso(),
        }
        _atomic_write_json(_done_path(job_id), marker)
        _mark_turn(job, "done")
        try:
            os.unlink(path)
        except OSError:
            pass
        _log(
            session_id,
            "auto-memory-ok",
            f"turn={turn_id} job={job_id[:12]}",
            component="reme",
        )
        return True, ""
    except urllib.error.URLError as exception:
        error = str(getattr(exception, "reason", exception))
    except Exception as exception:  # noqa: BLE001 - detached best-effort writer
        error = repr(exception)

    error = error[:1000]
    job["last_error"] = error
    _atomic_write_json(path, job)
    _mark_turn(job, "retry", error=error)
    _log(
        session_id,
        "auto-memory-unreachable",
        f"turn={turn_id} job={job_id[:12]} error={error}",
        component="reme",
    )
    return False, error


def _run_writer() -> None:
    """Drain queued turns serially; stop on first transient ReMe failure.

    After releasing an idle writer lock, re-check the queue once outside the
    lock. This closes the classic missed-wakeup window where a Stop enqueues a
    job after our final scan, its contender sees our still-held lock and exits,
    and then this writer exits without noticing the new job.
    """
    lock_path = _writer_lock_path()
    while True:
        try:
            with _exclusive_lock(lock_path, timeout=0.0, stale_after=_WRITER_LOCK_STALE):
                _log("", "writer-start", component="queue")
                while True:
                    paths = _queued_jobs()
                    if not paths:
                        _log("", "writer-idle", component="queue")
                        break
                    _touch(lock_path)
                    succeeded, _ = _process_job(paths[0])
                    _touch(lock_path)
                    if not succeeded:
                        # Preserve FIFO order. A later Stop will start another
                        # writer and retry this oldest pending turn.
                        return
        except TimeoutError:
            # Another detached writer owns the queue. This is the normal race
            # when several Stop hooks fire close together.
            return

        # The lock is now free. A job queued during the last locked empty scan
        # either has its own live contender or is visible here. If it is visible,
        # this process becomes the handoff writer and loops to acquire the lock.
        if not _queued_jobs():
            return


def _spawn_windows_writer() -> None:
    """Start the single-writer contender without creating a Windows console."""
    script = os.path.abspath(__file__)
    creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
    startup_info = None
    if hasattr(subprocess, "STARTUPINFO"):
        startup_info = subprocess.STARTUPINFO()
        startup_info.dwFlags |= getattr(subprocess, "STARTF_USESHOWWINDOW", 1)
        startup_info.wShowWindow = 0
    subprocess.Popen(
        [sys.executable, script, "--worker"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
        creationflags=creation_flags,
        startupinfo=startup_info,
        cwd=_plugin_root(),
    )


def _dispatch_writer() -> None:
    if os.name == "nt":
        try:
            _spawn_windows_writer()
        except Exception as exception:  # noqa: BLE001
            _log("", "worker-spawn-error", repr(exception)[:1000], component="queue")
        return
    if hasattr(os, "fork"):
        _daemonize()
        _run_writer()
        os._exit(0)
    # Unusual non-POSIX/non-Windows runtime: preserve correctness over latency.
    _run_writer()


# ---------------------------------------------------------------------------
# Claude Code official transcript path
# ---------------------------------------------------------------------------


def _detect_host(payload: dict[str, Any] | None = None) -> str:
    """Identify the hook dialect without tying the plugin to a shell version."""
    override = str(os.environ.get("REME_AGENT_HOST") or "").strip().casefold()
    if override in {"claude", "codex"}:
        return override

    payload = payload or {}
    # Codex lifecycle payloads provide a turn id and, at Stop, the final answer.
    if payload.get("turn_id") or "last_assistant_message" in payload:
        return "codex"
    # Claude Code's common hook payload carries the on-disk transcript path.
    if payload.get("transcript_path"):
        return "claude"

    claude_root = bool(os.environ.get("CLAUDE_PLUGIN_ROOT"))
    codex_root = bool(os.environ.get("PLUGIN_ROOT"))
    if claude_root and not codex_root:
        return "claude"
    return "codex"


def _run_claude_memory(session_id: str) -> None:
    """Call ReMe's official Claude transcript adapter for one Stop event."""
    if not session_id:
        return
    try:
        envelope = _mcp_call(
            _server_url(),
            "auto_memory_cc",
            {
                "session_id": session_id,
                "memory_hint": _MEMORY_LANGUAGE_HINT,
            },
        )
        error = _tool_error(envelope)
        if error:
            _log(session_id, "auto-memory-cc-error", f"error={error}", component="reme")
            return
        _log(session_id, "auto-memory-cc-ok", component="reme")
    except urllib.error.URLError as exception:
        error = str(getattr(exception, "reason", exception))
        _log(session_id, "auto-memory-cc-unreachable", f"error={error[:1000]}", component="reme")
    except Exception as exception:  # noqa: BLE001 - detached best-effort hook
        _log(
            session_id,
            "auto-memory-cc-exception",
            repr(exception)[:1000],
            component="reme",
        )


def _spawn_windows_claude_worker(session_id: str) -> None:
    """Run the Claude adapter with the selected Python and no console window."""
    script = os.path.abspath(__file__)
    creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
    startup_info = None
    if hasattr(subprocess, "STARTUPINFO"):
        startup_info = subprocess.STARTUPINFO()
        startup_info.dwFlags |= getattr(subprocess, "STARTF_USESHOWWINDOW", 1)
        startup_info.wShowWindow = 0
    subprocess.Popen(
        [sys.executable, script, "--claude-worker", session_id],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
        creationflags=creation_flags,
        startupinfo=startup_info,
        cwd=_plugin_root(),
    )


def _dispatch_claude_memory(session_id: str) -> None:
    """Detach exactly as the official ReMe Claude Code hook does."""
    if os.name == "nt":
        try:
            _spawn_windows_claude_worker(session_id)
        except Exception as exception:  # noqa: BLE001
            _log(session_id, "claude-worker-spawn-error", repr(exception)[:1000])
        return
    if hasattr(os, "fork"):
        _daemonize()
        _run_claude_memory(session_id)
        os._exit(0)
    _run_claude_memory(session_id)


# ---------------------------------------------------------------------------
# Hook input
# ---------------------------------------------------------------------------


def _read_payload() -> dict[str, Any]:
    """Decode either host's hook stdin explicitly as UTF-8 bytes."""
    try:
        stream = getattr(sys.stdin, "buffer", sys.stdin)
        raw = stream.read()
        text = raw if isinstance(raw, str) else raw.decode("utf-8")
        payload = json.loads(text or "{}")
    except UnicodeDecodeError as exception:
        _log("", "stdin-utf8-error", repr(exception), component="hook")
        return {}
    except Exception as exception:  # noqa: BLE001
        _log("", "stdin-json-error", repr(exception)[:1000], component="hook")
        return {}
    return payload if isinstance(payload, dict) else {}


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(add_help=False)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--capture", action="store_true")
    mode.add_argument("--stop", action="store_true")
    mode.add_argument("--worker", action="store_true")
    mode.add_argument("--claude-worker", metavar="SESSION_ID")
    arguments = parser.parse_args(argv)

    if arguments.worker:
        _run_writer()
        return
    if arguments.claude_worker:
        _run_claude_memory(arguments.claude_worker)
        return

    payload = _read_payload()
    session_id = str(payload.get("session_id") or "")
    turn_id = str(payload.get("turn_id") or "")
    host = _detect_host(payload)
    try:
        if host == "claude":
            # Match ReMe's official Claude Code integration: only Stop matters,
            # and the hook sends session_id rather than piping transcript text.
            if arguments.stop and session_id:
                _log(session_id, "claude-stop-start", component="hook")
                _dispatch_claude_memory(session_id)
            return

        if arguments.capture:
            _log(session_id, "capture-start", f"turn={turn_id}")
            if _capture_prompt(payload):
                _log(session_id, "capture-ok", f"turn={turn_id}")
            return
        if arguments.stop:
            _log(session_id, "stop-start", f"turn={turn_id}")
            if _record_stop(payload):
                _dispatch_writer()
    except Exception as exception:  # noqa: BLE001 - hooks must never disrupt a host
        _log(session_id, "hook-exception", repr(exception)[:1000])


if __name__ == "__main__":
    main()

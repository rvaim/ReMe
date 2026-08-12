from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
import urllib.error
import uuid
from pathlib import Path
from typing import Any

from .common import (
    MEMORY_LANGUAGE_HINT,
    STATE_VERSION,
    atomic_write_json,
    data_root,
    load_json,
    log,
    now_iso,
    plugin_root,
    safe_unlink,
    sha256_text,
)
from .mcp import AUTO_MEMORY_TIMEOUT, MCPClient, result_error, server_url

MAX_RECENT_PROMPTS = 6
MAX_TURNS = 500
QUEUE_VERSION = 2
WRITER_LOCK_STALE_SECONDS = int(AUTO_MEMORY_TIMEOUT) + 600


def _session_path(host: str, session_id: str) -> Path:
    path = data_root() / "sessions" / host
    path.mkdir(parents=True, exist_ok=True)
    return path / (sha256_text("session", host, session_id) + ".json")


def _new_state(host: str, session_id: str) -> dict[str, Any]:
    return {
        "version": STATE_VERSION,
        "host": host,
        "session_id": session_id,
        "recent_user_prompts": [],
        "turns": [],
    }


def _load_state(host: str, session_id: str) -> dict[str, Any]:
    state = load_json(_session_path(host, session_id), _new_state(host, session_id))
    if not isinstance(state, dict) or state.get("session_id") != session_id:
        return _new_state(host, session_id)
    if not isinstance(state.get("recent_user_prompts"), list):
        state["recent_user_prompts"] = []
    if not isinstance(state.get("turns"), list):
        state["turns"] = []
    state["version"] = STATE_VERSION
    state["host"] = host
    return state


def recent_user_prompts(host: str, session_id: str) -> list[str]:
    if not session_id:
        return []
    state = _load_state(host, session_id)
    values: list[str] = []
    for item in state.get("recent_user_prompts", []):
        if isinstance(item, dict) and isinstance(item.get("prompt"), str):
            values.append(item["prompt"])
        elif isinstance(item, str):
            values.append(item)
    return values[-MAX_RECENT_PROMPTS:]


def _find_turn(state: dict[str, Any], turn_id: str) -> dict[str, Any] | None:
    for turn in reversed(state.get("turns", [])):
        if isinstance(turn, dict) and turn.get("turn_id") == turn_id:
            return turn
    return None


def capture_user_prompt(host: str, payload: dict[str, Any]) -> bool:
    session_id = str(payload.get("session_id") or "").strip()
    prompt = payload.get("prompt")
    if not session_id or not isinstance(prompt, str) or not prompt.strip():
        return False
    state = _load_state(host, session_id)
    recent = state["recent_user_prompts"]
    latest = recent[-1] if recent else None
    if not isinstance(latest, dict) or latest.get("prompt") != prompt:
        recent.append({"prompt": prompt, "created_at": now_iso()})
    state["recent_user_prompts"] = recent[-MAX_RECENT_PROMPTS:]

    if host == "codex":
        turn_id = str(payload.get("turn_id") or "").strip()
        if turn_id:
            turn = _find_turn(state, turn_id)
            if turn is None:
                turn = {"turn_id": turn_id}
                state["turns"].append(turn)
            if turn.get("prompt") != prompt or not turn.get("prompt_at"):
                turn["prompt_at"] = now_iso()
            turn["prompt"] = prompt
            state["turns"] = state["turns"][-MAX_TURNS:]
    atomic_write_json(_session_path(host, session_id), state)
    return True


def _message_id(session_id: str, turn_id: str, role: str) -> str:
    value = f"reme-codex:{session_id}:{turn_id}:{role}"
    return str(uuid.uuid5(uuid.NAMESPACE_URL, value))


def _codex_job(payload: dict[str, Any]) -> dict[str, Any] | None:
    session_id = str(payload.get("session_id") or "").strip()
    turn_id = str(payload.get("turn_id") or "").strip()
    assistant = payload.get("last_assistant_message")
    if not session_id or not turn_id or not isinstance(assistant, str) or not assistant.strip():
        return None
    state = _load_state("codex", session_id)
    turn = _find_turn(state, turn_id)
    if turn is None or not isinstance(turn.get("prompt"), str) or not turn["prompt"].strip():
        log("memory", "skip-no-prompt", f"turn={turn_id}", session_id)
        return None
    if turn.get("assistant") != assistant or not turn.get("assistant_at"):
        turn["assistant_at"] = now_iso()
    turn["assistant"] = assistant
    atomic_write_json(_session_path("codex", session_id), state)

    prompt = str(turn["prompt"])
    messages = [
        {
            "id": _message_id(session_id, turn_id, "user"),
            "role": "user",
            "name": "user",
            "content": prompt,
            "created_at": str(turn.get("prompt_at") or now_iso()),
        },
        {
            "id": _message_id(session_id, turn_id, "assistant"),
            "role": "assistant",
            "name": "assistant",
            "content": assistant,
            "created_at": str(turn.get("assistant_at") or now_iso()),
        },
    ]
    revision = sha256_text("codex-revision", prompt, assistant)
    return {
        "version": QUEUE_VERSION,
        "job_id": sha256_text("codex-turn", session_id, turn_id),
        "revision": revision,
        "host": "codex",
        "session_id": session_id,
        "turn_id": turn_id,
        "created_at": now_iso(),
        "attempts": 0,
        "tool": "auto_memory",
        "arguments": {
            "session_id": session_id,
            "messages": messages,
            "memory_hint": MEMORY_LANGUAGE_HINT,
        },
    }


def _claude_job(payload: dict[str, Any]) -> dict[str, Any] | None:
    session_id = str(payload.get("session_id") or "").strip()
    if not session_id:
        return None
    transcript_path = str(payload.get("transcript_path") or "")
    fingerprint = ""
    if transcript_path:
        try:
            stat = Path(transcript_path).stat()
            fingerprint = f"{transcript_path}:{stat.st_size}:{stat.st_mtime_ns}"
        except OSError:
            fingerprint = transcript_path
    if not fingerprint:
        fingerprint = now_iso()
    revision = sha256_text("claude-stop", session_id, fingerprint)
    return {
        "version": QUEUE_VERSION,
        "job_id": revision,
        "revision": revision,
        "host": "claude",
        "session_id": session_id,
        "turn_id": "",
        "created_at": now_iso(),
        "attempts": 0,
        "tool": "auto_memory_cc",
        "arguments": {
            "session_id": session_id,
            "memory_hint": MEMORY_LANGUAGE_HINT,
        },
    }


def queue_stop(host: str, payload: dict[str, Any]) -> bool:
    job = _codex_job(payload) if host == "codex" else _claude_job(payload)
    if not job:
        return False
    return enqueue(job)


def _queue_root() -> Path:
    path = data_root() / "queue"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _queue_dir(name: str) -> Path:
    path = _queue_root() / name
    path.mkdir(parents=True, exist_ok=True)
    return path


def _pending_path(job_id: str) -> Path:
    return _queue_dir("pending") / (job_id + ".json")


def _inflight_path(job_id: str) -> Path:
    return _queue_dir("inflight") / (job_id + ".json")


def _done_path(job_id: str) -> Path:
    return _queue_dir("done") / (job_id + ".json")


def _writer_lock_path() -> Path:
    return _queue_root() / "writer.lock"


def _valid_job(job: Any) -> bool:
    if not isinstance(job, dict):
        return False
    if not all(job.get(key) for key in ("job_id", "revision", "session_id", "tool", "arguments")):
        return False
    return job.get("tool") in ("auto_memory", "auto_memory_cc") and isinstance(job.get("arguments"), dict)


def enqueue(job: dict[str, Any]) -> bool:
    if not _valid_job(job):
        return False
    job_id = str(job["job_id"])
    revision = str(job["revision"])
    done = load_json(_done_path(job_id), {})
    if isinstance(done, dict) and done.get("revision") == revision:
        log("queue", "duplicate-completed", f"job={job_id[:12]}", str(job["session_id"]))
        return False
    pending_path = _pending_path(job_id)
    pending = load_json(pending_path, {})
    if isinstance(pending, dict) and pending.get("revision") == revision:
        log("queue", "duplicate-pending", f"job={job_id[:12]}", str(job["session_id"]))
        return True
    atomic_write_json(pending_path, job)
    log(
        "queue",
        "queued",
        f"host={job.get('host')} tool={job.get('tool')} job={job_id[:12]}",
        str(job["session_id"]),
    )
    return True


def _json_files(directory: Path) -> list[Path]:
    try:
        return [path for path in directory.iterdir() if path.is_file() and path.suffix == ".json"]
    except OSError:
        return []


def _job_sort_key(path: Path) -> tuple[str, str]:
    value = load_json(path, {})
    created = str(value.get("created_at") or "") if isinstance(value, dict) else ""
    return created, path.name


def _has_pending() -> bool:
    return bool(_json_files(_queue_dir("pending")))


def _acquire_lock() -> bool:
    path = _writer_lock_path()
    for _ in range(2):
        try:
            path.mkdir()
        except FileExistsError:
            try:
                age = time.time() - path.stat().st_mtime
            except OSError:
                return False
            if age <= WRITER_LOCK_STALE_SECONDS:
                return False
            try:
                shutil.rmtree(path)
            except OSError:
                return False
            continue
        except OSError:
            return False
        try:
            atomic_write_json(path / "owner.json", {"pid": os.getpid(), "started_at": now_iso()})
        except Exception:
            pass
        return True
    return False


def _release_lock() -> None:
    try:
        shutil.rmtree(_writer_lock_path())
    except (FileNotFoundError, OSError):
        pass


def _recover_inflight() -> None:
    for path in _json_files(_queue_dir("inflight")):
        job = load_json(path, {})
        if not _valid_job(job):
            safe_unlink(path)
            continue
        done = load_json(_done_path(str(job["job_id"])), {})
        if isinstance(done, dict) and done.get("revision") == job.get("revision"):
            safe_unlink(path)
            continue
        pending = _pending_path(str(job["job_id"]))
        if pending.exists():
            pending_job = load_json(pending, {})
            if _valid_job(pending_job) and str(pending_job.get("created_at") or "") >= str(job.get("created_at") or ""):
                safe_unlink(path)
                continue
        os.replace(path, pending)


def _claim_next() -> Path | None:
    paths = sorted(_json_files(_queue_dir("pending")), key=_job_sort_key)
    for path in paths:
        job = load_json(path, {})
        if not _valid_job(job):
            safe_unlink(path)
            continue
        done = load_json(_done_path(str(job["job_id"])), {})
        if isinstance(done, dict) and done.get("revision") == job.get("revision"):
            safe_unlink(path)
            continue
        inflight = _inflight_path(str(job["job_id"]))
        try:
            os.replace(path, inflight)
        except OSError:
            continue
        return inflight
    return None


def _requeue(path: Path, job: dict[str, Any], error: str) -> None:
    updated = dict(job)
    updated["attempts"] = int(updated.get("attempts") or 0) + 1
    updated["last_attempt_at"] = now_iso()
    updated["last_error"] = str(error)[:1000]
    atomic_write_json(_pending_path(str(updated["job_id"])), updated)
    safe_unlink(path)


def _process(path: Path) -> bool:
    job = load_json(path, {})
    if not _valid_job(job):
        safe_unlink(path)
        return True
    session_id = str(job["session_id"])
    try:
        client = MCPClient(server_url(), timeout=AUTO_MEMORY_TIMEOUT)
        envelope = client.call(str(job["tool"]), dict(job["arguments"]))
        error = result_error(envelope)
        if error:
            raise RuntimeError(error)
        atomic_write_json(
            _done_path(str(job["job_id"])),
            {
                "version": QUEUE_VERSION,
                "job_id": job["job_id"],
                "revision": job["revision"],
                "host": job.get("host"),
                "session_id": session_id,
                "completed_at": now_iso(),
            },
        )
        safe_unlink(path)
        pending = _pending_path(str(job["job_id"]))
        pending_job = load_json(pending, {})
        if isinstance(pending_job, dict) and pending_job.get("revision") == job.get("revision"):
            safe_unlink(pending)
        log("memory", "write-ok", f"tool={job['tool']} job={str(job['job_id'])[:12]}", session_id)
        return True
    except urllib.error.URLError as exc:
        error = str(getattr(exc, "reason", exc))
    except Exception as exc:
        error = repr(exc)
    _requeue(path, job, error)
    log("memory", "write-failed", f"tool={job['tool']} error={error}", session_id)
    return False


def drain_queue() -> bool:
    _recover_inflight()
    while True:
        path = _claim_next()
        if path is None:
            return True
        if not _process(path):
            return False


def run_writer() -> None:
    while True:
        if not _acquire_lock():
            return
        try:
            drained = drain_queue()
        finally:
            _release_lock()
        if not drained or not _has_pending():
            return


def _daemonize() -> None:
    if os.fork() > 0:
        os._exit(0)
    os.setsid()
    if os.fork() > 0:
        os._exit(0)
    devnull = os.open(os.devnull, os.O_RDWR)
    for descriptor in (0, 1, 2):
        os.dup2(devnull, descriptor)


def _spawn_windows_writer() -> None:
    script = plugin_root() / "hooks" / "reme_hook.py"
    creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
    startup_info = None
    if hasattr(subprocess, "STARTUPINFO"):
        startup_info = subprocess.STARTUPINFO()
        startup_info.dwFlags |= getattr(subprocess, "STARTF_USESHOWWINDOW", 1)
        startup_info.wShowWindow = 0
    subprocess.Popen(
        [sys.executable, str(script), "--worker"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        cwd=str(plugin_root()),
        env=os.environ.copy(),
        close_fds=True,
        creationflags=creation_flags,
        startupinfo=startup_info,
    )


def dispatch_writer() -> None:
    if not _has_pending():
        return
    if os.name == "nt":
        try:
            _spawn_windows_writer()
        except Exception as exc:
            log("queue", "writer-spawn-failed", repr(exc))
        return
    if hasattr(os, "fork"):
        _daemonize()
        run_writer()
        os._exit(0)
    run_writer()

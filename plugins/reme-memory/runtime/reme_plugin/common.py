from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

LOG_NAME = "reme-plugin.log"
CONFIG_NAME = "llm.json"
STATE_VERSION = 3
MAX_ERROR_DETAIL = 1000


def plugin_root() -> Path:
    explicit = os.environ.get("PLUGIN_ROOT") or os.environ.get("CLAUDE_PLUGIN_ROOT")
    if explicit:
        return Path(explicit).expanduser().resolve()
    return Path(__file__).resolve().parents[2]


def data_root() -> Path:
    explicit = os.environ.get("PLUGIN_DATA") or os.environ.get("CLAUDE_PLUGIN_DATA")
    if explicit:
        return Path(explicit).expanduser()
    return Path.home() / ".reme" / "plugin-state" / "reme-memory"


def config_path() -> Path:
    """Return the shared LLM configuration used by all plugin LLM features."""
    override = os.environ.get("REME_LLM_CONFIG") or os.environ.get("REME_RECALL_GATE_CONFIG")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".reme" / "config" / CONFIG_NAME


def log_path() -> Path:
    return Path.home() / ".reme" / "log" / LOG_NAME


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def clean_log_value(value: Any) -> str:
    return str(value).replace("\r", "\\r").replace("\n", "\\n")[:MAX_ERROR_DETAIL]


def log(component: str, status: str, detail: str = "", session_id: str = "") -> None:
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"{stamp} [{clean_log_value(component)}]"
    if session_id:
        line += f" session={clean_log_value(session_id)}"
    line += f" {clean_log_value(status)}"
    if detail:
        line += f" {clean_log_value(detail)}"
    try:
        path = log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8", newline="\n") as file:
            file.write(line + "\n")
    except Exception:
        pass


def read_stdin_json() -> dict[str, Any]:
    try:
        stream = getattr(sys.stdin, "buffer", sys.stdin)
        raw = stream.read()
        if isinstance(raw, str):
            text = raw
        else:
            text = raw.decode("utf-8-sig", "strict")
        value = json.loads(text or "{}")
    except UnicodeDecodeError as exc:
        log("hook", "stdin-invalid-utf8", repr(exc))
        return {}
    except Exception as exc:
        log("hook", "stdin-invalid-json", repr(exc))
        return {}
    return value if isinstance(value, dict) else {}


def emit_additional_context(text: str) -> None:
    if not text:
        return
    payload = {
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": text,
        }
    }
    sys.stdout.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
    sys.stdout.flush()


def sha256_text(*parts: str) -> str:
    digest = hashlib.sha256()
    for part in parts:
        digest.update(part.encode("utf-8", "strict"))
        digest.update(b"\0")
    return digest.hexdigest()


def load_json(path: Path, default: Any) -> Any:
    try:
        with path.open(encoding="utf-8") as file:
            return json.load(file)
    except Exception:
        return default


def atomic_write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".reme-plugin-", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as file:
            json.dump(payload, file, ensure_ascii=False, separators=(",", ":"))
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def safe_unlink(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    except OSError:
        pass


def bounded_text(value: Any, limit: int) -> str:
    text = str(value or "")
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)] + "\u2026"


def contains_cjk(text: str) -> bool:
    return any("\u3400" <= char <= "\u9fff" for char in text)


MEMORY_LANGUAGE_HINT = (
    "Infer the primary natural language from the user's conversation text and create or update "
    "the memory in that same language. Use that language for the title, frontmatter name and "
    "description, Markdown headings, summaries, facts, procedures, and explanatory prose. Do not "
    "default-translate the memory into English. Preserve code, commands, file paths, identifiers, "
    "API names, and direct quotations exactly. If the user intentionally mixes languages, preserve "
    "the mixed-language content and use the primary user language for connective prose."
)

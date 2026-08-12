from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

from .common import bounded_text, config_path, log

DEFAULTS: dict[str, Any] = {
    "version": 1,
    "enabled": False,
    "api": "responses",
    "base_url": "",
    "api_key": "",
    "model": "",
    "timeout_seconds": 8.0,
    "reasoning_effort": "none",
    "max_output_tokens": 120,
    "recent_user_messages": 2,
    "max_prompt_chars": 5000,
    "search_limit": 5,
    "inject_limit": 3,
    "max_context_chars": 6000,
    "reme_timeout_seconds": 3.0,
    "min_score": None,
}

# MUST rules are intentionally narrow and precision-oriented. They identify
# explicit references to durable history, preferences, or continuation.
_MUST_PATTERNS = [
    re.compile(
        r"\b(recall|do\s+you\s+remember|remember\s+(?:when|our|my)|"
        r"previous(?:ly)?|earlier|last\s+time|yesterday|"
        r"continue\s+(?:where|from)|we\s+(?:decided|agreed|discussed)|"
        r"my\s+(?:preference|preferences|style|habit|habits|convention|conventions)|"
        r"as\s+usual)\b",
        re.IGNORECASE,
    ),
    re.compile(
        "(?:\\u4e4b\\u524d|\\u4e0a\\u6b21|\\u6628\\u5929|\\u4ee5\\u524d|"
        "\\u66fe\\u7ecf|\\u8fd8\\u8bb0\\u5f97|\\u8bb0\\u5f97\\u6211|"
        "\\u6211\\u7684.{0,8}(?:\\u504f\\u597d|\\u4e60\\u60ef|\\u98ce\\u683c|\\u89c4\\u5219)|"
        "\\u6211\\u4eec.{0,8}(?:\\u51b3\\u5b9a|\\u7ea6\\u5b9a|\\u8ba8\\u8bba\\u8fc7)|"
        "\\u7ee7\\u7eed.{0,12}(?:\\u4e4b\\u524d|\\u4e0a\\u6b21|\\u6628\\u5929|\\u90a3\\u4e2a)|"
        "\\u6309.{0,8}(?:\\u4ee5\\u524d|\\u4e4b\\u524d|\\u4e0a\\u6b21))"
    ),
]

# SKIP rules cover only tasks that are clearly self-contained. MUST is checked
# first, so a phrase such as "rename it using my previous convention" recalls.
_SKIP_PATTERNS = [
    re.compile(r"^\s*(?:please\s+)?remember\s+(?:that|this)\b", re.IGNORECASE),
    re.compile("^(?:\u8bf7)?\u8bb0\u4f4f"),
    re.compile(r"^\s*(?:translate|summarize)\b", re.IGNORECASE),
    re.compile(r"^\s*(?:explain|describe)\s+(?:this|the)\s+(?:code|snippet|text)\b", re.IGNORECASE),
    re.compile(r"\b(?:rename|change)\b.{0,30}\b(?:variable|identifier)\b", re.IGNORECASE),
    re.compile(r"^\s*(?:run|execute)\s+(?:the\s+)?tests?\s*[.!?]*\s*$", re.IGNORECASE),
    re.compile(r"^\s*[0-9\s+*/().^-]+\s*$"),
    re.compile(
        "^(?:\\u7ffb\\u8bd1|\\u603b\\u7ed3|\\u6982\\u62ec|\\u89e3\\u91ca\\u8fd9\\u6bb5\\u4ee3\\u7801|"
        "\\u8fd0\\u884c\\u6d4b\\u8bd5)"
    ),
    re.compile(
        "(?:\\u4fee\\u6539|\\u66f4\\u6539|\\u91cd\\u547d\\u540d|\\u6539).{0,10}"
        "(?:\\u53d8\\u91cf\\u540d|\\u53d8\\u91cf)"
    ),
]

GATE_INSTRUCTIONS = """You are a conservative recall router for a coding assistant.
Decide whether answering the current request materially requires durable information from earlier sessions, such as prior decisions, user preferences, project history, unresolved work, or earlier failures that are not fully present in the supplied recent context.

Return recall only when missing long-term memory could make the answer wrong, inconsistent, or unable to continue. Return skip when the current request can be handled from the current prompt, the current conversation, repository inspection, or normal tools. Do not recall merely because historical context could be mildly useful.

Return exactly one JSON object and no prose, Markdown, or code fences. The object must contain exactly two keys: decision and query. Do not emit any other field.

When decision is recall, produce a concise non-empty standalone ReMe search query in the user's language. When decision is skip, query must be an empty string. Do not answer the user's task."""



@dataclass(frozen=True)
class GateDecision:
    decision: str
    query: str
    source: str
    latency_ms: int = 0


class GateConfigurationError(ValueError):
    pass


def _section(value: dict[str, Any], *names: str) -> dict[str, Any] | None:
    present = [name for name in names if name in value]
    if len(present) > 1:
        raise GateConfigurationError(
            "llm.json must not define both recall_gate and recall-gate"
        )
    if not present:
        return None
    section = value[present[0]]
    if section is None:
        return None
    if not isinstance(section, dict):
        raise GateConfigurationError(f"{present[0]} must be a JSON object")
    return section


def load_config() -> dict[str, Any] | None:
    """Load ``default`` plus optional ``recall_gate`` overrides from llm.json.

    The shared file is intentionally feature-extensible. Future LLM-backed
    plugin features can merge their own section over the same ``default``
    credentials without introducing another API-key file. For compatibility
    with the user's wording, both ``recall_gate`` and ``recall-gate`` are
    accepted, but they may not coexist.
    """
    path = config_path()
    try:
        with path.open(encoding="utf-8") as file:
            value = json.load(file)
    except FileNotFoundError:
        return None
    except json.JSONDecodeError as exc:
        raise GateConfigurationError(
            f"llm.json contains invalid JSON at line {exc.lineno}, column {exc.colno}"
        ) from exc
    except OSError as exc:
        raise GateConfigurationError(f"llm.json could not be read: {exc}") from exc
    if not isinstance(value, dict):
        raise GateConfigurationError("llm.json must contain a JSON object")

    default_section = value.get("default")
    if default_section is None:
        default_section = {}
    if not isinstance(default_section, dict):
        raise GateConfigurationError("default must be a JSON object")
    recall_section = _section(value, "recall_gate", "recall-gate")

    # If neither section exists, this is a valid generic config file that does
    # not currently configure automatic recall.
    if not default_section and recall_section is None:
        return None

    config = dict(DEFAULTS)
    config.update(default_section)
    if recall_section is not None:
        config.update(recall_section)

    if not bool(config.get("enabled")):
        return None
    api = str(config.get("api") or "responses").strip().lower()
    if api not in ("responses", "openai-responses", "openai_responses"):
        raise GateConfigurationError("recall_gate requires api=responses")
    config["api"] = "responses"

    for key in ("base_url", "api_key", "model"):
        if not isinstance(config.get(key), str):
            raise GateConfigurationError(f"{key} must be a string")
    for key in (
        "timeout_seconds",
        "max_output_tokens",
        "recent_user_messages",
        "max_prompt_chars",
        "search_limit",
        "inject_limit",
        "max_context_chars",
        "reme_timeout_seconds",
    ):
        try:
            config[key] = float(config[key]) if "seconds" in key else int(config[key])
        except (TypeError, ValueError) as exc:
            raise GateConfigurationError(f"{key} has an invalid value") from exc
    config["timeout_seconds"] = min(max(float(config["timeout_seconds"]), 1.0), 30.0)
    config["reme_timeout_seconds"] = min(
        max(float(config["reme_timeout_seconds"]), 1.0),
        30.0,
    )
    config["max_output_tokens"] = min(max(int(config["max_output_tokens"]), 32), 512)
    config["recent_user_messages"] = min(max(int(config["recent_user_messages"]), 0), 5)
    config["max_prompt_chars"] = min(max(int(config["max_prompt_chars"]), 256), 20000)
    config["search_limit"] = min(max(int(config["search_limit"]), 1), 20)
    config["inject_limit"] = min(max(int(config["inject_limit"]), 1), 8)
    config["max_context_chars"] = min(max(int(config["max_context_chars"]), 512), 9500)
    min_score = config.get("min_score")
    if min_score is not None:
        try:
            config["min_score"] = float(min_score)
        except (TypeError, ValueError) as exc:
            raise GateConfigurationError("min_score must be a number or null") from exc
    return config


def deterministic_route(prompt: str) -> str:
    text = prompt.strip()
    if not text:
        return "skip"
    for pattern in _MUST_PATTERNS:
        if pattern.search(text):
            return "recall"
    for pattern in _SKIP_PATTERNS:
        if pattern.search(text):
            return "skip"
    return "llm"


def _responses_url(base_url: str) -> str:
    value = base_url.strip().rstrip("/")
    if value.endswith("/responses"):
        return value
    return value + "/responses"


def _gate_input(prompt: str, recent_user_prompts: list[str], config: dict[str, Any]) -> str:
    max_chars = int(config["max_prompt_chars"])
    recent_count = int(config["recent_user_messages"])
    recent = recent_user_prompts[-recent_count:] if recent_count else []
    sections = ["CURRENT USER REQUEST:\n" + bounded_text(prompt, max_chars)]
    if recent:
        per_item = max(256, max_chars // max(1, len(recent)))
        rendered = []
        for index, value in enumerate(recent, start=1):
            rendered.append(f"[{index}] {bounded_text(value, per_item)}")
        sections.append("RECENT USER REQUESTS FROM THIS SESSION:\n" + "\n".join(rendered))
    return "\n\n".join(sections)


def _extract_output_text(response: dict[str, Any]) -> str:
    helper = response.get("output_text")
    if isinstance(helper, str) and helper.strip():
        return helper
    pieces: list[str] = []
    output = response.get("output")
    if not isinstance(output, list):
        return ""
    for item in output:
        if not isinstance(item, dict) or item.get("type") != "message":
            continue
        content = item.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "output_text" and isinstance(block.get("text"), str):
                pieces.append(block["text"])
    return "".join(pieces)


def call_responses_gate(
    prompt: str,
    recent_user_prompts: list[str],
    config: dict[str, Any],
) -> GateDecision:
    if not config.get("base_url") or not config.get("api_key") or not config.get("model"):
        raise GateConfigurationError("base_url, api_key, and model are required for LLM routing")
    body = {
        "model": config["model"],
        "instructions": GATE_INSTRUCTIONS,
        "input": _gate_input(prompt, recent_user_prompts, config),
        "reasoning": {"effort": str(config.get("reasoning_effort") or "none")},
        "text": {
            "format": {
                "type": "json_object",
            }
        },
        "store": False,
        "max_output_tokens": int(config["max_output_tokens"]),
    }
    request = urllib.request.Request(
        _responses_url(str(config["base_url"])),
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": "Bearer " + str(config["api_key"]),
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )
    started = time.monotonic()
    try:
        with urllib.request.urlopen(request, timeout=float(config["timeout_seconds"])) as response:
            raw = response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        body_text = exc.read().decode("utf-8", "replace")[:500]
        secret = str(config.get("api_key") or "")
        if secret:
            body_text = body_text.replace(secret, "<redacted>")
        raise RuntimeError(f"Responses API HTTP {exc.code}: {body_text}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Responses API unavailable: {exc.reason}") from exc
    elapsed = int((time.monotonic() - started) * 1000)
    try:
        response_obj = json.loads(raw)
        result = json.loads(_extract_output_text(response_obj))
    except Exception as exc:
        raise RuntimeError("Responses API did not return valid gate JSON") from exc
    if not isinstance(result, dict) or set(result) != {"decision", "query"}:
        raise RuntimeError("Responses API gate result must contain exactly decision and query")
    decision = result.get("decision")
    query = result.get("query")
    if decision not in ("recall", "skip") or not isinstance(query, str):
        raise RuntimeError("Responses API gate result failed local validation")
    query = query.strip()
    if decision == "recall" and not query:
        raise RuntimeError("Responses API recall decision requires a non-empty query")
    if decision == "skip" and query:
        raise RuntimeError("Responses API skip decision requires an empty query")
    return GateDecision(decision, query, "llm", elapsed)


def decide(
    prompt: str,
    recent_user_prompts: list[str],
    config: dict[str, Any],
    *,
    session_id: str = "",
) -> GateDecision:
    route = deterministic_route(prompt)
    if route == "recall":
        decision = GateDecision("recall", prompt.strip(), "rule", 0)
        log("gate", "recall", "source=rule", session_id)
        return decision
    if route == "skip":
        decision = GateDecision("skip", "", "rule", 0)
        log("gate", "skip", "source=rule", session_id)
        return decision
    try:
        decision = call_responses_gate(prompt, recent_user_prompts, config)
    except Exception as exc:
        log("gate", "failed-open", repr(exc), session_id)
        return GateDecision("skip", "", "failure", 0)
    log(
        "gate",
        decision.decision,
        f"source=llm latency_ms={decision.latency_ms} query_chars={len(decision.query)}",
        session_id,
    )
    return decision

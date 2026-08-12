from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .common import plugin_root

DEFAULT_MCP_TIMEOUT = 8.0
AUTO_MEMORY_TIMEOUT = 600.0


def server_url() -> str:
    override = os.environ.get("REME_MCP_URL")
    if override:
        return override.rstrip("/")
    path = plugin_root() / ".mcp.json"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        url = value["mcpServers"]["reme"]["url"]
        if isinstance(url, str) and url:
            return url.rstrip("/")
    except Exception:
        pass
    host = os.environ.get("REME_HOST", "127.0.0.1")
    port = os.environ.get("REME_PORT", "2333")
    return f"http://{host}:{port}/mcp"


def _read_jsonrpc(response) -> dict[str, Any] | None:
    content_type = response.headers.get("content-type", "")
    body = response.read().decode("utf-8", "replace")
    if "text/event-stream" in content_type:
        found = None
        for line in body.splitlines():
            line = line.strip()
            if not line.startswith("data:"):
                continue
            try:
                value = json.loads(line[5:].strip())
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict) and ("result" in value or "error" in value):
                found = value
        return found
    try:
        value = json.loads(body)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def _text_blocks(content: Any) -> list[str]:
    texts: list[str] = []
    if not isinstance(content, list):
        return texts
    for block in content:
        if isinstance(block, dict) and isinstance(block.get("text"), str):
            texts.append(block["text"])
    return texts


def _structured_failure(value: Any) -> str | None:
    if not isinstance(value, dict):
        return None
    if value.get("success") is False:
        return str(
            value.get("answer")
            or value.get("error")
            or value.get("message")
            or "ReMe returned success=false"
        )
    for key in ("response", "data", "result"):
        failure = _structured_failure(value.get(key))
        if failure:
            return failure
    return None


def result_error(envelope: dict[str, Any] | None) -> str | None:
    if envelope is None:
        return "no JSON-RPC response"
    if envelope.get("error") is not None:
        return json.dumps(envelope["error"], ensure_ascii=False)
    result = envelope.get("result")
    if not isinstance(result, dict):
        return "JSON-RPC response is missing a tool result"
    if result.get("isError") is True or result.get("is_error") is True:
        return " | ".join(_text_blocks(result.get("content"))) or "MCP tool returned isError=true"
    structured = result.get("structuredContent")
    if structured is None:
        structured = result.get("structured_content")
    failure = _structured_failure(structured)
    if failure:
        return failure
    for text in _text_blocks(result.get("content")):
        stripped = text.strip()
        if not stripped.startswith("{"):
            continue
        try:
            decoded = json.loads(stripped)
        except json.JSONDecodeError:
            continue
        failure = _structured_failure(decoded)
        if failure:
            return failure
    return None


def result_values(envelope: dict[str, Any] | None) -> list[Any]:
    if not isinstance(envelope, dict):
        return []
    result = envelope.get("result")
    if not isinstance(result, dict):
        return []
    values: list[Any] = []
    for key in ("structuredContent", "structured_content"):
        if key in result:
            values.append(result[key])
    for text in _text_blocks(result.get("content")):
        stripped = text.strip()
        if stripped.startswith("{") or stripped.startswith("["):
            try:
                values.append(json.loads(stripped))
                continue
            except json.JSONDecodeError:
                pass
        values.append(text)
    return values


@dataclass
class MCPClient:
    url: str
    timeout: float = DEFAULT_MCP_TIMEOUT

    def __post_init__(self) -> None:
        self.url = self.url.rstrip("/")
        self._headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        self._next_id = 1
        self._initialize()

    def _post(self, body: dict[str, Any]):
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            self.url,
            data=data,
            headers=self._headers,
            method="POST",
        )
        return urllib.request.urlopen(request, timeout=self.timeout)

    def _initialize(self) -> None:
        initialize = {
            "jsonrpc": "2.0",
            "id": self._next_id,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "reme-universal-plugin", "version": "0.3.0"},
            },
        }
        self._next_id += 1
        with self._post(initialize) as response:
            session_id = response.headers.get("mcp-session-id")
            _read_jsonrpc(response)
        if session_id:
            self._headers["mcp-session-id"] = session_id
        try:
            with self._post(
                {
                    "jsonrpc": "2.0",
                    "method": "notifications/initialized",
                    "params": {},
                }
            ) as response:
                response.read()
        except urllib.error.HTTPError as exc:
            if exc.code not in (200, 202, 204):
                raise

    def call(self, tool: str, arguments: dict[str, Any]) -> dict[str, Any] | None:
        request_id = self._next_id
        self._next_id += 1
        with self._post(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": "tools/call",
                "params": {"name": tool, "arguments": arguments},
            }
        ) as response:
            return _read_jsonrpc(response)


def call_once(
    tool: str,
    arguments: dict[str, Any],
    *,
    timeout: float = DEFAULT_MCP_TIMEOUT,
    url: str | None = None,
) -> dict[str, Any] | None:
    return MCPClient(url or server_url(), timeout=timeout).call(tool, arguments)

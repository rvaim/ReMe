# Upstream alignment

This adapter intentionally minimizes divergence from ReMe's official Claude Code plugin.

## Official sources

- ReMe Claude Code hook: `plugins/claude_code/reme/hooks/auto_memory.py`
- ReMe Claude Code skill: `plugins/claude_code/reme/skills/reme-memory/SKILL.md`
- ReMe Claude Code MCP config: `plugins/claude_code/reme/.mcp.json`

Upstream repository: https://github.com/agentscope-ai/ReMe

## Kept from the official hook

The following design and implementation shapes are retained intentionally:

- stdlib-only hook runtime;
- `_CALL_TIMEOUT = 600` for the slow inner-agent memory job;
- `.mcp.json` first, `REME_HOST` / `REME_PORT` fallback;
- best-effort file logging that never fails the host agent;
- `urllib.request` POST helper;
- JSON + `text/event-stream` JSON-RPC parser;
- MCP `initialize` → `notifications/initialized` → `tools/call` sequence;
- MCP protocol version `2025-06-18`;
- POSIX double-fork + `setsid` detachment;
- unreachable/error handling is logged instead of surfaced to the host.

## Codex-only delta

The official Claude hook can call `auto_memory_cc(session_id)` because ReMe knows how to find and parse Claude Code transcripts. Codex is different, so this adapter changes only the lifecycle-input edge:

1. Capture documented `UserPromptSubmit.prompt` keyed by `session_id + turn_id`.
2. Capture documented `Stop.last_assistant_message` for that same turn.
3. Keep a bounded local session state (500 turns max) using atomic writes.
4. Pass `[{role, content}, ...]` to ReMe's documented generic `auto_memory` tool.
5. Never parse `transcript_path` or depend on Codex rollout JSONL internals.
6. On Windows, use a small GUI-subsystem launcher and `CREATE_NO_WINDOW` for plugin-owned child processes because Python's normal console executable can flash a terminal when launched from GUI hosts.

The Windows launcher does **not** claim to hide the outer shell that Codex itself creates for command hooks. That is an upstream Codex runtime boundary.

# Attribution and adaptation notes

This plugin is an independent Codex adapter for ReMe. It is not an official AgentScope/ReMe release.

The following parts intentionally track the current official ReMe Claude Code plugin closely:

- `.mcp.json` is structurally identical to ReMe's official Claude Code plugin MCP config.
- `skills/reme-memory/SKILL.md` is adapted from ReMe's official `reme-memory` skill. The substantive recall workflow is retained; only Claude Code / plugin-path wording is changed for Codex.
- `hooks/auto_memory.py` retains the official ReMe hook's stdlib Streamable HTTP MCP client, server URL resolution, call timeout, JSON/SSE handling, POSIX double-fork behavior, and best-effort logging philosophy.

Codex-only additions are limited to lifecycle input adaptation (`UserPromptSubmit` + `Stop`), stable local state instead of parsing Codex transcript JSONL, the generic ReMe `auto_memory` tool, writable `PLUGIN_DATA`, and a Windows no-console launcher.

ReMe is licensed under Apache-2.0. See the upstream project for its license and notices:
https://github.com/agentscope-ai/ReMe


Runtime packaging note: `bin/reme-hook-launcher.exe` is prebuilt. End users do not need Go or any compiler to install or run this plugin.

# Attribution and adaptation notes

This is an independent universal adapter for ReMe. It is not an official AgentScope/ReMe, Anthropic, or OpenAI release.

The implementation intentionally tracks ReMe's official Claude Code integration:

- `.mcp.json` and the recall-only `reme-memory` Skill retain the official workflow.
- `hooks/auto_memory.py` retains the official stdlib Streamable HTTP MCP client shape, server resolution, long call timeout, JSON/SSE handling, POSIX detachment, and best-effort failure behavior.
- Claude Code uses ReMe's official `auto_memory_cc` server-side transcript resolver rather than a replacement parser.
- Generic Codex messages follow the official `{role, name, content}` contract with `name = role`.
- Both paths use ReMe's official `memory_hint` parameter to request conversation-language-preserving memory.

Host-specific additions are limited to dual marketplace/manifests, explicitly separated hook configs, Codex lifecycle input adaptation, strict UTF-8 handling, stable per-turn identity, serialized generic `auto_memory` writing, a Claude Code exec-form dispatcher, cross-platform interpreter discovery, and a Windows no-console launcher.

ReMe is licensed under Apache-2.0. See the upstream project and bundled `LICENSE` for license terms:
https://github.com/agentscope-ai/ReMe

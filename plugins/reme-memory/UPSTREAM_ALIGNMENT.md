# Upstream alignment

This universal adapter minimizes divergence from ReMe's official Claude Code integration while isolating the small lifecycle differences required by Claude Code and Codex.

## Primary ReMe baseline

The implementation tracks these upstream behaviors:

- ReMe's official Claude Code Stop hook and Streamable HTTP MCP client;
- `reme/steps/evolve/auto_memory_cc.py` for server-side Claude transcript loading, UUID deduplication, increment rendering, and delegation to `AutoMemoryStep`;
- `reme/steps/evolve/auto_memory.py` for generic conversation memory and the official `memory_hint` parameter;
- ReMe's official recall-only `reme-memory` Skill and `.mcp.json` endpoint;
- ReMe's generic integration principle of serializing background `auto_memory` writes.

Upstream repository: https://github.com/agentscope-ai/ReMe

## Universal marketplace layout

One source directory exposes both manifests and both marketplace catalogs:

```text
.claude-plugin/marketplace.json
.agents/plugins/marketplace.json
plugins/reme-memory/.claude-plugin/plugin.json
plugins/reme-memory/.codex-plugin/plugin.json
```

Skill, MCP configuration, Python memory adapter, launchers, license, and documentation are shared once. Each manifest explicitly selects a host-specific hook file:

```text
Claude Code -> hooks/claude-hooks.json
Codex      -> hooks/codex-hooks.json
```

There is intentionally no default `hooks/hooks.json`. Both hosts recognize that conventional location, but their handler schemas and Windows command forms are not identical. Explicit custom paths keep the shared plugin source tree portable without making either host parse the other's configuration.

The requested `rvaim/rvaim-marketplace/plugins/plugin-creator` path established the intended dual-host direction. That exact GitHub tree was not retrievable from the build sandbox, so no unverified file contents were attributed to it. The resulting layout was cross-checked against current official Claude Code and OpenAI Codex plugin specifications and public multi-runtime marketplace implementations.

## Claude Code path: official ReMe behavior retained

Claude Code registers only a Stop hook. Its exec-form dispatcher:

1. preserves hook stdin as raw bytes;
2. selects the existing Windows GUI launcher or POSIX launcher without a host shell;
3. extracts only `session_id` in the shared Python adapter;
4. detaches immediately;
5. calls `auto_memory_cc` with `session_id` and the shared `memory_hint`;
6. sends no transcript messages from the plugin.

ReMe's server-side `AutoMemoryCCStep` remains responsible for locating the transcript, copying identity-bearing entries, deduplicating by record UUID, rendering only the increment into `{role, name, content}`, and writing memory. The adapter does not duplicate that parser or create a second Claude session store.

## Codex path: necessary lifecycle delta

ReMe currently has no equivalent server-side Codex transcript resolver, so the plugin adds only the missing boundary adapter:

1. Decode hook stdin from raw UTF-8 bytes.
2. Capture documented `UserPromptSubmit.prompt` keyed by `session_id + turn_id` as provisional state.
3. Capture `Stop.last_assistant_message` for the same turn.
4. Emit only that completed turn as two AgentScope-compatible messages.
5. Use `name = role`, stable UUIDv5 message IDs, and stable timestamps.
6. Include the shared `memory_hint` in deterministic job identity and the `auto_memory` request.
7. Serialize generic `auto_memory` calls through one FIFO writer.
8. Keep transient failures queued and use completion markers for duplicate-Stop idempotence.
9. Never parse Codex rollout/transcript JSONL or depend on a private schema.

## Conversation-language preservation

Both paths pass the same official `memory_hint` before generation. It requires ReMe to infer the dominant language from user-authored conversation and use it for memory titles, frontmatter names/descriptions, headings, summaries, facts, procedures, and prose. Intentional language mixing is preserved, and code, commands, paths, identifiers, API names, and quotations remain unchanged.

The plugin does not post-translate generated memory. Post-processing would add another model/write phase and could corrupt exact technical text. The configured ReMe model remains responsible for following the hint.

## MCP behavior retained

The shared Python hook preserves the official transport shape:

- stdlib-only Python runtime;
- bundled `.mcp.json` first, `REME_HOST` / `REME_PORT` fallback;
- UTF-8 JSON request bodies;
- JSON and `text/event-stream` JSON-RPC parsing;
- `initialize -> notifications/initialized -> tools/call`;
- MCP protocol version `2025-06-18`;
- 600-second detached memory-call limit;
- POSIX double-fork plus `setsid`;
- best-effort failures that never block the host agent.

## Platform launchers

Platform launchers contain no ReMe memory logic.

Windows discovery:

```text
REME_PYTHON -> REME_CODEX_PYTHON -> py -3 -> python3 -> python
```

macOS/Linux discovery:

```text
REME_PYTHON -> REME_CODEX_PYTHON -> python3 -> python
```

Every candidate must pass a finite Python >=3.10 probe. The Windows executable is a prebuilt x64 GUI-subsystem launcher using no-console child-process flags. Go is build-time only; no Go source/runtime is included in the release.

Codex owns its hook shell; the plugin retains the shell-native `&` command and does not select PowerShell. Claude Code uses its documented exec form (`command` plus `args`) with a dependency-free Node dispatcher. The dispatcher uses `shell: false`, starts the GUI launcher with `windowsHide` on Windows, and uses `/bin/sh` only for the POSIX launcher on macOS/Linux.

## Deliberate omissions

- Codex tool traces and hidden thinking are not captured.
- No speculative Codex subagent filtering is added without a stable host field.
- The Recall Skill does not call `auto_memory`; lifecycle hooks own automatic recording.
- The plugin does not alter ReMe health, LLM, embedding, or App Execution Alias configuration.
- The plugin does not scan every PATH directory.

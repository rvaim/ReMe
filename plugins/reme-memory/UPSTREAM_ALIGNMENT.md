# Upstream alignment

This plugin intentionally separates shared behavior from host-specific adapters.

## ReMe alignment

The following behavior follows the official ReMe Claude Code integration:

- Streamable HTTP MCP initialization and tool calls;
- a long timeout for detached auto-memory work;
- Claude Code memory writing through `auto_memory_cc(session_id)`;
- recall through ReMe `search`, `read`, `traverse`, date, and frontmatter tools;
- best-effort hooks that do not block the host when ReMe is unavailable.

The Codex adapter differs only where Codex lacks a ReMe server-side transcript resolver. It captures stable lifecycle fields, uses UTF-8 bytes, emits AgentScope-compatible `role + name + content` messages with stable IDs, and submits only the current completed turn.

## Dual-platform layout

The layout follows the dual-plugin principles documented by `rvaim-marketplace/plugins/plugin-creator`:

- `.claude-plugin/plugin.json` and `.codex-plugin/plugin.json` coexist in one target plugin;
- shared `skills/`, runtime code, documentation, and templates stay in the plugin root;
- Claude Code and Codex hooks use separate `hooks/claude-hooks.json` and `hooks/codex-hooks.json` files;
- there is no shared `hooks/hooks.json` that could be auto-discovered by the wrong host;
- both manifests use the same plugin name and version.

## Automatic recall additions

Automatic recall is a plugin-level enhancement, not an upstream ReMe server modification:

- deterministic rules handle high-confidence recall and skip cases;
- ambiguous cases use only the OpenAI Responses API from the independent shared configuration;
- Responses requests use `reasoning.effort=none`, `store=false`, and strict JSON Schema output;
- only a `recall` decision triggers ReMe search/read;
- retrieved memory is injected as guarded historical context;
- the Recall-only skill remains available for deeper agentic retrieval.

## Shared LLM configuration

The remote Gate configuration is plugin-owned and intentionally separate from the ReMe service process. It lives at `~/.reme/config/llm.json`, has a reusable `default` section, and allows a `recall_gate` feature section to override only the values it needs. If the feature section is absent, the Gate inherits the default LLM settings and built-in conservative recall limits.

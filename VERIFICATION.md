# Verification report for 0.3.2+universal.local-20260812-0628Z

The build is validated with local mock OpenAI Responses and ReMe Streamable HTTP MCP servers.

Covered behavior:

- explicit-history rule recall;
- explicit self-contained rename skip without API or ReMe access;
- ambiguous prompt OpenAI Responses routing;
- JSON-mode Gate instructions explicitly contain valid JSON wording plus recall/skip JSON examples;
- `reasoning.effort=none`, `store=false`, and `text.format.type=json_object` request field plus strict local `decision/query` validation;
- Responses `recall` and `skip` decisions;
- exact two-key Gate JSON contract (`decision`, `query`) with extra-field and invalid-query rejection;
- no legacy recall-specific config filename or environment alias in runtime or documentation;
- Responses failure fail-open behavior;
- ReMe `search` plus bounded `read` calls;
- guarded additional-context JSON for `UserPromptSubmit`;
- Chinese UTF-8 process round-trip;
- missing configuration disables only automatic recall;
- generic `~/.reme/config/llm.json` path;
- `default` fallback when `recall_gate` is absent;
- `recall_gate` override merging and `recall-gate` alias support;
- conflicting feature-section names rejected;
- Codex incremental `auto_memory` with `name=role`, stable IDs, and language hint;
- Claude official-style `auto_memory_cc` with language hint;
- FIFO serialized writes and failure retry;
- Node dispatcher to POSIX launcher to Python hook round-trip;
- separate Claude/Codex hook files and matching manifest versions;
- one shared config path and one log path;
- Windows PE32+ x64 GUI-subsystem launcher;
- Windows Python candidate order `py -3 -> python3 -> python` after explicit overrides.

The build environment does not contain the user's real Claude Code, Codex, ReMe workspace, remote provider, or Windows desktop. The package therefore includes protocol/process tests and binary inspection; the final installation still requires one real-machine smoke test.

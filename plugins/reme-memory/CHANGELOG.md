# Changelog

## 0.3.0+universal.local-20260812-0300Z

- Added conservative automatic recall for Claude Code and Codex.
- Added OpenAI Responses API routing with `reasoning.effort=none`, `store=false`, and strict JSON Schema output.
- Added ReMe search/read context injection with a bounded historical-context guardrail.
- Added generic shared `~/.reme/config/llm.json` configuration with `default` plus optional `recall_gate` overrides.
- Added fallback to `default` when `recall_gate` is absent and support for the `recall-gate` alias.
- Kept explicit self-contained tasks out of both the Gate API and ReMe retrieval.
- Preserved UTF-8, `name=role`, stable message IDs, incremental Codex writes, Claude `auto_memory_cc`, serialized retries, same-language memory generation, and no-console Windows execution.

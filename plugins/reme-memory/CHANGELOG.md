# Changelog

## 0.3.2+universal.local-20260812-0628Z

- Strengthened the Recall Gate prompt for JSON mode: explicitly require valid JSON, include valid `recall` and `skip` JSON examples, and forbid prose, Markdown, code fences, comments, or extra text.
- Kept the Responses request format as `text.format.type=json_object`, with the existing two-field `decision/query` contract and strict local validation.
- Kept the configured `reasoning_effort` and output-token budget unchanged.

## 0.3.1+universal.local-20260812-0613Z

- Changed the Recall Gate Responses request from `json_schema` to `json_object` for providers that reject Structured Outputs.
- Kept the Gate output contract to exactly `decision` and `query`, with strict local validation and no confidence field.
- Removed the legacy recall-specific config environment alias; the shared config is only `~/.reme/config/llm.json` unless `REME_LLM_CONFIG` explicitly overrides it.

## 0.3.0+universal.local-20260812-0300Z

- Added conservative automatic recall for Claude Code and Codex.
- Added OpenAI Responses API routing with `reasoning.effort=none`, `store=false`, and strict JSON Schema output.
- Added ReMe search/read context injection with a bounded historical-context guardrail.
- Added generic shared `~/.reme/config/llm.json` configuration with `default` plus optional `recall_gate` overrides.
- Added fallback to `default` when `recall_gate` is absent and support for the `recall-gate` alias.
- Kept explicit self-contained tasks out of both the Gate API and ReMe retrieval.
- Preserved UTF-8, `name=role`, stable message IDs, incremental Codex writes, Claude `auto_memory_cc`, serialized retries, same-language memory generation, and no-console Windows execution.

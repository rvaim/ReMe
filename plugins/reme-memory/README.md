# ReMe Memory 0.3.0+universal.local-20260812-0300Z

A dual-host Claude Code and Codex plugin for persistent ReMe memory, conservative automatic recall, and automatic memory writing.

## Requirements

- ReMe installed and its MCP service available at `http://127.0.0.1:2333/mcp` by default.
- Python 3.10 or newer.
- Node.js for the Claude Code hook dispatcher.
- Claude Code and/or Codex with plugin hooks enabled.
- An OpenAI Responses API-compatible remote model when LLM-gated recall is enabled.

No Go compiler is required. The Windows no-console launcher is prebuilt.

## Recall behavior

Each `UserPromptSubmit` follows this flow:

1. Explicit references to earlier sessions, durable preferences, or prior decisions recall immediately.
2. Clearly self-contained tasks, including ordinary variable renames, skip recall immediately.
3. Ambiguous prompts call the configured OpenAI Responses API with `reasoning.effort=none`, `store=false`, and strict `text.format.type=json_schema` output.
4. Only a `recall` decision calls ReMe `search`, reads a small number of matching notes, and injects a bounded `<reme_recalled_memory>` context block.
5. Gate API or ReMe retrieval failures fail open: the original prompt continues without automatic memory context.

Automatic context is historical background, not instructions. The bundled skill remains available for deeper `search`, `read`, `traverse`, date, and frontmatter retrieval.

## Shared LLM configuration

Create one extensible configuration file:

`~/.reme/config/llm.json`

Initialize it from the extracted marketplace.

Windows:

`py -3 .\plugins\reme-memory\bin\init-llm-config.py`

macOS/Linux:

`python3 ./plugins/reme-memory/bin/init-llm-config.py`

Example:

```json
{
  "version": 1,
  "default": {
    "enabled": true,
    "api": "responses",
    "base_url": "https://api.example.com/v1",
    "api_key": "REPLACE_ME",
    "model": "REPLACE_ME",
    "timeout_seconds": 8,
    "reasoning_effort": "none",
    "max_output_tokens": 120
  },
  "recall_gate": {
    "recent_user_messages": 2,
    "max_prompt_chars": 5000,
    "search_limit": 5,
    "inject_limit": 3,
    "max_context_chars": 6000,
    "reme_timeout_seconds": 3,
    "min_score": null
  }
}
```

The plugin merges `default` with `recall_gate`; values in `recall_gate` override the corresponding default. If `recall_gate` is absent, automatic recall uses `default` plus built-in conservative recall settings. The alias `recall-gate` is also accepted, but do not define both names at once.

Future LLM-backed plugin features can add their own sections and inherit the same `default` endpoint, key, model, and request settings. Set the merged `enabled` value to `false`, or remove the file, to disable automatic recall while retaining automatic memory writing and the ReMe skill.

The Gate sends the current prompt and a small number of recent user prompts to `{base_url}/responses`. It does not send recalled ReMe memory to the Gate model. Protect this file because it contains an API key. On macOS/Linux:

`chmod 600 ~/.reme/config/llm.json`

The key is never copied into the plugin cache and is never written to logs. Set `REME_LLM_CONFIG` only when a non-default config path is required. The old `REME_RECALL_GATE_CONFIG` environment variable remains accepted as a compatibility alias.

## Start ReMe

`reme start service.backend=mcp service.transport=streamable-http`

If ReMe uses another endpoint, edit `.mcp.json` before installation or set `REME_MCP_URL` in the host process environment.

## Install in Codex

Remove or disable older ReMe/Codex plugins first to avoid duplicate hooks.

`codex plugin marketplace add "<absolute path to reme-universal-marketplace>"`

`codex plugin add reme-memory@reme-local`

Restart Codex, open a new thread, and approve the plugin hooks if trust is requested.

Current Codex versions may render `UserPromptSubmit.additionalContext` as a visible developer/hook-context entry. The context is still supplied to the model; this is a host presentation behavior rather than a plugin failure.

## Install in Claude Code

Remove or disable an older official/manual ReMe Stop hook first to avoid duplicate writes.

`claude plugin marketplace add "<absolute path to reme-universal-marketplace>"`

`claude plugin install reme-memory@reme-local`

Restart Claude Code and inspect `/hooks`. Claude Code uses exec-form Node hooks with no shell interpolation.

## Automatic write behavior

- Claude Code keeps the official ReMe design: Stop queues `auto_memory_cc(session_id, memory_hint)`, and ReMe resolves the Claude transcript server-side.
- Codex captures stable `UserPromptSubmit` and `Stop` fields, sends only the completed current turn to generic `auto_memory`, includes `name=role`, stable message IDs, UTF-8 text, and the same-language memory hint.
- Both hosts use a persistent FIFO writer with cross-process serialization. Failed ReMe writes remain queued and retry after a later hook.
- ReMe-generated memory is instructed to use the primary language of the user's conversation while preserving code, commands, paths, API names, identifiers, and direct quotations.

## Logs and state

All diagnostic logging goes to one file:

`~/.reme/log/reme-plugin.log`

Logs include routing decisions, latency, recall counts, and write status. They do not include the API key or full recalled memory text.

Transactional queue/session state is stored under the host-provided plugin data directory. That state is required for idempotency and retry and is not a diagnostic log.

## Python selection

Windows:

`REME_PYTHON -> REME_CODEX_PYTHON -> py -3 -> python3 -> python`

macOS/Linux:

`REME_PYTHON -> REME_CODEX_PYTHON -> python3 -> python`

Every candidate is executed as a real Python 3.10+ probe. A Microsoft Store `WindowsApps` placeholder that exits with code 9009 is rejected automatically.

## Smoke test

1. Start ReMe and verify `reme health_check`.
2. Configure `~/.reme/config/llm.json`.
3. In one host, say: `Remember that this project uses reversible database migrations.`
4. Let the turn complete and confirm a successful write in `~/.reme/log/reme-plugin.log`.
5. Open a new session and ask: `Use my previous database migration preference.`
6. Confirm a Gate recall decision, ReMe search/read activity, and recalled context.

A prompt such as `Rename this variable to result_count` should skip both the remote Gate and ReMe retrieval.

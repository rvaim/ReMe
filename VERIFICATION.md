# Verification record — 2026-08-11

## Release under test

```text
0.2.0+universal.local-20260811-1021Z
```

Development-tree regression suite: **20 passed**.

The release ZIP is also extracted into a fresh directory and the same 20-test suite is rerun against the extracted copy before delivery.

## Dual-host packaging

Verified:

- Root Codex marketplace exists at `.agents/plugins/marketplace.json`.
- Root Claude Code marketplace exists at `.claude-plugin/marketplace.json`.
- Plugin name and version match in `.codex-plugin/plugin.json` and `.claude-plugin/plugin.json`.
- Both marketplace entries resolve to `./plugins/reme-memory`.
- Codex manifest explicitly selects `hooks/codex-hooks.json`.
- Claude Code manifest explicitly selects `hooks/claude-hooks.json`.
- The release contains no default `hooks/hooks.json`, preventing either host from auto-loading the other host's schema.
- MCP and Skill are shared rather than copied into host-specific directories.
- JSON syntax validation passes for every manifest, marketplace, MCP, and hook file.
- Python compilation, POSIX shell syntax validation, and `node --check` validation pass.

## Conversation-language preservation

Verified on both host paths:

- A single language-neutral policy is passed through ReMe's official `memory_hint` parameter.
- The policy requires the generated memory title, frontmatter name/description, headings, summary, facts, procedures, and prose to use the dominant user conversation language.
- The policy forbids translation into a default language.
- Intentional mixed-language sessions remain mixed; connective prose follows the dominant user language.
- Code, commands, paths, identifiers, API names, and verbatim quotations remain unchanged.
- The policy contains no hard-coded preference for Chinese or English.
- Changing the language policy changes the Codex deterministic job identity, preventing an old completion marker from silently suppressing a changed memory request.

The test environment validates the exact MCP request content. It does not contain the user's configured ReMe LLM, so it cannot independently prove that every possible model will obey the hint in generated prose.

## Claude Code path

Verified:

- Only the Stop hook is registered for Claude Code.
- Host detection selects Claude from its hook payload/environment.
- The plugin calls `auto_memory_cc` with exactly `session_id + memory_hint`.
- No conversation messages are sent by the Claude hook.
- Claude Code uses command-hook exec form: `command: node` with an explicit `args` array; no hook shell parses the plugin path.
- The dependency-free Node dispatcher is syntax-checked and contains no npm imports.
- The process-level Node dispatcher -> POSIX launcher -> detached Claude worker -> mock Streamable HTTP MCP flow succeeds.
- The mock server observes `initialize -> notifications/initialized -> tools/call(auto_memory_cc)`.
- The dispatcher uses `shell: false`, preserves stdin as bytes, and sets `windowsHide: true`.
- Source-policy checks verify that Windows selects the bundled GUI launcher while macOS/Linux selects the POSIX launcher.
- Neither the hook config nor dispatcher selects `powershell.exe`, `pwsh.exe`, `cmd.exe`, or a shell version.

## Codex path

Verified:

- Hook stdin is read as raw bytes and decoded as strict UTF-8.
- A real subprocess with deliberately non-UTF-8 Python text-stream settings preserves Chinese content byte-for-byte.
- `UserPromptSubmit` records a provisional prompt.
- `Stop` emits only the matching completed turn and never replays prior turns.
- Every message has AgentScope-compatible stable `id`, `role`, `name`, `content`, and `created_at`; `name = role`.
- Stable UUIDs are derived from `session_id + turn_id + role`.
- Duplicate identical Stop events resolve to the same job and are skipped after completion.
- The deterministic job payload includes `memory_hint`.
- FIFO jobs are processed by one writer with no overlapping ReMe calls.
- Transport errors, MCP `isError=true`, and known ReMe application-error text stay queued for retry.
- Completion markers are written before successful queue removal.
- The writer closes the enqueue/lock-release missed-wakeup window.
- Invalid queue files are quarantined so later jobs can proceed.
- The process-level POSIX launcher -> detached writer -> mock Streamable HTTP MCP flow succeeds.
- The mock server observes `initialize -> notifications/initialized -> tools/call(auto_memory)` with Unicode messages, official schema, and `memory_hint` intact.

## Python discovery

macOS/Linux order:

```text
REME_PYTHON -> REME_CODEX_PYTHON -> python3 -> python
```

Windows order:

```text
REME_PYTHON -> REME_CODEX_PYTHON -> py -3 -> python3 -> python
```

Verified:

- Every candidate runs a real Python >=3.10 probe.
- Per-candidate timeout is finite at three seconds.
- A discoverable but broken `python3` is rejected and `python` is selected.
- The neutral `REME_PYTHON` override wins over legacy and command-name candidates.
- Probe processes do not consume the original hook stdin.
- No PATH-directory enumeration, hard-coded Python installation folder, registry discovery, or App Execution Alias modification is used.

## Windows binary

The Windows launcher was rebuilt in the build environment. Go is **build-time only**; no Go source or Go runtime is included in the release.

```text
Format:       PE32+ x86-64
Subsystem:    IMAGE_SUBSYSTEM_WINDOWS_GUI (2)
OS target:    Windows 6.01+
SHA-256:      18c465d58b167c33989801640e341c02b8b945d3e1c82c611a6aa874961a59a3
Size:         2,220,032 bytes
```

Source-policy tests verify:

- both Claude and Codex plugin-root variables are supported;
- selected Python is actually probed;
- probe, foreground hook, Codex writer, and Claude worker use hidden/no-console process attributes;
- only `~/.reme/log/reme-plugin.log` is used for diagnostics;
- no PowerShell executable or version is selected by the native launcher or Claude dispatcher.

## Recall Skill

Verified:

- Skill frontmatter name is `reme-memory`.
- Description covers Claude Code and Codex.
- Recall flow remains based on the official ReMe Claude Code plugin.
- Retrieval tools include `search`, `traverse`, `daily_list`, `frontmatter_read`, and `read`.
- Skill does not duplicate automatic `auto_memory` writing.
- Shared MCP endpoint is `http://127.0.0.1:2333/mcp`.

## Release hygiene

The delivered ZIP is checked to contain no:

```text
.go
.pyc
__pycache__/
.pytest_cache/
tests/
build/
```

Executable bits on POSIX launchers are retained in the ZIP.

## Build-environment limits

The build sandbox is Linux and has no installed Claude Code CLI, Codex CLI, target Windows/macOS desktop environment, or the user's configured ReMe service/model. Therefore:

- Claude/Codex manifest and hook behavior are validated structurally and through process-level host simulations, not by claiming a real target-host installation.
- Windows is cross-compiled and PE-inspected, not run under the user's Windows session.
- ReMe calls are verified against a protocol-accurate mock MCP server.
- Real memory-file generation, backend health, embedding configuration, and model compliance with `memory_hint` must still be smoke-tested against the user's ReMe instance.
- The exact requested `rvaim/rvaim-marketplace/plugins/plugin-creator` page was not retrievable from this build environment. The dual-manifest layout was cross-checked against current official Claude Code and Codex plugin specifications and separately indexed universal marketplace implementations.

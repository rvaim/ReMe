# Verification record — 2026-08-11

## What was verified locally

- Python syntax compilation: PASS.
- Plugin/unit/protocol tests: **14 passed**.
- Process-level hook round trip: PASS. The real hook script was launched as a subprocess for `UserPromptSubmit` and `Stop`, detached using the POSIX production path, and completed MCP `initialize -> notifications/initialized -> tools/call` against a mock ReMe server.
- ReMe call contract asserted by test: `auto_memory({session_id, messages})`, with messages limited to `role + content`.
- Codex rollout/transcript file is never parsed by the adapter: PASS.
- JSON package files parse: PASS.
- Marketplace/manifest shape checks: PASS.
- ReMe MCP default config exactly matches the official Claude Code plugin endpoint: PASS.
- Recall-only skill alignment checks: PASS.
- Windows launcher prebuilt binary: PASS. Build-time toolchain was used only when producing the release; **Go is not a runtime dependency and launcher source is not included in the runtime ZIP**.
- Windows launcher format: **PE32+ x86-64, IMAGE_SUBSYSTEM_WINDOWS_GUI (2)**.
- Windows launcher source: `HideWindow=true` + `CREATE_NO_WINDOW` for the Python child.
- Windows Python background worker: `CREATE_NO_WINDOW` + hidden startup info.

Windows launcher SHA-256:

`19552ba37b6d599e82c3daf8a6ade252e5f0af527c94a29e566cd5486e9eb7dc`

## What could not be verified in this build environment

The build sandbox is Linux and does not contain the user's configured `codex` or `reme` executable, Windows, or Wine. Therefore this package has **not** been falsely labelled as having completed a real Windows Codex + real ReMe end-to-end test. The included automated tests validate the package contract, MCP wire flow, hook state handling, process detachment, and Windows binary properties; the final real-machine smoke test must run on the target Codex/ReMe installation.

## Upstream basis

The plugin intentionally follows `agentscope-ai/ReMe/plugins/claude_code/reme` for the skill, MCP endpoint, stdlib MCP transport, JSON/SSE handling, timeout, POSIX detachment, and best-effort failure behavior. Codex-specific code is isolated to lifecycle-field capture, bounded session state, the generic `auto_memory` call, and the Windows no-console launcher.

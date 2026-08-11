# ReMe for Codex（本地 marketplace）

这是一个 **ReMe → Codex 适配插件**。目标是尽量保持 ReMe 官方 Claude Code 插件的实现和行为，只在 Codex 必须不同的地方做适配。

## 设计基线

上游基线：`agentscope-ai/ReMe` 当前 `main` 中的 `plugins/claude_code/reme`。

保留/直接对齐的部分：

- `.mcp.json`：与 ReMe 官方 Claude Code 插件相同，连接 `http://127.0.0.1:2333/mcp`。
- `skills/reme-memory/SKILL.md`：逐段基于官方 `reme-memory` skill；只把 Claude Code / 插件路径措辞改成 Codex。
- `hooks/auto_memory.py` 的 MCP transport：保留官方的 stdlib HTTP 客户端、MCP initialize → initialized → tools/call、JSON/SSE 解析、600 秒后台调用上限、POSIX double-fork 和 best-effort 日志语义。

Codex 必须新增的部分：

- `UserPromptSubmit` 读取稳定字段 `session_id / turn_id / prompt`。
- `Stop` 读取稳定字段 `last_assistant_message`。
- 不解析 `transcript_path` 的 Codex rollout JSONL；本地只缓存已经由 hook contract 提供的 prompt / final assistant message。
- 调 ReMe 通用 `auto_memory(session_id, messages)`，而不是 Claude 专用 `auto_memory_cc(session_id)`。
- Windows 使用 GUI-subsystem launcher，并对子 Python worker 设置 `CREATE_NO_WINDOW`。

详细差异见 `plugins/codex/reme/UPSTREAM_ALIGNMENT.md`。

## 安装

**运行本插件不需要 Go、gcc、Visual Studio 或任何编译器。** Windows 无黑窗启动器已经以 `bin/reme-hook-launcher.exe` 形式预编译在包内；`main.go` 只是构建时源码，不属于运行依赖，本发布包已移除。

你已经配置好 ReMe 的话，按下面做即可：

1. 使用本仓库根目录（`C:\Users\49333\Project\ReMe`）作为 marketplace 路径，不要只使用 `plugins/codex/reme` 子目录。
2. 确认 ReMe MCP 服务正在运行。默认地址是 `127.0.0.1:2333`；如果你改过端口，同步编辑 `plugins/codex/reme/.mcp.json`。
3. 添加本地 marketplace：`codex plugin marketplace add "C:\Users\49333\Project\ReMe"`。
4. 安装插件：`codex plugin add reme@reme-marketplace`。
5. 重新打开一个 Codex 会话。第一次加载 hook 时，如果 `/hooks` 要求 trust/approve，请批准本插件的两个 hook。
6. Windows 如果找不到 Python，可设置 `REME_CODEX_PYTHON` 为你现有 ReMe 环境里的 `python.exe` 完整路径；优先推荐同一个 Python 环境。

## Windows“不能弹黑窗口”的边界

插件自身做了两层处理：

- `bin/reme-hook-launcher.exe` 编译为 **Windows GUI subsystem**，不是 console subsystem。
- launcher 和后台 Python worker 都使用 `CREATE_NO_WINDOW`，并关闭 stdout/stderr，所以**插件自己启动的进程不会创建终端窗口**。

但截至 2026-08-11，Codex 的 Windows command-hook runner 仍由 Codex 自己先启动 `cmd.exe /C`，其公开源码没有统一设置 `CREATE_NO_WINDOW`；Codex Desktop 也有仍处于 Open 状态的后台 PowerShell/console 闪窗问题。因此：

- **Codex CLI 已经在终端中运行**：本插件不会再额外弹一个插件终端窗口。
- **Codex Desktop / GUI**：插件内部进程是无窗口的，但 Codex 宿主创建外层 command-hook shell 时仍可能闪一下。这个行为不在插件进程控制范围内。

如果你的要求是“Codex Desktop 下绝对不能因为插件 hook 触发任何 command shell”，当前 Codex runtime 做不到自动记忆 + 这个保证同时成立。包内提供 `hooks/hooks.recall-only.json`：把它覆盖为 `hooks/hooks.json` 后再安装，可完全关闭本插件 command hooks，只保留官方风格的 ReMe MCP recall + skill；代价是没有自动 `auto_memory`。

## 运行机制

正常自动模式：

- `UserPromptSubmit`：快速记录当前 user prompt 到插件私有状态。
- `Stop`：把同一 `turn_id` 的最终 assistant message 补齐，构造当前 session 的完整 user/assistant 消息列表。
- 后台调用 ReMe MCP `auto_memory`。失败只记日志，不阻塞 Codex。
- 同一 turn 重复触发 Stop 时会更新该 turn，不追加重复消息。
- 不读取 Codex transcript JSONL，因此不依赖其内部 rollout schema。

状态/日志默认位置：

- 若宿主提供 `PLUGIN_DATA`，使用该目录。
- Windows fallback：`%LOCALAPPDATA%\ReMeCodex`。
- Linux/macOS fallback：`$XDG_STATE_HOME/reme-codex` 或 `~/.local/state/reme-codex`。

## 本地验证说明

本包在生成环境执行了 Python 单元测试、mock MCP 协议集成测试、JSON/manifest 检查、Python 编译检查，并在发布前构建/检查了 Windows x64 launcher 的 PE subsystem。运行本插件不需要 Go；发布 ZIP 只携带已编译的 `.exe`。

生成环境没有安装你的 `reme`、`codex`，也没有 Windows/Wine，因此无法冒充“已经在你的真实 Windows + Codex + ReMe 环境跑过端到端”。安装后建议做一次最小 smoke test：在一个会话里明确告诉 Codex 一个独特偏好，完成一轮回复后打开新会话询问该偏好，并观察 ReMe workspace / hook log 是否出现对应记录。

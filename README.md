# ReMe Memory（Claude Code + Codex 通用 Marketplace）

这是一个同时支持 **Claude Code** 与 **OpenAI Codex** 的 ReMe 长期记忆插件。它以 ReMe 官方 Claude Code 集成为主基线，只在 Codex 缺少服务端 transcript adapter 的位置增加必要适配。两个宿主共用一个插件目录、一份 Recall Skill、一份 MCP 配置和同一条记忆语言策略；manifest 与 hook 配置仅保留宿主协议要求的最小差异。

仓库本身就是一个 marketplace：直接在 Codex / Claude Code 中把本仓库 URL 添加为 marketplace 即可在线安装，无需克隆、打包或解压。

当前版本：`0.2.0+universal.local-20260811-1021Z`。

## 主要能力

- Claude Code 与 Codex 共用同一个 `reme-memory` 插件目录。
- 两个宿主共用 `http://127.0.0.1:2333/mcp` 和 Recall-only Skill。
- Claude Code 在 Stop 时调用 ReMe 官方 `auto_memory_cc(session_id)` transcript 适配器。
- Codex 使用 `UserPromptSubmit + Stop` 捕获当前完成 turn，并调用通用 `auto_memory`。
- 自动记忆遵循会话主语言：以用户消息的主自然语言为准，生成的标题、frontmatter、Markdown 标题、摘要、事实、过程和说明文字使用同一种语言。
- Windows 与 macOS 均支持；运行时不需要 Go 或编译器。
- Codex Windows 继续使用预编译 GUI/no-console launcher，且不固定 PowerShell 版本。
- Claude Code 使用官方 command-hook exec form（`node` + 参数数组），不依赖 Claude 当前选择的 Bash 或 PowerShell；dispatcher 不需要 npm 包。

## 语言一致性

每一次自动记忆写入都会向 ReMe 传递同一条严格的 `memory_hint`：

1. 从用户撰写的会话内容判断主自然语言。
2. 记忆标题、frontmatter 的名称与描述、Markdown 标题、摘要、事实、过程和连接性文字使用该语言。
3. 不把会话翻译成英文或其他默认语言。
4. 会话有意混用语言时保留混用方式，并使用主用户语言撰写连接性文字。
5. 代码、命令、路径、标识符、API 名称和原文引用保持原样。

该策略在 ReMe 生成记忆之前通过官方 `memory_hint` 参数注入，不在生成后再次翻译或重写。这样不会破坏代码、路径和精确引用。最终文字由你配置给 ReMe 的模型生成，因此插件可以保证请求携带约束，但不能替模型保证百分之百服从。

## 双宿主架构

| 宿主 | 生命周期数据源 | 写入工具 | 去重/增量方式 |
|---|---|---|---|
| Claude Code | Stop hook 只传 `session_id`；ReMe 服务端读取 Claude transcript | `auto_memory_cc` | ReMe 官方 `CcFileSessionStore` 按 transcript UUID 去重，只处理新增条目 |
| Codex | `UserPromptSubmit.prompt` + 同一 turn 的 `Stop.last_assistant_message` | `auto_memory` | 插件只入队当前完成 turn；稳定 message UUID、完成标记与 FIFO 单 writer 保证幂等 |

Claude Code 路径不把会话正文发送给 ReMe，也不重新实现 transcript parser。Codex 当前没有 ReMe 官方的服务端 transcript resolver，因此只在 Codex 路径维护必要的本地增量队列。

## 通用插件目录

```text
ReMe/                                  # 仓库根目录 = marketplace 根目录
├── .agents/plugins/marketplace.json   # Codex marketplace
├── .claude-plugin/marketplace.json    # Claude Code marketplace
└── plugins/reme-memory/
    ├── .codex-plugin/plugin.json       # Codex manifest
    ├── .claude-plugin/plugin.json      # Claude Code manifest
    ├── .mcp.json                       # 两个宿主共用
    ├── hooks/
    │   ├── codex-hooks.json            # Codex lifecycle 配置
    │   ├── claude-hooks.json           # Claude Code lifecycle 配置
    │   └── auto_memory.py              # 共用 MCP/记忆适配实现
    ├── skills/reme-memory/SKILL.md     # 共用 Recall-only Skill
    └── bin/
        ├── reme-hook-dispatch.js       # Claude Code 跨平台 exec-form dispatcher
        ├── reme-hook-launcher          # macOS/Linux Python launcher
        └── reme-hook-launcher.exe      # Windows GUI/no-console launcher
```

两个 manifest 都显式引用自己的 hook 文件：

```text
Claude Code -> ./hooks/claude-hooks.json
Codex      -> ./hooks/codex-hooks.json
```

发布包故意**不包含**默认的 `hooks/hooks.json`。这样 Claude Code 不会自动加载 Codex 的 `commandWindows`，Codex 也不会误读 Claude 的 `args` exec-form；共享的是业务实现和资源，不是互不兼容的宿主命令语法。

## 升级前移除重复集成

不要让两套自动记忆 hook 同时处理同一宿主：

- Codex：停用或卸载旧的 `reme-codex@reme-local`，再安装本包的 `reme-memory@reme-local`。
- Claude Code：如果已安装 ReMe 官方 Claude Code 插件或其他会调用 `auto_memory_cc` 的 Stop hook，请停用其中一个。

重复安装会让同一 Stop 触发多次。即使 transcript adapter 能去重，也会产生多余调用和日志。

## 前置条件

你已经安装并配置 ReMe，并具备 Python 3.10 或更高版本。

Claude Code 的跨平台 dispatcher 还要求 `node` 命令可用。它是一个零依赖 JavaScript 文件，不需要 `npm install`；Codex 路径不依赖 Node。

启动 ReMe MCP 服务：

```text
reme start service.backend=mcp service.transport=streamable-http
```

默认 endpoint：

```text
http://127.0.0.1:2333/mcp
```

如使用其他 host/port，编辑：

```text
plugins/reme-memory/.mcp.json
```

## 安装到 Codex

直接从 GitHub 在线添加 marketplace（无需克隆）：

```text
codex plugin marketplace add https://github.com/rvaim/ReMe
```

然后启动或重启 Codex，在 `/plugins` 中打开 `ReMe Local` marketplace 并安装 `reme-memory`。部分 Codex 版本也提供：

```text
codex plugin add reme-memory@reme-local
```

安装后新建 Codex thread，使新版 manifest、MCP、Skill 和 hooks 全部重新加载。第一次加载 hook 时，在 Codex 的 hooks/trust 界面批准插件命令。

更新插件时重新拉取 marketplace 快照即可：

```text
codex plugin marketplace update reme-local
```

Codex hook 行为：

- `UserPromptSubmit`：按 UTF-8 保存 provisional prompt。
- `Stop`：补齐最终 assistant message，只入队当前 turn。
- 后台单 writer 按 FIFO 调用 ReMe；失败任务保留，并在后续 Stop 重试。

## 安装到 Claude Code

直接从 GitHub 在线添加 marketplace（无需克隆）。在 Claude Code 中执行：

```text
/plugin marketplace add https://github.com/rvaim/ReMe
/plugin install reme-memory@reme-local
```

也可以使用非交互 CLI：

```text
claude plugin marketplace add "https://github.com/rvaim/ReMe"
claude plugin install reme-memory@reme-local
```

重新打开 Claude Code session，然后用 `/hooks` 检查来自 `reme-memory` 的 Stop hook。Claude Code 路径只发送 `session_id + memory_hint`，ReMe 服务端自行加载、过滤并去重 transcript。

更新插件时重新拉取 marketplace 即可：

```text
/plugin marketplace update reme-local
```

## Python 解释器选择

推荐使用中性的显式变量：

```text
REME_PYTHON=<Python 解释器绝对路径>
```

为兼容旧版，仍识别 `REME_CODEX_PYTHON`。

Windows：

```text
REME_PYTHON -> REME_CODEX_PYTHON -> py -3 -> python3 -> python
```

macOS/Linux：

```text
REME_PYTHON -> REME_CODEX_PYTHON -> python3 -> python
```

每个候选都必须在 3 秒内通过真实的 Python >= 3.10 probe。Windows App Execution Alias 占位程序若返回 9009，会被拒绝并继续 fallback。插件不全量枚举 PATH、不硬编码 Python 安装目录、不读取注册表，也不修改 App Execution Alias。

## Windows 无终端黑窗口边界

Codex Windows hook 保持宿主当前 hook shell 的调用语法：

```text
& "${PLUGIN_ROOT}\bin\reme-hook-launcher.exe" --capture
& "${PLUGIN_ROOT}\bin\reme-hook-launcher.exe" --stop
```

插件不指定 `powershell.exe`、`pwsh.exe` 或版本。预编译 launcher 是 Windows GUI subsystem 程序；Python probe、hook 进程和 detached writer 使用 no-console 标志。

Claude Code 使用官方 exec form：

```json
{
  "command": "node",
  "args": [
    "${CLAUDE_PLUGIN_ROOT}/bin/reme-hook-dispatch.js",
    "--stop"
  ]
}
```

dispatcher 使用 `shell: false`。Windows 下直接启动同一个 GUI launcher 并设置 `windowsHide`; macOS/Linux 下把 stdin 原始字节交给 POSIX launcher。它不指定 `powershell.exe`、`pwsh.exe`、`cmd.exe` 或某个 shell 版本。宿主自身如何创建最外层 hook 进程由相应 Claude Code/Codex 版本负责。

## 状态与日志

所有插件诊断只写一个文件：

```text
~/.reme/log/reme-plugin.log
```

Windows：

```text
C:\Users\<user>\.reme\log\reme-plugin.log
```

macOS：

```text
/Users/<user>/.reme/log/reme-plugin.log
```

Codex 事务状态使用 `PLUGIN_DATA`，可能包含：

```text
sessions/
queue/
completed/
failed/
```

这些是队列/幂等状态，不是额外日志。Claude Code 路径不使用 Codex turn queue；ReMe 自行保存 Claude transcript increment。

## Skill 与 Recall

共用 Skill 继续保持 ReMe 官方 Claude Code 插件的 **recall-only** 设计。涉及过去会话、偏好、项目历史或决策时，通过 ReMe MCP 使用：

```text
search
traverse
daily_list
frontmatter_read
read
```

自动写入由 lifecycle hook 负责，不在 Skill 中重复调用 `auto_memory`。

## 验证边界

本包完成了双 marketplace/双 manifest/独立 hook 路径、语言 hint、UTF-8、Codex 增量与串行队列、Claude `auto_memory_cc`、Streamable HTTP MCP、Python fallback、Windows GUI launcher 和最终 ZIP 解压回归测试。详见 `VERIFICATION.md` 与 `plugins/reme-memory/UPSTREAM_ALIGNMENT.md`。

构建环境没有你的真实 Windows/macOS、Claude Code、Codex 和 ReMe 模型配置，因此不会声称已经在你的目标机器完成真实 E2E。测试验证 hook、编码、MCP 参数、增量/幂等、进程属性和包结构；实际 ReMe 服务健康状态以及模型对语言 hint 的遵循仍需在目标环境 smoke test。

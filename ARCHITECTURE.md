# ForgeCoder 设计说明

更新日期：2026-11-18。本文件记录目标、架构、框架模块映射与目录规划。

## 1. 目标

1. **能用**：用户用自然语言提出需求，Agent 在本地仓库（`repos/` 下的项目，不连接远程 GitHub）中完成"读懂代码 → 规划 → 修改 → 跑测试 → 修复"的闭环，所有改动保留修改记录供用户 code review 后决定接受或丢弃。
2. **能展示**：每个功能都对应 ForgeAgents 的具体模块，并能在 CLI 与 Web 面板中看到该模块的作用。
3. **能讲清**：可以说明一次请求如何经过 LLM、Agent 循环、工具、权限、上下文与会话组件。

不做：IDE 插件、多模型适配大全、沙箱/容器隔离、自动提交与推送代码、大规模基准评测。

## 2. 架构

```text
           ┌──────────── 入口层 ────────────┐
           │  CLI REPL（流式输出 / 确认提示）  │   Web 面板（FastAPI + SSE 时间线）
           └───────────────┬────────────────┘
                           ▼
           ┌──────────── 应用层 forge_coder ─────────────┐
           │ CoderSession：组装 Agent、工具、策略、会话      │
           │ 模式：chat（ReAct） / plan（PlanSolve）        │
           │ WorkspaceManager：任务分支 + git worktree      │
           │ 子 Agent：Explorer（只读） / Tester（运行）     │
           │ 确认策略 ConfirmPolicy、工作区路径约束          │
           │ 自建工具：Glob / Grep / ListDir / RunCommand    │
           └───────────────┬────────────────────────────┘
                           ▼
           ┌──────────── 框架层 forge_agents ─────────────────────────┐
           │ ReActAgent / PlanSolveAgent   ToolRegistry + ToolPolicy │
           │ Read/Write/Edit/MultiEdit     TodoWrite  Task(子Agent)   │
           │ CircuitBreaker  ContextBuilder  SessionStore  MemoryStore│
           │ TraceLogger  RunBudget  StreamEvent  Skills  MCP         │
           └─────────────────────────────────────────────────────────┘
```

## 3. 核心设计决策

### 3.1 改动隔离与审查：git worktree

```text
repos/<项目>（本地 git 仓库，主分支 main）
   │  用户发起任务
   ▼
创建分支 forge/<任务ID> + worktree（data/worktrees/<项目>/<任务ID>）
   │  Agent 只在 worktree 内读写、运行命令
   │  每轮对话结束自动 commit → 形成修改记录
   ▼
用户审查：/diff（相对 main 的完整改动） /log（逐轮提交记录）
   ├─ /accept → 合并回 main，删除 worktree 与分支
   └─ /reject → 删除 worktree 与分支，main 不受影响
```

- 示例项目是本地 git 仓库，不连接远程；依赖系统已安装的 git。
- worktree 与运行数据放在 `data/`（不进入示例仓库，已加入 `.gitignore`）。

### 3.2 测试项目模板与一键还原

- `samples/<项目>/` 是只读模板（普通文件，可随 ForgeCoder 一起纳入版本管理）；`repos/<项目>/` 是由模板生成的工作仓库（已加入 `.gitignore`）。
- 还原命令 `forge-coder reset <项目>`（REPL 内 `/reset`）：删除该项目的所有 worktree、任务分支与 `data/` 下的运行数据，删除 `repos/<项目>`，再从模板复制并 `git init`，提交一次初始提交 `baseline`。
- 无论 Agent 改了什么、用户是否已 `/accept` 合并，还原后都回到完全相同的初始状态，便于重复测试。

### 3.3 简易面板

- 在 M2 提供简易 Web 面板用于阶段检查：对话输入、Agent 步骤时间线、diff 查看与接受/拒绝、还原测试项目。
- 主体功能的开发验证仍以 CLI 与 pytest 为准，面板只用于关键阶段完成后的人工检查；后续再补充 token 用量与占比、上下文窗口分布、运行轨迹等展示。

### 3.4 确认策略

| 操作 | 处理方式 |
| --- | --- |
| 读文件、搜索、列目录 | 自动执行 |
| 写文件、编辑 | 在 worktree 内自动执行，任务结束统一审查 |
| 白名单命令（如 `pytest`、`python`） | 在 worktree 内自动执行 |
| 其他命令 | 执行前需用户确认 |
| 访问工作区之外的路径 | 直接拒绝 |

### 3.5 Agent 分工

| 角色 | 实现 | 可用工具 | 职责 |
| --- | --- | --- | --- |
| 主 Agent | ReActAgent / PlanSolveAgent | 全部工具 + Task | 理解需求、规划、**唯一负责修改代码**、调度子 Agent |
| Explorer | Task 子 Agent，只读过滤 | Read / Glob / Grep / ListDir | 探索代码结构，返回摘要 |
| Tester | Task 子 Agent，自定义过滤 | Read / Glob / Grep / RunCommand | 运行测试或程序，汇报结果，不修改文件 |

## 4. 框架层

`forge_agents/` 是基于 HelloAgents 复刻的智能体框架，代码中统一使用 ForgeAgents 命名（如 `ForgeAgentsLLM`、`ForgeAgentsException`）。框架层随应用需要持续修改与优化：通用的框架能力放在 `forge_agents/`，代码开发场景专属的能力（代码搜索、命令执行、确认策略、子 Agent 角色等）放在 `forge_coder/`。

## 5. 框架模块与功能映射

| ForgeAgents 模块 | ForgeCoder 中的作用 | 展示点 |
| --- | --- | --- |
| `agents.ReActAgent` | 默认对话模式，思考—工具—观察循环 | 时间线显示 Thought / 工具调用 / Finish |
| `agents.PlanSolveAgent` | `/plan` 模式：先出计划再逐步执行 | 计划列表与逐步执行结果 |
| `tools.builtin.file_tools` | 读、写、编辑、多处编辑文件（含备份与 mtime 冲突检测） | 修改前 diff 预览 |
| `tools.ToolRegistry` + `permissions` | 自定义 `ConfirmPolicy`：只读放行，写入/命令需确认 | 拒绝与确认记录 |
| `tools.circuit_breaker` | 同一工具连续失败时熔断，防止死循环 | 熔断事件 |
| `tools.builtin.todowrite_tool` | 多步任务的待办清单 | 面板中的实时 Todo |
| `tools.builtin.task_tool` | 派出只读子 Agent 探索模块（tool_filter 限制工具） | 子 Agent 独立轨迹 |
| `context` | 大仓库下的上下文构建与截断、项目说明文件注入 | token 占用变化 |
| `core.session_store` | 保存/恢复会话 | `--resume` 继续上次任务 |
| `memory` | 记住用户偏好（代码风格、常用命令） | 跨会话生效 |
| `observability.TraceLogger` + `core.budget` | 记录每步轨迹，限制调用次数/token/时长 | Trace 文件与预算告警 |
| `core.streaming` + FastAPI SSE | 向 CLI 与 Web 推送事件流 | 实时时间线 |
| `skills` / `tools.mcp`（扩展） | 加载代码审查等技能；接入外部 MCP 工具 | 可选演示 |

框架缺少、需在应用层自建的能力：代码搜索（Glob / Grep / ListDir）、命令执行（RunCommand）、交互式确认策略（框架只提供白名单 `AllowlistPolicy`）。

## 6. 目录规划

```text
forge-coder/
├── pyproject.toml / uv.lock / .env / .env.example
├── forge_agents/       # 框架层（迁移自 forge-agents，已改名）
├── forge_coder/        # 应用层
│   ├── cli.py          # 命令行入口与 REPL
│   ├── session.py      # CoderSession：组装 Agent、工具、策略
│   ├── config.py       # 读取 .env 与运行参数
│   ├── policy.py       # 确认策略与工作区约束
│   ├── workspace.py    # WorkspaceManager：任务分支、worktree、提交、合并/丢弃
│   ├── subagents.py    # Explorer / Tester 子 Agent 定义
│   ├── prompts.py      # 系统提示词
│   ├── tools/          # Glob、Grep、ListDir、RunCommand
│   └── web/            # FastAPI SSE 服务与单页面板
├── samples/            # 测试项目模板（只读，用于生成与还原）
├── repos/              # 由模板生成的测试项目（各自为独立 git 仓库）
└── data/               # worktree、会话、Trace 等运行数据（不纳入版本管理）
```

## 7. 待确认事项

- 暂无。

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
           │ 工作模式：plan（只读规划）/ auto / manual      │
           │ WorkspaceManager：任务分支 + git worktree      │
           │ 子 Agent：Explorer（只读） / Tester（运行）     │
           │ 辅助 Agent：auto 模式下审批需确认的操作         │
           │ WorkspacePolicy：路径约束、命令白名单、需确认   │
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

### 3.4 工作模式与确认策略

三种工作模式共用同一个主 Agent 与对话历史，切换只改变可用工具与审批方式：

| 模式 | 可用操作 | 需要确认的操作如何处理 |
| --- | --- | --- |
| plan（规划） | 只读：Read / Grep / Glob / ListDir + Explorer；思考、向用户提问，输出方案 | 不会产生（写入与命令均不可用） |
| auto（自动审批） | 全部工具 | 程序级辅助 Agent 总结内容、评估风险后自动批准或拒绝，决定与完整对话记入审批记录 |
| manual（人工审批，默认） | 全部工具 | 用户确认：终端询问或面板确认卡片 |

执行模式（auto / manual）下各操作的处理：

| 操作 | 处理方式 |
| --- | --- |
| 读文件、搜索、列目录 | 自动执行 |
| 写文件、编辑 | 在 worktree 内自动执行，任务结束统一审查 |
| 白名单命令（如 `pytest`、`python`） | 在 worktree 内自动执行 |
| 其他命令 | 需要确认（按模式由辅助 Agent 或用户审批） |
| 访问工作区之外的路径 | 直接拒绝 |

### 3.5 Agent 分工

| 角色 | 实现 | 可用工具 | 职责 |
| --- | --- | --- | --- |
| 主 Agent | ReActAgent（可选 PlanSolveAgent） | 按工作模式 | 理解需求、规划、**唯一负责修改代码**、调度子 Agent |
| Explorer | Task 子 Agent，只读过滤 | Read / Glob / Grep / ListDir | 探索代码结构，返回摘要 |
| Tester | Task 子 Agent，自定义过滤 | Read / Glob / Grep / RunCommand | 运行测试或程序，汇报结果，不修改文件 |
| Reviewer | `/review` 触发，只读过滤 | Read / Glob / Grep / ListDir | 合并前审查任务 diff，给出结论、问题与建议 |
| 辅助 Agent | 单次模型调用（可单独配置模型） | 无 | auto 模式下审批需确认的操作 |

## 4. 框架层

`forge_agents/` 是基于 HelloAgents 复刻的智能体框架，代码中统一使用 ForgeAgents 命名（如 `ForgeAgentsLLM`、`ForgeAgentsException`）。框架层随应用需要持续修改与优化：通用的框架能力放在 `forge_agents/`，代码开发场景专属的能力（代码搜索、命令执行、确认策略、子 Agent 角色等）放在 `forge_coder/`。

## 5. 框架模块与功能映射

| ForgeAgents 模块 | ForgeCoder 中的作用 | 展示点 |
| --- | --- | --- |
| `agents.ReActAgent` | 默认对话模式，思考—工具—观察循环 | 时间线显示 Thought / 工具调用 / Finish |
| `agents.PlanSolveAgent` | `/plansolve`：先出计划再逐步执行（Plan-and-Solve 范式演示） | 计划列表与逐步执行结果 |
| `tools.builtin.file_tools` | 读、写、编辑、多处编辑文件（含备份与 mtime 冲突检测） | 修改前 diff 预览 |
| `tools.ToolRegistry` + `permissions` | `WorkspacePolicy`：路径约束、命令白名单、需确认；审批函数按模式由辅助 Agent 或用户审批 | 审批卡片与审批记录 |
| `tools.circuit_breaker` | 同一工具连续失败时熔断，防止死循环 | 熔断事件 |
| `tools.builtin.todowrite_tool` | 多步任务的待办清单 | 面板中的实时 Todo |
| `tools.builtin.task_tool` | 派出只读子 Agent 探索模块（tool_filter 限制工具） | 子 Agent 独立轨迹 |
| `context` | 大仓库下的上下文构建与截断、项目说明文件注入 | token 占用变化 |
| `core.session_store` | 每轮保存会话与任务信息；`--resume` 接回 worktree 继续任务 | 恢复后的对话与可撤销记录 |
| `memory.profile`（ProfileStore） | `/remember` 记录用户偏好，每次请求注入 | 跨会话生效，上下文分布中可见 |
| `observability.TraceLogger` + `core.budget` | 记录每步轨迹，限制调用次数/token/时长 | Trace 文件与预算告警 |
| `core.streaming` + FastAPI SSE | 向 CLI 与 Web 推送事件流 | 实时时间线 |
| `skills` / `tools.mcp`（扩展） | 加载代码审查等技能；接入外部 MCP 工具 | 可选演示 |

框架缺少、需在应用层自建的能力：代码搜索（Glob / Grep / ListDir）、命令执行（RunCommand）、工作区策略与审批方式（框架提供策略与审批接口，具体规则由应用实现）。

## 6. 目录规划

```text
forge-coder/
├── pyproject.toml / uv.lock / .env / .env.example
├── forge_agents/       # 框架层（迁移自 forge-agents，已改名）
├── forge_coder/        # 应用层（按职责分包）
│   ├── config.py       # 读取 .env、各角色模型、框架配置
│   ├── session.py      # CoderSession：组装工具、策略与 Agent，驱动每一轮对话
│   ├── workspace.py    # 测试项目还原、任务分支、worktree、提交、合并/丢弃
│   ├── agents/         # Agent 相关
│   │   ├── prompts.py      # 全部提示词：主 Agent、工作模式、子 Agent、辅助 Agent
│   │   ├── modes.py        # 工作模式：plan / auto / manual
│   │   ├── subagents.py    # Explorer / Tester 子 Agent
│   │   └── advisor.py      # 程序级辅助 Agent：auto 模式审批
│   ├── permissions/    # 权限
│   │   ├── policy.py       # 工作区策略：路径约束、命令白名单、需确认
│   │   └── approvals.py    # 审批管理：auto / manual 审批方式与审批记录
│   ├── context/        # 上下文
│   │   ├── providers.py    # 仓库地图、子 Agent 已读文件、项目说明 FORGE.md
│   │   └── window.py       # 上下文窗口用量与各部分分布
│   ├── tools/          # Glob、Grep、ListDir、RunCommand
│   ├── memory/         # 跨会话的用户偏好（ProfileStore）
│   ├── metrics/        # token 用量统计、固定评估任务集
│   └── ui/             # 命令行 REPL、Web 面板（FastAPI + SSE）、共用显示函数
├── samples/            # 测试项目模板（只读，用于生成与还原）
├── repos/              # 由模板生成的测试项目（各自为独立 git 仓库）
└── data/               # worktree、会话、Trace 等运行数据（不纳入版本管理）
```

## 7. 待确认事项

- 暂无。

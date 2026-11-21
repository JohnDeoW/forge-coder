# ForgeCoder 设计说明

更新日期：2026-11-21。本文件记录目标、架构、框架模块映射与目录规划。

## 1. 目标

1. **能用**：用户用自然语言提出需求，Agent 在本地仓库（`repos/` 下的项目，不连接远程 GitHub）中完成"读懂代码 → 规划 → 修改 → 跑测试 → 修复"的闭环，所有改动保留修改记录，供用户 code review 后合并或丢弃。
2. **能展示**：每个功能都对应 ForgeAgents 的具体模块，并能在 CLI 与 Web 面板中看到该模块的作用。
3. **能讲清**：可以说明一次请求如何经过 LLM、Agent 循环、工具、权限、上下文与会话组件。

不做：IDE 插件、多模型适配大全、沙箱/容器隔离、自动提交与推送代码、大规模基准评测。

## 2. 架构

```text
           ┌──────────── 入口层 ────────────┐
           │  CLI REPL（流式输出 / 确认提示）  │   Web 面板（React 前端 + FastAPI / SSE）
           └───────────────┬────────────────┘
                           ▼
           ┌──────────── 应用层 forge_coder ─────────────┐
           │ CoderSession：组装 Agent、工具、策略、会话      │
           │ 工作模式：plan（只读规划）/ auto / manual      │
           │ WorkspaceManager：任务分支 + git worktree      │
           │ 子 Agent：Explorer（只读） / Tester（运行）     │
           │          Memory（管理项目记忆与用户偏好）      │
           │ 辅助 Agent：auto 审批、历史压缩摘要、会话标题与建议│
           │ 记忆：项目记忆按需检索注入、用户偏好全部注入    │
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

### 3.1 改动隔离与审查：git worktree（一个会话一个任务分支）

```text
repos/<项目>（本地 git 仓库，主分支 main）
   │  用户发起任务
   ▼
创建分支 forge/<任务ID> + worktree（data/worktrees/<项目>/<任务ID>）
   │  Agent 只在 worktree 内读写、运行命令
   │  每轮对话结束自动 commit → 形成修改记录（停止或失败的轮次同样提交）
   │  回溯：代码与对话回到任意一轮开始前（最近一次合并 / 丢弃之后的轮次）
   ▼
用户审查：/diff（尚未合并的改动） /log（逐轮提交记录）
   ├─ /accept  → 合并回 main，任务分支快进到合并提交，会话继续（可再次合并）
   ├─ /discard → 丢弃尚未合并的改动，回到上次合并（或会话开始）时的状态，会话继续
   └─ /archive → 总结整个会话写入项目记忆，删除 worktree 与分支，对话保存为只读存档
```

- 示例项目是本地 git 仓库，不连接远程；依赖系统已安装的 git。
- worktree 与运行数据放在 `data/`（不进入示例仓库，已加入 `.gitignore`）。
- 回溯：每轮开始前记录主 Agent 的历史位置与任务分支提交，回溯时代码与对话同时回到该点，之后各轮的记录一并移除；已合并到 main、已丢弃或已被历史压缩的轮次不能回溯。`/undo` 即回溯最近一轮。

### 3.2 测试项目模板与一键还原

- `samples/<项目>/` 是只读模板（普通文件，可随 ForgeCoder 一起纳入版本管理）；`repos/<项目>/` 是由模板生成的工作仓库（已加入 `.gitignore`）。
- 还原命令 `forge-coder reset <项目>`（REPL 内 `/reset`）：删除该项目的所有 worktree、任务分支与 `data/` 下的运行数据，删除 `repos/<项目>`，再从模板复制并 `git init`，提交一次初始提交 `baseline`。
- 无论 Agent 改了什么、用户是否已合并，还原后都回到完全相同的初始状态，便于重复测试。

### 3.3 Web 面板

前端为 `web/` 下的 React + Vite + TypeScript 应用，构建产物由 FastAPI 提供；后端复用 `CoderSession`，对话以 SSE 推送 Agent 事件。界面分三栏：

```text
┌ 顶栏：项目 / 当前会话 · 工作模式 · 待确认提示 · 上下文占用 · 会话 token ───────────────┐
├ 左栏：项目 → 会话 ─┬ 中间：对话时间线 ─────────────┬ 右侧：当前任务检查器 ──────────┤
│ 进行中 / 可恢复    │ 需求 → 思考（折叠）→ 工具调用   │ 改动：diff、逐轮提交、审查、    │
│ 已归档（只读对话）  │ 子 Agent 分组、待办、审批卡片    │       合并 / 丢弃未合并改动    │
│                    │ 执行过程折叠为一行摘要、最终回答 │ 待办 · 上下文 · 运行 · 审批    │
│ 用户偏好 · 设置    │ 输入框（/ 命令、ReAct/PlanSolve）│                                │
└────────────────────┴─────────────────────────────────┴────────────────────────────────┘
```

- **层级**：跨任务的内容放在左栏（项目、会话；用户偏好为全局入口，项目记忆从项目打开），当前任务的细节放在右侧检查器，中间只放对话过程。
- **会话**：一个会话对应一个任务分支；合并到 main 后会话继续；新建会话时当前会话自动保存，可随时恢复；归档后会话摘要写入项目记忆，完整对话在"已归档"中只读查看。第一轮后由辅助 Agent 生成会话标题；左栏搜索覆盖标题、需求、对话内容与归档摘要。
- **对话辅助**：每轮结束后辅助 Agent 给出 2~3 条下一步建议，点选即发送（设置中可关闭）；标题与建议在释放对话锁之后生成，不阻塞下一轮。时间线左侧的导航栏为每条用户消息显示一个标记，悬停查看、单击定位，高亮当前可见的轮次。
- **事件归并**：实时 SSE 事件与恢复会话时的事件共用同一套归并逻辑，生成"轮次 → 步骤 → 子 Agent 步骤"的时间线。
- **运行记录**：每轮保存主 Agent、子 Agent 与审批的事件及时间戳（精简后随会话保存），恢复会话、重启面板与查看归档会话时直接重放，因此仍能看到子 Agent 的逐步记录与各轮的运行时间轴；没有记录的早期轮次由对话历史重建。
- **运行控制**：运行中可停止本轮——取消主 Agent（框架补全被中断的工具调用记录，历史保持完整），通知子 Agent 在下一个事件处结束，等待中的审批视为拒绝；已产生的改动照常提交。失败时按原因分类显示（预算超限、模型超时、模型调用失败、程序异常、达到最大步数），重试即回溯本轮后重新发送。
- **流程细节**：每轮记录最后一次请求的上下文分布（历史压缩后的摘要单独统计）、注入的参考资料、对话历史中的压缩摘要与系统提示词，以及框架 TraceLogger 的运行轨迹报告；默认折叠，按需展开。
- **模型选择**：`.env` 中主 Agent（`LLM_MODEL_ID`）与子 Agent、辅助 Agent（`LLM_SUBAGENT_MODEL_ID`）的模型写成逗号分隔的可选范围，第一个为默认；主 Agent 的模型在输入框底部切换，其余角色在设置中选择，CLI 用 `/model`。切换下一轮生效，选择随会话保存；用量、时间线、运行轨迹报告中标注每个来源实际使用的模型。
- 主体功能的开发验证仍以 CLI 与 pytest 为准，面板用于展示与人工检查。

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
| 写文件、编辑 | 在 worktree 内自动执行，合并前统一审查 |
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
| Memory | Task 子 Agent（主 Agent 派出，或归档会话时由程序派出） | 项目记忆与用户偏好的检索 / 新增 / 修改 / 删除 | 写入会话摘要、项目约定与用户偏好，整理已有记忆（合并重复、删除过时、核对冲突），不接触代码与命令 |
| 辅助 Agent | 单次模型调用（可单独配置模型） | 无 | auto 模式下审批需确认的操作；生成历史压缩摘要、会话标题与下一步建议；Memory 子 Agent 未完成时生成后备会话摘要 |

### 3.6 记忆与上下文压缩

| 类型 | 范围 | 写入方 | 如何进入上下文 |
| --- | --- | --- | --- |
| 对话历史 | 单个会话 | 框架自动记录 | 完整注入；接近上下文窗口上限时压缩为摘要 |
| 用户偏好 | 全局，跨会话 | 用户（`/remember`、编辑、删除）；Memory 子 Agent（依据用户在对话中表达的偏好） | 每次请求全部注入 |
| 项目记忆 | 单个项目，跨会话 | Memory 子 Agent（主 Agent 派出，或归档会话时由程序派出）；用户可编辑、删除 | 每次请求按当前需求检索相关条目注入 |

- 会话之间不共享整段对话：归档会话时由 Memory 子 Agent 把目标、改动、决策与遗留问题总结为一条项目记忆，标明是否合并到 main，并整理已有记忆（合并重复、删除过时、核对冲突，无法判断的标注为待确认）；后续会话据此延续约定与结论，与代码不一致时以代码为准。
- 会话进行中，主 Agent 可在确认新的项目约定、用户表达新的偏好或发现记忆与代码不符时派出 Memory 子 Agent 同步记忆；用户也可以直接在对话中要求更新。用户偏好只记录用户本人表达的习惯与要求，不从代码或项目内容推断。
- 记忆的每次修改都记录操作、前后内容、理由与触发来源，可查看与撤销；用户的编辑优先，Memory 子 Agent 不覆盖用户刚编辑过的条目。
- 历史压缩只作用于当前会话：只保留最近几轮完整对话（阈值与保留轮数可配置），其余由辅助 Agent 生成结构化摘要，作为当前会话后续请求的上下文，不写入项目记忆；归档时会话摘要以压缩摘要与之后的对话为素材。
- 还原测试项目时清除该项目的记忆；用户偏好跨项目保留。

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
| `context` | 大仓库下的上下文构建与截断、项目说明文件注入；历史接近窗口上限时智能摘要压缩（`/compact` 手动触发） | token 占用变化、压缩前后对比 |
| `core.session_store` | 每轮保存会话、任务信息、回溯点、运行记录与子 Agent 已读文件（恢复时重新读入未修改的文件）；`--resume` 接回 worktree 继续任务 | 恢复后的对话、可回溯的轮次与运行记录 |
| `memory.profile`（ProfileStore） | 用户偏好：用户 `/remember` 与 Memory 子 Agent 写入，每次请求注入 | 跨会话生效，上下文分布中可见，修改记录可查看 |
| `memory.MemoryStore` + `MemoryContextProvider` | 项目记忆：Memory 子 Agent 写入与整理，按项目保存，每次请求按需求检索注入；`/project-memory` 查看、编辑与删除 | 后续会话延续之前的约定与结论，上下文分布中可见，修改记录可查看 |
| `observability.TraceLogger` + `core.budget` | 主 Agent 每轮生成运行轨迹报告（JSONL + HTML）；统计每轮 token，可设单轮预算 | 面板"运行"标签的时间轴、报告链接与预算状态 |
| `core.streaming` + FastAPI SSE | 向 CLI 与 Web 面板推送事件流；取消运行时框架闭合中断的工具调用，用于停止本轮 | 实时时间线、子 Agent 分组、审批卡片与停止 |
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
│   ├── workspace.py    # 测试项目还原、任务分支、worktree、提交、合并 / 丢弃未合并改动
│   ├── agents/         # Agent 相关
│   │   ├── prompts.py      # 全部提示词：主 Agent、工作模式、子 Agent、辅助 Agent
│   │   ├── modes.py        # 工作模式：plan / auto / manual
│   │   ├── subagents.py    # Explorer / Tester / Memory 子 Agent
│   │   └── advisor.py      # 程序级辅助 Agent：auto 模式审批（标题、建议等单次调用在 session.py）
│   ├── permissions/    # 权限
│   │   ├── policy.py       # 工作区策略：路径约束、命令白名单、需确认
│   │   └── approvals.py    # 审批管理：auto / manual 审批方式与审批记录
│   ├── context/        # 上下文
│   │   ├── providers.py    # 仓库地图、子 Agent 已读文件、项目说明 FORGE.md
│   │   └── window.py       # 上下文窗口用量与各部分分布
│   ├── tools/          # Glob、Grep、ListDir、RunCommand；Memory 子 Agent 专用的记忆工具
│   ├── memory/         # 用户偏好（ProfileStore）、项目记忆（MemoryStore）、统一的修改入口与修改记录（可撤销）
│   ├── metrics/        # token 用量统计、逐轮运行记录与失败分类、固定评估任务集
│   └── ui/             # 命令行 REPL、共用显示函数、Web 面板服务（FastAPI + SSE，构建产物在 web/dist）
├── web/                # Web 面板前端（React + Vite + TypeScript，npm 管理）
│   └── src/            # 布局与组件、状态（zustand）、事件归并、接口调用
├── samples/            # 测试项目模板（只读，用于生成与还原）
├── repos/              # 由模板生成的测试项目（各自为独立 git 仓库）
└── data/               # worktree、会话、审批、偏好与项目记忆、Trace、评估结果等运行数据（不纳入版本管理）
```

## 7. 待确认事项

- 暂无。

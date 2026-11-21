# ForgeCoder

基于 ForgeAgents 框架构建的代码开发 Agent，提供命令行与 Web 面板两种用法。

用自然语言提出需求，ForgeCoder 在本地仓库中完成"读懂代码 → 规划 → 修改 → 运行测试 → 修复"的闭环：

- **改动隔离**：每个会话在独立的 git 分支与 worktree 中进行，每轮自动提交；你审查 diff 后合并到 main 或丢弃，主分支不受未审查的改动影响；合并后会话可以继续。
- **多 Agent 协作**：主 Agent 负责理解需求和修改代码，可派出 Explorer（只读探索）与 Tester（运行测试）子 Agent；合并前可由 Reviewer 子 Agent 审查改动。
- **分级权限**：读写文件和白名单命令（`pytest`、`python`）自动执行，其他命令按工作模式由你确认或由辅助 Agent 判断风险后审批，所有审批都有记录。
- **上下文与记忆**：自动注入仓库地图、项目说明与用户偏好；归档会话时把目标、改动与决策总结为项目记忆，供后续会话参考；对话过长时自动压缩为摘要。
- **过程可见**：实时展示每一步思考、工具调用、子 Agent 轨迹、token 用量与上下文窗口分布；每轮的运行记录随会话保存，恢复或归档后仍可回看。
- **随时可控**：运行中可停止本轮，已产生的改动照常保留；可回溯到任意一轮之前修改需求重新发送；失败时显示原因并可一键重试。

> 设计说明（目标、架构、核心设计决策、框架模块映射）见 [ARCHITECTURE.md](ARCHITECTURE.md)。

## 环境要求

| 依赖 | 说明 |
| --- | --- |
| Python 3.12 / 3.13 + [uv](https://docs.astral.sh/uv/) | 管理 Python 环境与依赖 |
| git | 任务分支、worktree 与逐轮提交 |
| Node.js 18+ 与 npm | 仅构建 Web 面板前端时需要 |
| 兼容 OpenAI Chat Completions 接口的模型服务 | 需支持 Function Calling |

## 快速开始

### 1. 安装依赖

```bash
uv sync --all-extras
```

`--all-extras` 会同时安装 Web 面板所需的依赖；不带该参数会移除它们。

### 2. 配置模型

复制 `.env.example` 为 `.env`，至少填写以下三项：

```dotenv
LLM_API_KEY=your-api-key
LLM_BASE_URL=https://your-base-url
LLM_MODEL_ID=your-model-id
```

模型可以写成逗号分隔的列表，表示可选范围，第一个为默认，使用时可随时切换。子 Agent 与辅助 Agent 的模型，以及历史压缩阈值、单轮 token 预算等都是可选配置，见下文"配置项"。

### 3. 命令行运行

```bash
uv run forge-coder
```

首次运行会从 `samples/inventory` 模板生成测试项目 `repos/inventory`（一个本地 git 仓库，带有预置的缺陷与待实现功能）。进入 REPL 后直接输入需求即可，例如：

```text
运行测试并修复失败的用例，不要修改测试文件
```

Agent 完成后用 `/diff` 查看改动，`/accept` 合并到 main（会话继续）或 `/discard` 丢弃尚未合并的改动；工作告一段落后用 `/archive` 归档会话。演示结束后可用 `uv run forge-coder reset` 把测试项目还原到初始状态。

### 4. Web 面板

首次使用先构建前端：

```bash
cd web
npm install
npm run build
```

然后启动面板服务，在浏览器中打开终端打印的地址：

```bash
uv run forge-coder --web
```

默认端口为 8765，被占用或被系统保留时会自动改用其他端口，以终端输出为准。

## 使用说明

### 工作模式

三种模式共用同一个 Agent 与对话历史，可随时切换（启动参数 `--mode`、REPL `/mode`、面板顶部切换）：

| 模式 | 行为 |
| --- | --- |
| plan | 只读规划：阅读代码、向你提问澄清，最后输出方案；确认后切换到 auto / manual 按方案执行 |
| auto | 白名单外的命令由辅助 Agent 判断风险后自动批准或拒绝，决定与理由记入审批记录 |
| manual（默认） | 白名单外的命令由你确认（终端询问或面板确认卡片） |

### 一个会话的流程

1. 发送第一条需求时自动创建任务分支与 worktree，Agent 只能访问这个工作副本；
2. 每轮对话结束，改动自动提交到任务分支，形成逐轮修改记录；运行中可以停止本轮（面板停止按钮、命令行 Ctrl+C），已产生的改动同样提交；
3. 审查改动：查看尚未合并的 diff 与提交记录，可让 Reviewer 子 Agent 审查；不满意可回溯到任意一轮之前（代码与对话一起回退，该轮需求可修改后重新发送），已合并或已丢弃之前的轮次除外；
4. 合并到 main 后会话继续，可以接着提需求、再次合并；也可以丢弃尚未合并的改动，代码回到上次合并时的状态；
5. 归档会话：辅助 Agent 把整个会话的目标、改动、决策与遗留问题写入项目记忆，删除任务分支（未合并的改动随之丢弃），对话保存为只读存档，可随时查看。

未归档的会话会自动保存，退出后可用 `--resume` 或在面板左栏恢复。

### 命令行

```bash
uv run forge-coder [--repo NAME] [--mode plan|auto|manual]  # 进入 REPL（默认项目 inventory）
uv run forge-coder --resume [任务ID]                        # 恢复未归档的会话（默认最近一次）
uv run forge-coder sessions                                # 列出可恢复的会话
uv run forge-coder reset [NAME]                            # 还原测试项目（含任务分支、会话与项目记忆）
uv run forge-coder list                                    # 列出测试项目模板
uv run forge-coder eval [--task ID] [--save-baseline]      # 运行固定评估任务集，结果在 data/eval/
```

REPL 内常用命令（`/help` 查看全部）：

| 类别 | 命令 |
| --- | --- |
| 审查与会话 | `/diff` 改动 · `/log` 提交记录 · `/review` 审查 · `/undo` 撤销最近一轮 · `/rewind [轮次]` 回溯到某一轮之前 · `/accept` 合并到 main · `/discard` 丢弃未合并改动 · `/archive` 归档会话 |
| 模式与范式 | `/mode` 切换工作模式 · `/model [角色] [模型]` 查看或切换模型（下一轮生效） · `/plansolve <需求>` 用 Plan-and-Solve 范式完成一次需求（范式演示） |
| 记忆与上下文 | `/remember <偏好>`、`/memory`、`/forget` 管理用户偏好 · `/project-memory` 查看与删除项目记忆 · `/compact` 压缩对话历史 |
| 其他 | `/approvals` 审批记录 · `/status` 任务信息 · `/reset` 还原测试项目 · `/exit` 退出 |

### Web 面板

- **左栏**：按项目列出会话（进行中、可恢复、已归档），可新建、恢复会话或查看已归档会话的完整对话；底部为用户偏好与设置（可选择子 Agent 与辅助 Agent 的模型），项目记忆从项目行打开；
- **中间**：对话时间线，每轮的执行过程结束后折叠为一行摘要，思考与子 Agent 分组可展开，需要确认的命令以卡片显示；悬停自己的消息可回溯到该轮之前；停止或失败的轮次显示原因，失败时可重试；顶栏可归档当前会话；输入框支持 `/` 命令与 ReAct / PlanSolve 切换，底部可切换主 Agent 的模型，运行中发送按钮变为停止按钮；
- **右侧检查器**：改动（尚未合并的 diff 与提交、审查、合并到 main、丢弃未合并改动）、待办、上下文（窗口分布、每轮注入的参考资料与系统提示词）、运行（可选择任一轮查看用量与来源占比、预算、主 Agent 与子 Agent 的时间轴和运行轨迹报告）、审批记录。

## 配置项

全部写在本目录 `.env`，修改后重启生效：

| 配置 | 说明 |
| --- | --- |
| `LLM_API_KEY` / `LLM_BASE_URL` / `LLM_MODEL_ID` | 必填：服务地址、密钥与主 Agent 的可选模型（逗号分隔，第一个为默认），所有角色共用地址与密钥 |
| `LLM_SUBAGENT_MODEL_ID` | 子 Agent（Explorer / Tester / Reviewer）与辅助 Agent（auto 审批、任务摘要、历史压缩）的可选模型，写法同上；未设置时与主 Agent 相同 |
| `FORGE_COMPACT_THRESHOLD` / `FORGE_COMPACT_KEEP_ROUNDS` | 历史超过上下文窗口的该比例时自动压缩（默认 0.8），压缩时保留的最近轮数（默认 2） |
| `FORGE_ROUND_TOKEN_LIMIT` | 单轮 token 预算，超出后本轮停止；不设置表示只统计不限制 |

## 目录与数据

- `samples/`：测试项目模板（只读）；`repos/`：由模板生成的测试项目，每个都是独立的本地 git 仓库；
- `data/`：运行数据，包括任务 worktree、会话与归档会话、审批记录、用户偏好与项目记忆、运行轨迹、评估结果；
- `forge_agents/`：框架层；`forge_coder/`：应用层；`web/`：Web 面板前端（React + Vite + TypeScript）。

`forge-coder reset` 会删除对应项目的 worktree、任务分支、会话与项目记忆，并从模板重新生成，因此每次演示都从相同的状态开始；用户偏好跨项目保留。

前端开发时可以运行 `npm run dev`（在 `web/` 目录），Vite 会把 `/api` 请求代理到 `http://127.0.0.1:8900`，需先用 `uv run forge-coder --web --port 8900` 启动面板服务。

## 来源与许可证

本项目采用 [CC BY-NC-SA 4.0](https://creativecommons.org/licenses/by-nc-sa/4.0/) 许可证，完整条款见 [LICENSE](LICENSE)。

- **原始作品**：[HelloAgents](https://github.com/jjyaoao/HelloAgents)，HelloAgents Team，CC BY-NC-SA 4.0；其教程来源为 Datawhale [Hello-Agents](https://github.com/datawhalechina/hello-agents)。
- **修改说明**：`forge_agents/` 基于 HelloAgents 复刻，并按应用需要进行了修改与优化；`forge_coder/` 与 `web/` 为本项目的应用代码。
- **许可要点**：使用时需署名原作者；修改后的作品须以相同许可证发布；不得用于商业目的。

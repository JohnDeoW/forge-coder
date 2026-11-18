# ForgeCoder

基于 ForgeAgents 框架构建的命令行代码开发 Agent。

针对 `repos/` 下的本地代码仓库，ForgeCoder 可以：

- 接收自然语言需求，阅读、搜索代码，解释项目结构；
- 派出 Explorer（只读探索）与 Tester（运行测试）子 Agent，由主 Agent 统一修改代码；
- 在独立的 git worktree 中修改代码并逐轮提交，用户通过 diff 审查后接受或丢弃；
- 保存与恢复会话，并通过 Web 面板实时展示每一步思考、工具调用、结果与 token 消耗。

> 设计说明（目标、架构、核心设计决策、框架模块映射）见 [ARCHITECTURE.md](ARCHITECTURE.md)。

## 使用方式

```bash
uv sync --all-extras
uv run forge-coder                  # 进入 REPL，默认测试项目 inventory
uv run forge-coder --repo inventory # 指定测试项目
uv run forge-coder reset inventory  # 把测试项目还原为初始状态
uv run forge-coder list             # 列出测试项目模板
uv run forge-coder sessions         # 列出可恢复的任务会话
uv run forge-coder --resume         # 恢复最近一次未结束的任务（可指定任务 ID）
uv run forge-coder eval             # 运行固定评估任务集（结果保存在 data/eval/）
```

REPL 内直接输入需求即可。工作模式（启动参数 `--mode`、REPL `/mode`、面板顶部选择器切换，同一任务内共用对话历史）：

- **plan**：只读规划，Agent 阅读代码、向你提问澄清，最后输出方案；确认后切换到 auto / manual 按方案执行；
- **auto**：白名单外的命令由程序级辅助 Agent 判断风险后自动批准或拒绝，决定与理由都记录在审批记录中（`/approvals`）；
- **manual**（默认）：白名单外的命令需要你确认（终端询问，面板弹出确认卡片）。

Agent 的改动发生在独立的 git worktree 中，每轮自动提交；`pytest`、`python` 命令自动执行。`/plansolve <需求>` 可改用框架的 Plan-and-Solve 范式完成一次需求（范式演示）。

审查与任务命令：`/diff` 查看改动、`/log` 修改记录、`/review` 由 Reviewer 子 Agent 审查改动、`/undo` 撤销最近一轮（代码与对话一起回退）、`/accept` 合并到 main、`/reject` 丢弃、`/approvals` 审批记录、`/status` 任务信息、`/reset` 还原测试项目、`/exit` 退出（未结束的任务可用 `--resume` 恢复）。

偏好命令：`/remember <偏好>` 记住偏好（跨会话生效，每次请求都会带上）、`/memory` 查看、`/forget [序号|all]` 删除。

简易 Web 面板（阶段检查用）：`uv run forge-coder --web`，浏览器打开终端提示的地址（默认 http://127.0.0.1:8765，端口被系统保留时会自动改用其他端口）。

LLM 配置写在本目录 `.env`（复制 `.env.example` 后填写），支持任意兼容 OpenAI Chat Completions 接口的服务：

```dotenv
LLM_API_KEY=your-api-key
LLM_BASE_URL=https://your-base-url
LLM_MODEL_ID=your-model-id
LLM_SUBAGENT_MODEL_ID=your-subagent-model-id  # 可选：子 Agent 模型，未设置时与主 Agent 相同
LLM_UTILITY_MODEL_ID=your-utility-model-id    # 可选：辅助 Agent（auto 审批）模型，未设置时同子 Agent
```

## 来源与许可证

本项目采用 [CC BY-NC-SA 4.0](https://creativecommons.org/licenses/by-nc-sa/4.0/) 许可证，完整条款见 [LICENSE](LICENSE)。

- **原始作品**：[HelloAgents](https://github.com/jjyaoao/HelloAgents)，HelloAgents Team，CC BY-NC-SA 4.0；其教程来源为 Datawhale [Hello-Agents](https://github.com/datawhalechina/hello-agents)。
- **修改说明**：`forge_agents/` 基于 HelloAgents 复刻，并按应用需要进行了修改与优化；`forge_coder/` 为本项目的应用代码。
- **许可要点**：使用时需署名原作者；修改后的作品须以相同许可证发布；不得用于商业目的。

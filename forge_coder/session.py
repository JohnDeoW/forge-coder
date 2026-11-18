"""CoderSession：为一个测试项目组装 LLM、工具、策略与 Agent，管理任务 worktree，驱动每一轮对话。

- 发送第一条需求时自动创建任务（分支 + worktree），Agent 的所有工具只作用于该 worktree；
- 工作模式（agents/modes.py）：plan 只读规划、auto 由辅助 Agent 审批、manual 人工审批；三者共用主 Agent 与对话历史；
- Agent 范式：默认 ReAct；/plansolve 可选用框架的 PlanSolve（先规划再逐步执行），作为框架范式演示；
- 主 Agent 可派出 Explorer / Tester 子 Agent，子 Agent 的事件与主 Agent 事件合并成一条事件流；
- 需要确认的操作交给 permissions/approvals.py 的 ApprovalManager；
- 每轮对话结束把改动提交到任务分支，形成修改记录；
- 用户 accept / reject 后任务结束，下一条需求开启新任务（对话历史随之清空）。
"""

import asyncio
from contextlib import redirect_stdout
import io
import threading

from forge_agents import PlanSolveAgent, ReActAgent, ToolRegistry
from forge_agents.tools import EditTool, MultiEditTool, ReadTool, TodoWriteTool, WriteTool
from forge_agents.tools.circuit_breaker import CircuitBreaker

from . import workspace as ws
from .agents.advisor import ApprovalAdvisor
from .agents.modes import DEFAULT_MODE, PLAN, PLAN_TOOLS, parse_mode
from .agents.prompts import build_system_prompt
from .agents.subagents import SubAgentTool
from .config import MODEL_ROLES, Settings, agent_config, create_llm
from .context import SeenFilesProvider, context_usage, create_context_builder, read_project_notes, repo_map_provider
from .metrics.usage import UsageTracker
from .permissions import ApprovalManager, Approver, WorkspacePolicy
from .tools import GlobTool, GrepTool, ListDirTool, RunCommandTool

MAIN_AGENT = "forge-coder"
MAX_STEPS = 30
PLAN_STEP_TOOL_ITERATIONS = 12  # PlanSolve 每个计划步骤内的工具调用上限
# 同一工具连续 3 次出错即熔断，60 秒后恢复（框架默认 300 秒，交互场景偏长）
BREAKER_THRESHOLD, BREAKER_RECOVERY_S = 3, 60
REACT, PLANSOLVE = "react", "plansolve"  # Agent 范式
_DONE = object()


class CoderSession:
    def __init__(
        self, settings: Settings, repo_name: str, approver: Approver | str | None = None, mode: str = DEFAULT_MODE
    ):
        """approver 决定 manual 模式下需要确认的操作如何审批：
        函数（CLI 终端询问）、"web"（面板确认卡片）、None（评估等无人值守场景，一律拒绝）。
        auto 模式下由辅助 Agent 审批，不使用 approver。
        """
        self.settings = settings
        self.repo_name = repo_name
        # 主 Agent、子 Agent 与辅助 Agent 的模型可分别配置；模型相同的角色共用一个客户端
        self.llm = create_llm(settings)
        clients = {settings.model: self.llm}
        self.role_llms = {}
        for role in MODEL_ROLES:
            model = settings.model_for(role)
            self.role_llms[role] = clients.setdefault(model, clients.get(model) or create_llm(settings, model))
        self.llms = list(clients.values())
        self.usage = UsageTracker()  # 本轮与会话累计的 token 用量及来源分布
        self.approvals = ApprovalManager(approver, ApprovalAdvisor(self.role_llms["utility"]), self.usage)
        self.mode = DEFAULT_MODE
        self.set_mode(mode)
        ws.ensure_repo(settings, repo_name)
        self.task: ws.Task | None = None
        self.agent: ReActAgent | None = None
        self.plan_agent: PlanSolveAgent | None = None
        self.seen_files: SeenFilesProvider | None = None
        self.last_commit: str | None = None
        self.last_context: dict | None = None  # 本轮最后一次请求的上下文窗口用量与分布
        self._sink = None  # 本轮事件队列的写入函数，供子 Agent 线程转发事件

    # ------------------------------------------------------------ Agent
    def _build_agents(self, task: ws.Task) -> None:
        root = task.path
        registry = ToolRegistry(
            circuit_breaker=CircuitBreaker(BREAKER_THRESHOLD, BREAKER_RECOVERY_S),
            policy=WorkspacePolicy(root, self.approvals.always_allowed, mode_getter=lambda: self.mode),
            approval_handler=self.approvals,  # 策略返回"需要确认"时由框架调用
        )
        common = dict(project_root=str(root), registry=registry)
        todo_dir = self.settings.data_dir / "todos" / self.repo_name / task.id  # 不写入 worktree
        tools = [
            ReadTool(**common),
            WriteTool(**common),
            EditTool(**common),
            MultiEditTool(**common),
            ListDirTool(root),
            GlobTool(root),
            GrepTool(root),
            RunCommandTool(root),
            TodoWriteTool(project_root=str(todo_dir), persistence_dir="."),
            SubAgentTool(self._make_subagent, self.repo_name, on_event=self._forward),
        ]
        with redirect_stdout(io.StringIO()):  # 框架注册工具时会打印提示，CLI 中不需要
            for tool in tools:
                registry.register_tool(tool)
        self.registry = registry
        # plan 模式使用只读子集；共享读取缓存，规划阶段读过的文件在执行阶段仍能通过 Edit 的乐观锁检查
        self.plan_registry = registry.fork(PLAN_TOOLS)
        self.plan_registry.read_metadata_cache = registry.read_metadata_cache
        self.notes = read_project_notes(root)  # FORGE.md 写入系统提示词
        self.seen_files = SeenFilesProvider(root, registry)
        self.agent = ReActAgent(
            MAIN_AGENT, self.llm, tool_registry=registry, config=agent_config(self.settings),
            system_prompt=build_system_prompt(self.repo_name, self.notes, self.mode), max_steps=MAX_STEPS,
            # 每次模型调用前注入：仓库地图 + 子 Agent 已读文件
            context_builder=create_context_builder(),
            context_providers=[repo_map_provider(root), self.seen_files],
        )
        # 可选范式：框架的 Plan-and-Solve（规划器生成步骤 → 执行器逐步完成），作为框架范式演示
        self.plan_agent = PlanSolveAgent(
            MAIN_AGENT, self.llm, tool_registry=registry, config=agent_config(self.settings),
            executor_prompt=build_system_prompt(self.repo_name, self.notes, self.mode),
            max_tool_iterations=PLAN_STEP_TOOL_ITERATIONS,
        )

    def _make_subagent(self, role: str, system_prompt: str, max_steps: int) -> ReActAgent:
        """子 Agent 工厂：按角色选择模型，共享主注册表（由 subagent_scope 按角色过滤），附带仓库地图。"""
        return ReActAgent(
            role, self.role_llms[role], tool_registry=self.registry, config=agent_config(self.settings),
            system_prompt=system_prompt, max_steps=max_steps,
            context_builder=create_context_builder(),
            context_providers=[repo_map_provider(self.task.path)],
        )

    def _ensure_task(self) -> ws.Task:
        if self.task is None:
            self.task = ws.create_task(self.settings, self.repo_name)
            self._build_agents(self.task)
            self.approvals.start_task(self.settings.data_dir / "approvals" / self.repo_name / f"{self.task.id}.jsonl")
        return self.task

    def set_mode(self, mode: str) -> str:
        """切换工作模式；下一轮生效（工具集与系统提示词在每轮开始时按模式设置）。"""
        self.mode = self.approvals.mode = parse_mode(mode)
        return self.mode

    def _apply_mode(self) -> None:
        """按当前模式设置主 Agent 的工具集与系统提示词（历史保持不变）。"""
        self.agent.tool_registry = self.plan_registry if self.mode == PLAN else self.registry
        self.agent.system_prompt = build_system_prompt(self.repo_name, self.notes, self.mode)

    # ------------------------------------------------------------ 审批（委托 ApprovalManager）
    @property
    def approval_log(self) -> list[dict]:
        return self.approvals.records

    def resolve_approval(self, request_id: str, answer: str) -> bool:
        return self.approvals.resolve(request_id, answer)

    def _forward(self, event) -> None:
        """子 Agent 在工作线程中运行，把它的事件线程安全地放入本轮事件队列。"""
        if self.seen_files:
            self.seen_files.observe(event)  # 记录子 Agent 完整读取的文件，供主 Agent 直接使用
        if self._sink:
            self._sink(event)

    # ------------------------------------------------------------ 一轮对话
    async def stream(self, text: str, paradigm: str = REACT):
        """异步事件流（StreamEvent），合并主 Agent 与子 Agent 的事件；结束后提交本轮改动。

        paradigm：react（默认，受当前工作模式约束）或 plansolve（框架范式演示，plan 模式下不可用）。
        事件的 agent_name 区分来源：forge-coder 为主 Agent，explorer / tester 为子 Agent，utility 为辅助 Agent。
        CLI 与 Web 面板共用；提交结果放在 self.last_commit，本轮用量放在 self.usage.last。
        """
        if paradigm == PLANSOLVE and self.mode == PLAN:
            raise ValueError("plan 模式只读，PlanSolve 会修改文件；请先切换到 auto 或 manual 模式")
        task = self._ensure_task()
        self._apply_mode()
        if paradigm == PLANSOLVE:
            self.plan_agent.executor.system_prompt = self.agent.system_prompt
        agent = self.plan_agent if paradigm == PLANSOLVE else self.agent
        self.last_commit = None
        self.approvals.request = text
        self.usage.start(self.llms)
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue = asyncio.Queue()
        loop_thread = threading.get_ident()

        def sink(event):
            # 在事件循环线程内（如主 Agent 的审批）直接入队，保证与随后的工具结果顺序一致；
            # 来自子 Agent 工作线程的事件经 call_soon_threadsafe 安全入队
            if threading.get_ident() == loop_thread:
                queue.put_nowait(event)
            else:
                loop.call_soon_threadsafe(queue.put_nowait, event)

        self._sink = self.approvals.sink = sink

        async def pump():
            try:
                async for event in agent.arun_stream(text):
                    queue.put_nowait(event)
            finally:
                loop.call_soon_threadsafe(queue.put_nowait, _DONE)

        runner = asyncio.create_task(pump())
        try:
            while (event := await queue.get()) is not _DONE:
                self.usage.observe(event)
                yield event
            await runner  # 主 Agent 的异常在这里抛出
        finally:
            self._sink = self.approvals.sink = None
            if not runner.done():
                runner.cancel()
            self.usage.finish()  # 失败的轮次也计入用量
            self.last_context = context_usage(getattr(agent, "last_context_diagnostics", None))
        self.last_commit = ws.commit_round(task, text)

    # ------------------------------------------------------------ 审查
    def diff(self) -> str:
        return ws.task_diff(self.task) if self.task else ""

    def log(self) -> str:
        return ws.task_log(self.task) if self.task else ""

    def accept(self) -> str:
        commit = ws.accept_task(self.task)
        self._end_task()
        return commit

    def reject(self) -> None:
        ws.reject_task(self.task)
        self._end_task()

    def reset(self) -> None:
        """还原测试项目（同时清除所有任务 worktree）并清空对话历史。"""
        ws.reset_repo(self.settings, self.repo_name)
        self._end_task()

    def _end_task(self) -> None:
        self.task = None
        self.agent = None
        self.plan_agent = None
        self.seen_files = None
        self.last_commit = None
        self.approvals.start_task(None)

    def status(self) -> dict:
        return {
            "repo": self.repo_name,
            "model": self.llm.model,
            "mode": self.mode,
            "task": self.task.id if self.task else None,
            "branch": self.task.branch if self.task else None,
            "rounds": self.task.rounds if self.task else 0,
            "approvals": len(self.approvals.records),
            "session_tokens": self.usage.session.total_tokens,
        }

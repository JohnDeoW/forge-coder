"""CoderSession：为一个测试项目组装 LLM、工具、策略与 Agent，并管理任务 worktree。

- 发送第一条需求时自动创建任务（分支 + worktree），Agent 的所有工具只作用于该 worktree；
- 工作模式（modes.py）：plan 只读规划、auto 由辅助 Agent 审批、manual 人工审批；三者共用主 Agent 与对话历史；
- Agent 范式：默认 ReAct；/plansolve 可选用框架的 PlanSolve（先规划再逐步执行），作为框架范式演示；
- 主 Agent 可派出 Explorer / Tester 子 Agent，子 Agent 的事件与主 Agent 事件合并成一条事件流；
- 每轮对话结束把改动提交到任务分支，形成修改记录；
- 用户 accept / reject 后任务结束，下一条需求开启新任务（对话历史随之清空）。
"""

import asyncio
import concurrent.futures
from contextlib import redirect_stdout
from datetime import datetime
import io
import json
import threading
import uuid

from forge_agents import PlanSolveAgent, ReActAgent, ToolRegistry
from forge_agents.core.streaming import StreamEvent, StreamEventType
from forge_agents.tools import EditTool, MultiEditTool, PermissionDecision, ReadTool, TodoWriteTool, WriteTool
from forge_agents.tools.circuit_breaker import CircuitBreaker

from . import workspace as ws
from .advisor import ApprovalAdvisor
from .config import MODEL_ROLES, Settings, agent_config, create_llm
from .context import SeenFilesProvider, context_usage, create_context_builder, read_project_notes, repo_map_provider
from .modes import AUTO, DEFAULT_MODE, PLAN, PLAN_TOOLS, parse_mode
from .policy import Approver, WorkspacePolicy, program_of
from .prompts import build_system_prompt
from .subagents import SubAgentTool
from .tools import GlobTool, GrepTool, ListDirTool, RunCommandTool
from .usage import UsageTracker

MAX_STEPS = 30
PLAN_STEP_TOOL_ITERATIONS = 12  # PlanSolve 每个计划步骤内的工具调用上限
# 同一工具连续 3 次出错即熔断，60 秒后恢复（框架默认 300 秒，交互场景偏长）
BREAKER_THRESHOLD, BREAKER_RECOVERY_S = 3, 60
APPROVAL_TIMEOUT_S = 300  # 面板审批等待上限，超时视为拒绝
WEB_APPROVAL = "web"
REACT, PLANSOLVE = "react", "plansolve"  # Agent 范式
_DONE = object()


class CoderSession:
    def __init__(
        self, settings: Settings, repo_name: str, approver: Approver | str | None = None, mode: str = DEFAULT_MODE
    ):
        """approver 决定 manual 模式下需要确认的操作如何审批：
        - 函数（CLI）：在终端询问，返回 once / always / deny；
        - "web"（面板）：发出 APPROVAL_REQUIRED 事件，等待网页调用 resolve_approval；
        - None（评估等无人值守场景）：一律拒绝。
        auto 模式下由辅助 Agent 审批，不使用 approver。
        """
        self.settings = settings
        self.repo_name = repo_name
        self.approver = approver
        self.mode = parse_mode(mode)
        self.always_allowed: set[str] = set()
        self.pending_approvals: dict[str, concurrent.futures.Future] = {}
        self.approval_log: list[dict] = []  # 本任务的审批记录（auto 含辅助 Agent 的完整对话）
        # 主 Agent、子 Agent 与辅助 Agent 的模型可分别配置；模型相同的角色共用一个客户端
        self.llm = create_llm(settings)
        clients = {settings.model: self.llm}
        self.role_llms = {}
        for role in MODEL_ROLES:
            model = settings.model_for(role)
            self.role_llms[role] = clients.setdefault(model, clients.get(model) or create_llm(settings, model))
        self.llms = list(clients.values())
        self.advisor = ApprovalAdvisor(self.role_llms["utility"])
        ws.ensure_repo(settings, repo_name)
        self.task: ws.Task | None = None
        self.agent: ReActAgent | None = None
        self.plan_agent: PlanSolveAgent | None = None
        self.seen_files: SeenFilesProvider | None = None
        self.last_commit: str | None = None
        self.last_context: dict | None = None  # 本轮最后一次请求的上下文窗口用量与分布
        self.usage = UsageTracker()  # 本轮与会话累计的 token 用量及来源分布
        self._request = ""  # 本轮用户需求，供审批助手参考
        self._sink = None  # 本轮事件队列的写入函数，供子 Agent 线程转发事件

    # ------------------------------------------------------------ Agent
    def _build_agents(self, task: ws.Task) -> None:
        root = task.path
        registry = ToolRegistry(
            circuit_breaker=CircuitBreaker(BREAKER_THRESHOLD, BREAKER_RECOVERY_S),
            policy=WorkspacePolicy(root, self.always_allowed, mode_getter=lambda: self.mode),
            approval_handler=self._approve,  # 策略返回"需要确认"时由框架调用
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
            "forge-coder", self.llm, tool_registry=registry, config=agent_config(self.settings),
            system_prompt=build_system_prompt(self.repo_name, self.notes, self.mode), max_steps=MAX_STEPS,
            # 每次模型调用前注入：仓库地图 + 子 Agent 已读文件
            context_builder=create_context_builder(),
            context_providers=[repo_map_provider(root), self.seen_files],
        )
        # 可选范式：框架的 Plan-and-Solve（规划器生成步骤 → 执行器逐步完成），作为框架范式演示
        self.plan_agent = PlanSolveAgent(
            "forge-coder", self.llm, tool_registry=registry, config=agent_config(self.settings),
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
        return self.task

    def set_mode(self, mode: str) -> str:
        """切换工作模式；下一轮生效（工具集与系统提示词在每轮开始时按模式设置）。"""
        self.mode = parse_mode(mode)
        return self.mode

    def _apply_mode(self) -> None:
        """按当前模式设置主 Agent 的工具集与系统提示词（历史保持不变）。"""
        self.agent.tool_registry = self.plan_registry if self.mode == PLAN else self.registry
        self.agent.system_prompt = build_system_prompt(self.repo_name, self.notes, self.mode)

    # ------------------------------------------------------------ 审批
    async def _approve(self, name: str, arguments: dict, reason: str):
        """框架的审批函数（ToolRegistry.approval_handler）：可能在主事件循环或子 Agent 线程的循环中被调用。"""
        operation = str(arguments.get("command") or "") if name == "RunCommand" else json.dumps(arguments, ensure_ascii=False)
        if self.mode == AUTO:
            return await self._auto_approve(name, operation, reason)
        if callable(self.approver):
            # 终端输入会阻塞，放到线程中执行，不占用事件循环
            answer = await asyncio.to_thread(self.approver, operation)
        elif self.approver == WEB_APPROVAL:
            answer = await self._wait_web_approval(name, operation, reason)
        else:
            answer = "deny"
        if answer == "always" and program_of(operation):
            self.always_allowed.add(program_of(operation))  # 策略持有同一个集合，下次直接放行
        approved = answer in ("once", "always")
        self._log_approval({"mode": self.mode, "by": "user" if self.approver else "system",
                            "tool": name, "operation": operation, "reason": reason, "answer": answer, "approved": approved})
        return approved

    async def _auto_approve(self, name: str, operation: str, reason: str) -> PermissionDecision:
        """auto 模式：由程序级辅助 Agent 总结内容、评估风险并决定；完整对话与决定写入审批记录。"""
        review = await self.advisor.review(name, operation, reason, self._request)
        self.usage.add_external("utility", review.tokens)
        record = {"mode": AUTO, "by": "utility", "tool": name, "operation": operation, "reason": reason,
                  "approved": review.approved, "risk": review.risk, "summary": review.summary,
                  "decision_reason": review.reason, "model": self.advisor.llm.model, "exchange": review.exchange}
        self._log_approval(record)
        if self._sink:
            self._sink(StreamEvent.create(
                StreamEventType.APPROVAL_RESOLVED, "utility", tool_name=name, command=operation,
                approved=review.approved, risk=review.risk, summary=review.summary, reason=review.reason,
            ))
        if review.approved:
            return PermissionDecision(True)
        return PermissionDecision(False, f"审批助手拒绝了该操作（风险 {review.risk}）：{review.reason}")

    def _log_approval(self, record: dict) -> None:
        """审批记录：保存在会话中，并按任务追加写入 data/approvals/<项目>/<任务ID>.jsonl 便于事后查看。"""
        record = {"time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "request": self._request, **record}
        self.approval_log.append(record)
        if self.task is not None:
            path = self.settings.data_dir / "approvals" / self.repo_name / f"{self.task.id}.jsonl"
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")

    async def _wait_web_approval(self, name: str, command: str, reason: str) -> str:
        """发出审批事件并等待面板回复。用线程安全的 concurrent Future，跨线程、跨事件循环都能等待。"""
        if self._sink is None:
            return "deny"
        request_id = uuid.uuid4().hex[:8]
        future: concurrent.futures.Future = concurrent.futures.Future()
        self.pending_approvals[request_id] = future
        self._sink(StreamEvent.create(
            StreamEventType.APPROVAL_REQUIRED, "system",
            request_id=request_id, tool_name=name, command=command, reason=reason,
        ))
        try:
            return await asyncio.wait_for(asyncio.wrap_future(future), APPROVAL_TIMEOUT_S)
        except TimeoutError:
            return "deny"
        finally:
            self.pending_approvals.pop(request_id, None)

    def resolve_approval(self, request_id: str, answer: str) -> bool:
        """面板回复审批（once / always / deny）；请求不存在或已结束时返回 False。"""
        future = self.pending_approvals.get(request_id)
        if future is None or future.done():
            return False
        future.set_result(answer if answer in ("once", "always") else "deny")
        return True

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
        self._request = text
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

        self._sink = sink

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
            self._sink = None
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
        self.approval_log = []

    def status(self) -> dict:
        return {
            "repo": self.repo_name,
            "model": self.llm.model,
            "mode": self.mode,
            "task": self.task.id if self.task else None,
            "branch": self.task.branch if self.task else None,
            "rounds": self.task.rounds if self.task else 0,
            "approvals": len(self.approval_log),
            "session_tokens": self.usage.session.total_tokens,
        }

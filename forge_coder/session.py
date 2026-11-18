"""CoderSession：为一个测试项目组装 LLM、工具、策略与 Agent，并管理任务 worktree。

- 发送第一条需求时自动创建任务（分支 + worktree），Agent 的所有工具只作用于该 worktree；
- 主 Agent 可派出 Explorer / Tester 子 Agent，子 Agent 的事件与主 Agent 事件合并成一条事件流；
- 支持两种模式：默认 ReAct（边想边做），/plan 使用 PlanSolve（先规划再逐步执行）；
- 每轮对话结束把改动提交到任务分支，形成修改记录；
- 用户 accept / reject 后任务结束，下一条需求开启新任务（对话历史随之清空）。
"""

import asyncio
from contextlib import redirect_stdout
import io

from forge_agents import PlanSolveAgent, ReActAgent, ToolRegistry
from forge_agents.tools import EditTool, MultiEditTool, ReadTool, TodoWriteTool, WriteTool
from forge_agents.tools.circuit_breaker import CircuitBreaker

from . import workspace as ws
from .config import Settings, agent_config, create_llm
from .policy import Approver, WorkspacePolicy
from .prompts import build_executor_prompt, build_system_prompt
from .subagents import SubAgentTool
from .tools import GlobTool, GrepTool, ListDirTool, RunCommandTool
from .usage import UsageTracker

MAX_STEPS = 30
PLAN_STEP_TOOL_ITERATIONS = 12  # PlanSolve 每个计划步骤内的工具调用上限
# 同一工具连续 3 次出错即熔断，60 秒后恢复（框架默认 300 秒，交互场景偏长）
BREAKER_THRESHOLD, BREAKER_RECOVERY_S = 3, 60
_DONE = object()


class CoderSession:
    def __init__(self, settings: Settings, repo_name: str, approver: Approver | None = None):
        """approver：白名单外命令的审批函数；为 None 时这类命令一律拒绝（如 Web 面板）。"""
        self.settings = settings
        self.repo_name = repo_name
        self.approver = approver
        self.always_allowed: set[str] = set()
        self.llm = create_llm(settings)
        ws.ensure_repo(settings, repo_name)
        self.task: ws.Task | None = None
        self.agent: ReActAgent | None = None
        self.plan_agent: PlanSolveAgent | None = None
        self.last_commit: str | None = None
        self.usage = UsageTracker()  # 本轮与会话累计的 token 用量及来源分布
        self._sink = None  # 本轮事件队列的写入函数，供子 Agent 线程转发事件

    # ------------------------------------------------------------ Agent
    def _build_agents(self, task: ws.Task) -> None:
        root = task.path
        registry = ToolRegistry(
            circuit_breaker=CircuitBreaker(BREAKER_THRESHOLD, BREAKER_RECOVERY_S),
            policy=WorkspacePolicy(root, self.approver, self.always_allowed),
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
            SubAgentTool(self.llm, registry, self.repo_name, on_event=self._forward),
        ]
        with redirect_stdout(io.StringIO()):  # 框架注册工具时会打印提示，CLI 中不需要
            for tool in tools:
                registry.register_tool(tool)
        self.agent = ReActAgent(
            "forge-coder", self.llm, tool_registry=registry, config=agent_config(),
            system_prompt=build_system_prompt(self.repo_name), max_steps=MAX_STEPS,
        )
        self.plan_agent = PlanSolveAgent(
            "forge-coder", self.llm, tool_registry=registry, config=agent_config(),
            executor_prompt=build_executor_prompt(self.repo_name),
            max_tool_iterations=PLAN_STEP_TOOL_ITERATIONS,
        )

    def _ensure_task(self) -> ws.Task:
        if self.task is None:
            self.task = ws.create_task(self.settings, self.repo_name)
            self._build_agents(self.task)
        return self.task

    def _forward(self, event) -> None:
        """子 Agent 在工作线程中运行，把它的事件线程安全地放入本轮事件队列。"""
        if self._sink:
            self._sink(event)

    async def stream(self, text: str, mode: str = "react"):
        """异步事件流（StreamEvent），合并主 Agent 与子 Agent 的事件；结束后提交本轮改动。

        事件的 agent_name 区分来源：forge-coder 为主 Agent，explorer / tester 为子 Agent。
        CLI 与 Web 面板共用；提交结果放在 self.last_commit，本轮用量放在 self.usage.last。
        """
        task = self._ensure_task()
        agent = self.plan_agent if mode == "plan" else self.agent
        self.last_commit = None
        self.usage.start(self.llm)
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue = asyncio.Queue()
        self._sink = lambda event: loop.call_soon_threadsafe(queue.put_nowait, event)

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
        self.last_commit = None

    def status(self) -> dict:
        return {
            "repo": self.repo_name,
            "model": self.llm.model,
            "task": self.task.id if self.task else None,
            "branch": self.task.branch if self.task else None,
            "rounds": self.task.rounds if self.task else 0,
            "session_tokens": self.usage.session.total_tokens,
        }

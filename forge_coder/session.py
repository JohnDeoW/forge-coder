"""CoderSession：为一个测试项目组装 LLM、工具、策略与 Agent，管理任务 worktree，驱动每一轮对话。

- 发送第一条需求时自动创建任务（分支 + worktree），Agent 的所有工具只作用于该 worktree；
- 工作模式（agents/modes.py）：plan 只读规划、auto 由辅助 Agent 审批、manual 人工审批；三者共用主 Agent 与对话历史；
- Agent 范式：默认 ReAct；/plansolve 可选用框架的 PlanSolve（先规划再逐步执行），作为框架范式演示；
- 主 Agent 可派出 Explorer / Tester 子 Agent，子 Agent 的事件与主 Agent 事件合并成一条事件流；
- 需要确认的操作交给 permissions/approvals.py 的 ApprovalManager；
- 每轮对话结束把改动提交到任务分支形成修改记录，并保存会话（可 --resume 恢复）；/undo 撤销最近一轮；
- /review 由 Reviewer 子 Agent 只读审查任务 diff；/remember 记录跨会话的用户偏好；
- 项目记忆（memory/project.py）：会话归档时辅助 Agent 生成会话摘要写入，历史压缩摘要也写入，每次请求按需求检索注入；
- 历史压缩：历史接近上下文窗口上限时框架用辅助 Agent 模型生成智能摘要，/compact 手动压缩；
- 一个会话对应一个任务分支：合并到 main 后会话继续，可继续修改并再次合并；也可丢弃尚未合并的改动；
- 用户归档会话后任务结束：辅助 Agent 总结整个会话写入项目记忆，对话保存为只读存档，下一条需求开启新会话。
"""

import asyncio
from contextlib import nullcontext, redirect_stdout
from datetime import datetime
import io
import json
from pathlib import Path
import threading

from forge_agents import AgentComponents, PlanSolveAgent, ReActAgent, ToolRegistry
from forge_agents.context.text import count_tokens
from forge_agents.core.session_store import SessionStore
from forge_agents.core.subagent_scope import subagent_scope
from forge_agents.tools import EditTool, MultiEditTool, ReadTool, TodoWriteTool, WriteTool
from forge_agents.tools.circuit_breaker import CircuitBreaker
from forge_agents.tools.tool_filter import ReadOnlyFilter

from . import workspace as ws
from .agents.advisor import ApprovalAdvisor
from .agents.modes import DEFAULT_MODE, PLAN, PLAN_TOOLS, parse_mode
from .agents.prompts import (
    REVIEW_REQUEST, REVIEWER_PROMPT, TASK_SUMMARY_PROMPT, TASK_SUMMARY_REQUEST, build_subagent_prompt, build_system_prompt,
)
from .agents.subagents import ROLES, SubAgentTool
from .config import MODEL_ROLES, Settings, agent_config, create_llm
from .context import SeenFilesProvider, context_usage, create_context_builder, read_project_notes, repo_map_provider
from .context.window import LABELS as CONTEXT_LABELS
from .memory import PreferenceMemory, PreferencesProvider, ProjectMemory, ProjectMemoryProvider
from .memory.project import COMPACT, DISCARDED, MERGED
from .metrics.usage import Usage, UsageTracker
from .permissions import ApprovalManager, Approver, WorkspacePolicy
from .tools import GlobTool, GrepTool, ListDirTool, RunCommandTool

MAIN_AGENT = "forge-coder"
MAX_STEPS = 30
REVIEW_MAX_STEPS = 15
REVIEW_DIFF_CHARS = 30000  # 交给 Reviewer 的 diff 上限，超出部分让它自行 Read
SUMMARY_DIFF_CHARS, SUMMARY_TALK_CHARS = 12000, 8000  # 生成会话摘要时 diff 与对话要点的长度上限
PLAN_STEP_TOOL_ITERATIONS = 12  # PlanSolve 每个计划步骤内的工具调用上限
# 同一工具连续 3 次出错即熔断，60 秒后恢复（框架默认 300 秒，交互场景偏长）
BREAKER_THRESHOLD, BREAKER_RECOVERY_S = 3, 60
REACT, PLANSOLVE = "react", "plansolve"  # Agent 范式
SESSION_META_KEY = "forge_coder"  # 存入框架会话元数据的任务信息
# 丢弃未合并改动后，下一轮附在需求前提示主 Agent（只进入模型输入与历史，不改变用户需求记录）
DISCARD_NOTE = "【系统提示】用户已丢弃上次合并以来尚未合并的改动，工作区代码已回到最近一次合并（或会话开始）时的状态；之前读过的文件内容可能已失效，修改前请重新阅读。"
_DONE = object()


def sessions_dir(settings: Settings, repo_name: str) -> Path:
    return settings.data_dir / "sessions" / repo_name


def archived_dir(settings: Settings, repo_name: str) -> Path:
    return sessions_dir(settings, repo_name) / "archived"


def list_archived(settings: Settings, repo_name: str) -> list[dict]:
    """已归档的会话（只读），按归档时间从新到旧。"""
    items = []
    directory = archived_dir(settings, repo_name)
    for file in directory.glob("*.json") if directory.is_dir() else []:
        meta = json.loads(file.read_text(encoding="utf-8"))["metadata"][SESSION_META_KEY]
        items.append({"file": str(file), **meta})
    return sorted(items, key=lambda x: x["archived"]["time"], reverse=True)


def load_archived(settings: Settings, repo_name: str, task_id: str) -> tuple[list, dict]:
    """读取归档会话的对话历史（框架 Message）与任务信息。"""
    from forge_agents.core.message import Message

    data = json.loads((archived_dir(settings, repo_name) / f"{task_id}.json").read_text(encoding="utf-8"))
    return [Message.from_dict(m) for m in data.get("history", [])], data["metadata"][SESSION_META_KEY]


def list_sessions(settings: Settings, repo_name: str) -> list[dict]:
    """可恢复的会话（任务 worktree 仍存在），按保存时间从新到旧。"""
    items = []
    directory = sessions_dir(settings, repo_name)
    for file in directory.glob("*.json") if directory.is_dir() else []:
        data = json.loads(file.read_text(encoding="utf-8"))
        meta = (data.get("metadata") or {}).get(SESSION_META_KEY)
        if meta and (settings.worktrees_dir / repo_name / meta["task_id"]).is_dir():
            items.append({"file": str(file), "saved_at": data.get("saved_at"), **meta})
    return sorted(items, key=lambda x: x["saved_at"] or "", reverse=True)


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
        self.usage = UsageTracker(settings.round_token_limit)  # 本轮与会话累计的 token 用量及来源分布；可设单轮预算
        self.approvals = ApprovalManager(approver, ApprovalAdvisor(self.role_llms["utility"]), self.usage)
        self.preferences = PreferenceMemory(settings.data_dir)  # 跨会话的用户偏好
        self.project_memory = ProjectMemory(settings.data_dir, repo_name)  # 跨任务的项目知识
        self.mode = DEFAULT_MODE
        self.set_mode(mode)
        ws.ensure_repo(settings, repo_name)
        self.task: ws.Task | None = None
        self.agent: ReActAgent | None = None
        self.plan_agent: PlanSolveAgent | None = None
        self.seen_files: SeenFilesProvider | None = None
        self.requests: list[str] = []  # 本任务的用户需求（供 /review 与会话恢复）
        self.round_marks: list[dict] = []  # 每个已提交轮次：提交号与该轮开始前的主 Agent 历史长度（供 /undo）
        self.last_commit: str | None = None
        self.last_context: dict | None = None  # 本轮最后一次请求的上下文窗口用量与分布
        self.last_compaction: dict | None = None  # 本轮（或 /compact）的历史压缩结果
        self.last_task_summary: str | None = None  # 最近结束的任务写入项目记忆的摘要
        self._turn_start: int | None = None  # 进行中的轮次开始前的历史长度（压缩时随之调整）
        self.markers: list[dict] = []  # 会话中的合并 / 丢弃记录：类型、提交、时间与发生时的历史位置
        self._pending_note: str | None = None  # 下一轮附在需求前给主 Agent 的提示
        self.round_details: list[dict] = []  # 每轮的上下文分布、注入的参考资料、系统提示词与运行轨迹报告（供面板查看）
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
            MAIN_AGENT, self.llm, tool_registry=registry,
            # 主 Agent 启用智能摘要压缩与框架 TraceLogger（每轮一份运行轨迹报告）
            config=agent_config(self.settings, main=True, trace_dir=self.settings.data_dir / "traces" / self.repo_name / task.id),
            system_prompt=build_system_prompt(self.repo_name, self.notes, self.mode), max_steps=MAX_STEPS,
            # 每次模型调用前注入：仓库地图 + 子 Agent 已读文件 + 用户偏好 + 相关项目记忆
            context_builder=create_context_builder(),
            context_providers=[
                repo_map_provider(root), self.seen_files, PreferencesProvider(self.preferences),
                ProjectMemoryProvider(self.project_memory, lambda: self.task.id if self.task else None),
            ],
            components=AgentComponents(
                # 框架会话存储：每轮结束保存历史、读取缓存与任务信息，供 --resume 恢复
                session_store=SessionStore(str(sessions_dir(self.settings, self.repo_name))),
                summary_llm=self.role_llms["utility"],  # 历史压缩的智能摘要使用辅助 Agent 模型
            ),
        )
        self.agent.on_compress = self._on_compress
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

    def _approval_log_file(self, task: ws.Task) -> Path:
        return self.settings.data_dir / "approvals" / self.repo_name / f"{task.id}.jsonl"

    def _ensure_task(self) -> ws.Task:
        if self.task is None:
            self.task = ws.create_task(self.settings, self.repo_name)
            self.usage.session = Usage()  # 会话累计用量从新会话开始计算
            self._build_agents(self.task)
            self.approvals.start_task(self._approval_log_file(self.task))
            self.requests, self.round_marks = [], []
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

    # ------------------------------------------------------------ 驱动一轮 Agent
    async def _drive(self, agent, text: str, scope=None):
        """运行 agent.arun_stream(text)，合并子 Agent 事件、统计用量与上下文窗口；对话轮与 /review 共用。"""
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
                with scope or nullcontext():
                    async for event in agent.arun_stream(text):
                        queue.put_nowait(event)
            finally:
                loop.call_soon_threadsafe(queue.put_nowait, _DONE)

        runner = asyncio.create_task(pump())
        try:
            while (event := await queue.get()) is not _DONE:
                self.usage.observe(event)
                yield event
            await runner  # Agent 的异常在这里抛出
        finally:
            self._sink = self.approvals.sink = None
            if not runner.done():
                runner.cancel()
            self.usage.finish()  # 失败的轮次也计入用量
            self.last_context = context_usage(getattr(agent, "last_context_diagnostics", None))

    async def stream(self, text: str, paradigm: str = REACT):
        """一轮对话：异步事件流（StreamEvent），合并主 Agent 与子 Agent 的事件；结束后提交改动并保存会话。

        paradigm：react（默认，受当前工作模式约束）或 plansolve（框架范式演示，plan 模式下不可用）。
        事件的 agent_name 区分来源：forge-coder 为主 Agent，explorer / tester / reviewer 为子 Agent，utility 为辅助 Agent。
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
        self.requests.append(text)
        self.last_compaction = None
        self._turn_start = len(self.agent.history_manager.get_history())
        model_text = f"{self._pending_note}\n\n{text}" if self._pending_note else text
        self._pending_note = None
        async for event in self._drive(agent, model_text):
            yield event
        self.last_commit = ws.commit_round(task, text)
        if self.last_commit:  # 本轮结束时若发生了自动压缩，_turn_start 已在 _on_compress 中调整
            self.round_marks.append({"commit": self.last_commit, "history_len": self._turn_start})
        self._turn_start = None
        self._record_round(agent, text, paradigm)
        self._save_session()

    def _record_round(self, agent, text: str, paradigm: str) -> None:
        """记录本轮的流程细节：最后一次请求的上下文分布与注入的参考资料、系统提示词、运行轨迹报告。"""
        # 框架在每次运行结束时 finalize 运行轨迹（写入统计并关闭），下一轮新建一份，因此一轮对应一份报告
        logger = getattr(agent, "trace_logger", None)
        trace = str(logger.html_path) if logger is not None else None
        packets = [
            {"source": CONTEXT_LABELS.get("packet:" + str(p.metadata.get("source")), p.metadata.get("source")),
             "tokens": count_tokens(p.content), "content": p.content}
            for p in getattr(agent, "last_context_packets", None) or []
        ]
        prompt = agent.executor.system_prompt if paradigm == PLANSOLVE else agent.system_prompt
        self.round_details.append({
            "round": len(self.requests), "request": text, "mode": self.mode, "paradigm": paradigm,
            "commit": self.last_commit, "context": self.last_context,
            "usage": self.usage.last.to_dict() if self.usage.last else None,
            "packets": packets, "system_prompt": prompt, "trace": trace,
        })

    def system_prompts(self) -> dict[str, str]:
        """主 Agent（当前模式）与各子 Agent 的系统提示词。"""
        prompts = {"主 Agent": build_system_prompt(self.repo_name, getattr(self, "notes", ""), self.mode)}
        for role, spec in ROLES.items():
            prompts[role] = build_subagent_prompt(spec["prompt"], self.repo_name)
        prompts["reviewer"] = build_subagent_prompt(REVIEWER_PROMPT, self.repo_name)
        return prompts

    # ------------------------------------------------------------ 历史压缩
    def compact(self, keep_rounds: int = 0) -> dict:
        """/compact：用辅助 Agent 模型把对话历史压缩为摘要（默认全部压缩，可保留最近几轮），摘要写入项目记忆。"""
        if self.agent is None:
            raise ValueError("当前没有进行中的任务")
        self.last_compaction = None
        result = self.agent.compress_history(keep_rounds)
        if result is None:
            raise ValueError("对话轮数不足，没有可压缩的历史")
        self._save_session()
        return result

    def _on_compress(self, result: dict) -> None:
        """框架压缩历史后的回调（自动压缩与 /compact 共用）：摘要写入项目记忆，并调整 /undo 的历史位置。

        历史变为 [摘要] + 原历史[k:]（k 为被压缩的消息数）：之后的轮次位置减 k - 1；
        开始于被压缩部分的轮次无法再同步回退对话，不再支持撤销。
        """
        self.last_compaction = result
        self.project_memory.add(self.task.id, COMPACT, result["summary"])
        k = result["compressed_messages"]
        self.round_marks = [{**m, "history_len": m["history_len"] - k + 1}
                            for m in self.round_marks if m["history_len"] >= k]
        self.markers = [{**m, "history_len": max(m["history_len"] - k + 1, 0)} for m in self.markers]
        if self._turn_start is not None:
            self._turn_start = max(self._turn_start - k + 1, 1)

    # ------------------------------------------------------------ /review /undo
    async def review(self):
        """Reviewer 子 Agent 只读审查本任务相对 main 的全部改动；不提交、不改变对话历史。"""
        diff = self.diff()
        if not diff.strip():
            raise ValueError("当前任务没有改动可审查")
        self.last_commit = None  # 审查不产生提交，避免显示上一轮的提交号
        if len(diff) > REVIEW_DIFF_CHARS:
            diff = diff[:REVIEW_DIFF_CHARS] + "\n…（diff 过长已截断，其余部分请用 Read 查看相关文件）"
        requests = "\n".join(f"{i}. {r}" for i, r in enumerate(self.requests, 1)) or "（无记录）"
        reviewer = self._make_subagent("reviewer", build_subagent_prompt(REVIEWER_PROMPT, self.repo_name), REVIEW_MAX_STEPS)
        scope = subagent_scope(reviewer, ReadOnlyFilter(additional_allowed=["ListDir"]))  # 框架子代理机制：只读工具集
        async for event in self._drive(reviewer, REVIEW_REQUEST.format(requests=requests, diff=diff), scope):
            yield event

    def undo(self) -> str:
        """撤销最近一次提交的轮次：任务分支回退一个提交，主 Agent 历史截断到该轮开始前（之后的对话一并撤销）。"""
        if self.task is None or not self.round_marks:
            raise ValueError("当前任务没有可撤销的改动")
        mark = self.round_marks.pop()
        head = ws.undo_round(self.task)
        history = self.agent.history_manager.get_history()[: mark["history_len"]]
        self.agent.history_manager.clear()
        for message in history:
            self.agent.history_manager.append(message)
        self.agent._history_token_count = self.agent.token_counter.count_messages(history)
        self.registry.read_metadata_cache.clear()  # 文件已回退，旧的读取元数据失效
        self.requests = self.requests[: len(self.requests) - 1] if self.requests else []
        self.round_details = self.round_details[:-1]
        self._save_session()
        return head

    # ------------------------------------------------------------ 会话保存与恢复（框架 SessionStore）
    def _save_session(self) -> None:
        """把任务信息写入框架会话元数据后保存；文件名为任务 ID，每轮覆盖。"""
        if self.task is None or self.agent is None:
            return
        self.agent._session_metadata[SESSION_META_KEY] = {
            "repo": self.repo_name, "task_id": self.task.id, "base": self.task.base, "rounds": self.task.rounds,
            "mode": self.mode, "requests": self.requests, "round_marks": self.round_marks,
            "origin": self.task.origin, "markers": self.markers, "usage": self.usage.session.to_dict(),
        }
        self.agent.save_session(self.task.id)

    def resume(self, task_id: str | None = None) -> dict:
        """恢复会话：接回任务 worktree，重建 Agent 并由框架 load_session 恢复历史与读取缓存。"""
        sessions = list_sessions(self.settings, self.repo_name)
        if task_id:
            sessions = [s for s in sessions if s["task_id"] == task_id]
        if not sessions:
            raise ValueError("没有可恢复的会话" + (f"：{task_id}" if task_id else ""))
        meta = sessions[0]
        self.task = ws.load_task(self.settings, self.repo_name, meta["task_id"], meta["base"], meta["rounds"],
                                 meta.get("origin", ""))
        self.set_mode(meta.get("mode", self.mode))
        self._build_agents(self.task)
        with redirect_stdout(io.StringIO()):  # 框架加载时打印的提示不需要
            self.agent.load_session(meta["file"])
        # load_session 替换了读取缓存字典，plan 子集需要重新共享
        self.plan_registry.read_metadata_cache = self.registry.read_metadata_cache
        self.requests, self.round_marks = list(meta.get("requests", [])), list(meta.get("round_marks", []))
        self.round_details = []  # 流程细节只保存在内存中，恢复前的轮次不再提供
        self.markers = list(meta.get("markers", []))
        self.usage.session = Usage.from_dict(meta.get("usage"))
        self.approvals.start_task(self._approval_log_file(self.task))
        self.approvals.load_records()
        return meta

    # ------------------------------------------------------------ 审查与任务结束
    def diff(self) -> str:
        return ws.task_diff(self.task) if self.task else ""

    def log(self) -> str:
        return ws.task_log(self.task) if self.task else ""

    def _require_task(self) -> ws.Task:
        if self.task is None:
            raise ValueError("当前没有进行中的任务")
        return self.task

    def _mark(self, kind: str, commit: str) -> None:
        self.markers.append({"kind": kind, "commit": commit, "time": datetime.now().strftime("%m-%d %H:%M"),
                             "history_len": len(self.agent.history_manager.get_history())})

    def accept(self) -> str:
        """合并到 main，会话继续：之后的改动从合并点重新计算，可再次合并。返回合并后的提交。"""
        try:
            commit = ws.merge_task(self._require_task())
        except ws.WorkspaceError as exc:
            raise ValueError(str(exc)) from exc
        self._mark("merge", commit)
        self.round_marks = []  # 已合并的轮次不能再撤销
        self._save_session()
        return commit

    def discard(self) -> None:
        """丢弃尚未合并的改动，会话继续；下一轮提示主 Agent 代码已回退。"""
        task = self._require_task()
        if not self.diff().strip():
            raise ValueError("没有尚未合并的改动")
        ws.discard_changes(task)
        self._mark("discard", task.base[:7])
        self.round_marks = []
        self.registry.read_metadata_cache.clear()  # 文件已回退，旧的读取元数据失效
        self._pending_note = DISCARD_NOTE
        self._save_session()

    def archive(self, summarize: bool = True) -> dict:
        """归档会话：辅助 Agent 总结整个会话写入项目记忆，对话保存为只读存档，删除 worktree 与任务分支
        （尚未合并的改动随之丢弃）。summarize=False 时不生成摘要（评估等场景）。"""
        task = self._require_task()
        merges = sum(1 for m in self.markers if m["kind"] == "merge")
        unmerged = bool(self.diff().strip())
        outcome = MERGED if merges else DISCARDED
        self._record_task(outcome, self._summary_input(merges, unmerged) if summarize else None)
        self._save_session()
        file = sessions_dir(self.settings, self.repo_name) / f"{task.id}.json"
        if file.is_file():  # 对话保存为只读存档，左栏"已结束"中可查看
            data = json.loads(file.read_text(encoding="utf-8"))
            data["metadata"][SESSION_META_KEY]["archived"] = {
                "time": datetime.now().strftime("%Y-%m-%d %H:%M"), "outcome": outcome, "merges": merges,
                "unmerged_discarded": unmerged, "summary": self.last_task_summary,
            }
            target = archived_dir(self.settings, self.repo_name) / file.name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        ws.reject_task(task)
        self._end_task()
        return {"outcome": outcome, "merges": merges, "summary": self.last_task_summary}

    # ------------------------------------------------------------ 会话摘要 → 项目记忆
    def _summary_input(self, merges: int, unmerged: bool) -> str | None:
        """在 worktree 删除前收集会话摘要的素材：需求、对话要点（压缩摘要 + 用户与 Agent 的文本）、整个会话的 diff。"""
        if self.task is None or not self.requests:
            return None
        talk = []
        for message in self.agent.history_manager.get_history():
            if message.role == "summary":
                talk.append(message.content)
            elif message.role in ("user", "assistant") and message.content.strip():
                talk.append(f"[{message.role}] {message.content[:800]}")
        conversation = "\n\n".join(talk)[-SUMMARY_TALK_CHARS:]
        diff = ws.session_diff(self.task)
        if len(diff) > SUMMARY_DIFF_CHARS:
            diff = diff[:SUMMARY_DIFF_CHARS] + "\n…（已截断）"
        if merges:
            outcome = f"已合并到 main {merges} 次" + ("；归档时还有未合并的改动，已丢弃" if unmerged else "")
        else:
            outcome = "从未合并到 main，归档时改动已丢弃"
        return TASK_SUMMARY_REQUEST.format(
            outcome=outcome, requests="\n".join(f"{i}. {r}" for i, r in enumerate(self.requests, 1)),
            conversation=conversation or "（无）", diff=diff or "（无改动）",
        )

    def _record_task(self, outcome: str, summary_input: str | None) -> None:
        """辅助 Agent 生成会话摘要写入项目记忆；成功后撤回本会话的压缩摘要（已被会话摘要覆盖）。"""
        self.last_task_summary = None
        if summary_input is None:
            return
        messages = [{"role": "system", "content": TASK_SUMMARY_PROMPT}, {"role": "user", "content": summary_input}]
        try:
            summary = self.role_llms["utility"].invoke(messages, temperature=0.2).content.strip()
        except Exception as exc:  # 模型不可用时只记录需求，任务照常结束
            summary = f"（摘要生成失败：{type(exc).__name__}）\n目标：" + "；".join(self.requests)
        else:
            self.project_memory.retract_task(self.task.id, COMPACT)
        self.project_memory.add(self.task.id, outcome, summary)
        self.last_task_summary = summary

    def reset(self) -> None:
        """还原测试项目（同时清除所有任务 worktree 与会话）并清空对话历史。"""
        ws.reset_repo(self.settings, self.repo_name)
        self._end_task()

    def detach(self) -> None:
        """离开当前会话但不结束任务（面板"新会话"或切换会话）：会话已保存，之后可恢复。"""
        self._save_session()
        self._clear()

    def _end_task(self) -> None:
        if self.task is not None:  # 任务已结束，会话不再可恢复
            (sessions_dir(self.settings, self.repo_name) / f"{self.task.id}.json").unlink(missing_ok=True)
        self._clear()

    def _clear(self) -> None:
        self.task = None
        self.agent = None
        self.plan_agent = None
        self.seen_files = None
        self.last_commit = None
        self.requests, self.round_marks, self.round_details, self.markers = [], [], [], []
        self._pending_note = None
        self.usage.session = Usage()
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
            "preferences": len(self.preferences.items()),
            "project_memory": len(self.project_memory.entries()),
            "session_tokens": self.usage.session.total_tokens,
        }

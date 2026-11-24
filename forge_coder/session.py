"""CoderSession：为一个测试项目组装 LLM、工具、策略与 Agent，管理任务 worktree，驱动每一轮对话。

- 发送第一条需求时自动创建任务（分支 + worktree），Agent 的所有工具只作用于该 worktree；
- 工作模式（agents/modes.py）：plan 只读规划、auto 由辅助 Agent 审批、manual 人工审批；三者共用主 Agent 与对话历史；
- Agent 范式：默认 ReAct；/plansolve 可选用框架的 PlanSolve（先规划再逐步执行），作为框架范式演示；
- 主 Agent 可派出 Explorer / Tester 子 Agent，子 Agent 的事件与主 Agent 事件合并成一条事件流；
- 需要确认的操作交给 permissions/approvals.py 的 ApprovalManager；
- 每轮对话结束把改动提交到任务分支形成修改记录，并保存会话（可 --resume 恢复）；
- 回溯（/rewind、面板）：代码与对话回到任意一轮开始前，之后各轮一并移除；/undo 即回溯最近一轮；
- 停止本轮（面板"停止"、CLI Ctrl+C）：取消主 Agent、通知子 Agent 结束、等待中的审批视为拒绝，已产生的改动照常提交；
- 每轮保存运行记录（metrics/runlog.py）：主 Agent 与子 Agent 的事件及时间戳、审批等待、失败原因，恢复与归档后可重放；
- /review 由 Reviewer 子 Agent 只读审查任务 diff；/remember 记录跨会话的用户偏好；
- 记忆（memory/）：memory 子 Agent 维护项目记忆与用户偏好——主 Agent 按需派出，归档会话时由程序派出写入会话摘要并整理记忆；
  所有修改经 memory/editor.py 记录，可查看与撤销；项目记忆每次请求按需求检索注入，用户偏好全部注入；
- 历史压缩：历史接近上下文窗口上限时框架用辅助 Agent 模型生成智能摘要，/compact 手动压缩；摘要只留在当前会话的历史中；
- 模型选择：各角色在 .env 配置的范围内手动切换（set_model），下一轮生效，选择随会话保存；
- 一个会话对应一个任务分支：合并到 main 后会话继续，可继续修改并再次合并；也可丢弃尚未合并的改动；
- 用户归档会话后任务结束：memory 子 Agent 总结整个会话并整理记忆，对话保存为只读存档，下一条需求开启新会话。
"""

import asyncio
from contextlib import nullcontext, redirect_stdout
from datetime import datetime
import io
import json
from pathlib import Path
import threading
import time

from forge_agents import AgentComponents, PlanSolveAgent, ReActAgent, ToolRegistry
from forge_agents.context.text import count_tokens
from forge_agents.core.session_store import SessionStore
from forge_agents.core.runtime import sync_events
from forge_agents.core.subagent_scope import subagent_scope
from forge_agents.tools import EditTool, MultiEditTool, ReadTool, TodoWriteTool, WriteTool
from forge_agents.tools.circuit_breaker import CircuitBreaker
from forge_agents.tools.tool_filter import ReadOnlyFilter

from . import workspace as ws
from .agents.advisor import ApprovalAdvisor
from .agents.modes import DEFAULT_MODE, PLAN, PLAN_TOOLS, parse_mode
from .agents.prompts import (
    MEMORY_ARCHIVE_REQUEST, NEXT_STEPS_PROMPT, NEXT_STEPS_REQUEST, REVIEW_REQUEST, REVIEWER_PROMPT, SESSION_TITLE_PROMPT,
    TASK_SUMMARY_PROMPT, TASK_SUMMARY_REQUEST, build_subagent_prompt, build_system_prompt,
)
from .agents.subagents import ROLES, SubAgentTool
from .config import ALL_MODEL_ROLES, MAIN_ROLE, MODEL_ROLES, Settings, agent_config, create_llm, load_options
from .context import (ReferencedFilesProvider, SeenFilesProvider, context_usage, create_context_builder,
                      find_references, read_project_notes, repo_map_provider, strip_attachments)
from .context.window import LABELS as CONTEXT_LABELS
from .memory import PreferenceMemory, PreferencesProvider, ProjectMemory, ProjectMemoryProvider
from .memory.editor import MemoryEditor
from .memory.project import DISCARDED, MERGED
from .metrics.runlog import RunLog, classify_error
from .metrics.usage import Usage, UsageTracker
from .permissions import ApprovalManager, Approver, WorkspacePolicy
from .tools import GlobTool, GrepTool, ListDirTool, RunCommandTool
from .tools.memory_tools import MEMORY_TOOLS, MemoryRun

MAIN_AGENT = "forge-coder"
MAX_STEPS = 30
REVIEW_MAX_STEPS = 15
REVIEW_DIFF_CHARS = 30000  # 交给 Reviewer 的 diff 上限，超出部分让它自行 Read
SUMMARY_DIFF_CHARS, SUMMARY_TALK_CHARS = 12000, 8000  # 生成会话摘要时 diff 与对话要点的长度上限
SAVED_PACKET_CHARS = 20000  # 流程细节随会话保存时，每份参考资料保留的最大长度
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
    """可恢复的会话（任务 worktree 仍存在），按最后一轮对话的时间从新到旧。

    最后对话时间取会话元数据的 last_active；早期保存的会话没有该字段时，取历史中最后一条消息的时间
    （保存时间会在切换会话时更新，不能代表最后对话时间）。
    """
    items = []
    directory = sessions_dir(settings, repo_name)
    for file in directory.glob("*.json") if directory.is_dir() else []:
        data = json.loads(file.read_text(encoding="utf-8"))
        meta = (data.get("metadata") or {}).get(SESSION_META_KEY)
        if meta and (settings.worktrees_dir / repo_name / meta["task_id"]).is_dir():
            history = data.get("history") or []
            last = meta.get("last_active") or (history[-1].get("timestamp") if history else None) or data.get("saved_at")
            items.append({"file": str(file), "saved_at": data.get("saved_at"), **meta, "last_active": (last or "")[:19]})
    return sorted(items, key=lambda x: x["last_active"], reverse=True)


def search_sessions(settings: Settings, repo_name: str, query: str, limit: int = 30) -> dict[str, str]:
    """在会话（可恢复与已归档）的标题、需求、对话内容与归档摘要中查找，返回 {任务ID: 匹配处的片段}。"""
    query = query.strip().lower()
    if not query:
        return {}
    hits: dict[str, str] = {}
    for directory in (sessions_dir(settings, repo_name), archived_dir(settings, repo_name)):
        for file in directory.glob("*.json") if directory.is_dir() else []:
            data = json.loads(file.read_text(encoding="utf-8"))
            meta = (data.get("metadata") or {}).get(SESSION_META_KEY) or {}
            texts = [meta.get("title") or "", *(meta.get("requests") or []), (meta.get("archived") or {}).get("summary") or ""]
            texts += [m.get("content") or "" for m in data.get("history") or [] if m.get("role") in ("user", "assistant", "summary")]
            for text in texts:
                pos = text.lower().find(query)
                if pos >= 0:
                    flat = " ".join(text[max(pos - 24, 0): pos + len(query) + 40].split())
                    hits[meta.get("task_id", file.stem)] = ("…" if pos > 24 else "") + flat + "…"
                    break
            if len(hits) >= limit:
                return hits
    return hits


def _json_list(text: str) -> list[str]:
    """从模型输出中取出 JSON 字符串数组（容忍前后多余文字），取不到时返回空列表。"""
    start, end = text.find("["), text.rfind("]")
    try:
        items = json.loads(text[start:end + 1]) if start >= 0 and end > start else []
    except ValueError:
        return []
    return [" ".join(str(i).split()) for i in items if isinstance(i, str) and i.strip()]


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
        # 主 Agent、子 Agent 与辅助 Agent 的模型可分别选择（.env 中配置范围，第一个为默认）；模型相同的角色共用一个客户端
        self.models = {role: settings.model_for(role) for role in ALL_MODEL_ROLES}
        self._clients: dict = {}
        self.usage = UsageTracker(settings.round_token_limit)  # 本轮与会话累计的 token 用量及来源分布；可设单轮预算
        self.approvals = ApprovalManager(approver, ApprovalAdvisor(None), self.usage)
        self.agent: ReActAgent | None = None
        self._apply_models()
        self.preferences = PreferenceMemory(settings.data_dir)  # 跨会话的用户偏好
        self.project_memory = ProjectMemory(settings.data_dir, repo_name)  # 跨任务的项目知识
        # 两类记忆的修改统一经过编辑器（记录修改、支持撤销）；memory 子 Agent 只能使用这里的记忆工具
        self.memory_editor = MemoryEditor(settings.data_dir, self.project_memory, self.preferences)
        self._memory_run = MemoryRun()
        self.memory_registry = ToolRegistry()
        with redirect_stdout(io.StringIO()):
            for tool_class in MEMORY_TOOLS:
                self.memory_registry.register_tool(tool_class(self.memory_editor, lambda: self._memory_run))
        self.mode = DEFAULT_MODE
        self.set_mode(mode)
        ws.ensure_repo(settings, repo_name)
        self.task: ws.Task | None = None
        self.agent: ReActAgent | None = None
        self.plan_agent: PlanSolveAgent | None = None
        self.seen_files: SeenFilesProvider | None = None
        self.requests: list[str] = []  # 本任务的用户需求（供 /review 与会话恢复）
        self.last_commit: str | None = None
        self.last_context: dict | None = None  # 本轮最后一次请求的上下文窗口用量与分布
        self.last_compaction: dict | None = None  # 本轮（或 /compact）的历史压缩结果
        self.last_task_summary: str | None = None  # 最近结束的任务写入项目记忆的摘要
        self.last_memory_changes: list[dict] = []  # 最近一次归档时 memory 子 Agent 做的修改
        self.title: str | None = None  # 辅助 Agent 在第一轮后生成的会话标题
        self._save_lock = threading.Lock()  # 轮次结束后的标题 / 建议在后台线程中保存
        self._turn_start: int | None = None  # 进行中的轮次开始前的历史长度（压缩时随之调整）
        self.markers: list[dict] = []  # 会话中的合并 / 丢弃记录：类型、提交、时间与发生时的历史位置
        self.last_active: str | None = None  # 最后一轮对话完成的时间，会话列表按它排序（切换、恢复会话不改变）
        self._pending_note: str | None = None  # 下一轮附在需求前给主 Agent 的提示
        self.round_details: list[dict] = []  # 每轮的上下文分布、注入的参考资料、系统提示词与运行轨迹报告（供面板查看）
        self._sink = None  # 本轮事件队列的写入函数，供子 Agent 线程转发事件
        self.last_outcome: dict | None = None  # 本轮结果：completed / stopped / failed（含原因）
        self._runner: asyncio.Task | None = None  # 正在运行的 Agent 任务（停止本轮时取消）
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stopping = False
        self._runlog: RunLog | None = None
        self.approvals.on_answer = self._on_approval_answer

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
            SubAgentTool(self._make_subagent, self.repo_name, on_event=self._forward, should_stop=lambda: self._stopping,
                         on_finish=self._subagent_finished),
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
        self.referenced = ReferencedFilesProvider(root, registry)  # 本轮需求中 @ 引用的文件
        self.agent = ReActAgent(
            MAIN_AGENT, self.llm, tool_registry=registry,
            # 主 Agent 启用智能摘要压缩与框架 TraceLogger（每轮一份运行轨迹报告）
            config=agent_config(self.settings, main=True, trace_dir=self.settings.data_dir / "traces" / self.repo_name / task.id),
            system_prompt=build_system_prompt(self.repo_name, self.notes, self.mode), max_steps=MAX_STEPS,
            # 每次模型调用前注入：仓库地图 + 子 Agent 已读文件 + 本轮 @ 引用的文件 + 用户偏好 + 相关项目记忆
            context_builder=create_context_builder(),
            context_providers=[
                repo_map_provider(root), self.seen_files, self.referenced, PreferencesProvider(self.preferences),
                ProjectMemoryProvider(self.project_memory, lambda: self.task.id if self.task else None),
            ],
            components=AgentComponents(
                # 框架会话存储：每轮结束保存历史、读取缓存与任务信息，供 --resume 恢复
                session_store=SessionStore(str(sessions_dir(self.settings, self.repo_name))),
                summary_llm=self.role_llms["utility"],  # 历史压缩的智能摘要使用辅助 Agent 模型
            ),
        )
        self.agent.on_compress = self._on_compress
        self._build_plan_agent()

    def _build_plan_agent(self) -> None:
        """可选范式：框架的 Plan-and-Solve（规划器生成步骤 → 执行器逐步完成），作为框架范式演示；每轮独立，切换模型时重建。"""
        self.plan_agent = PlanSolveAgent(
            MAIN_AGENT, self.llm, tool_registry=self.registry, config=agent_config(self.settings),
            executor_prompt=build_system_prompt(self.repo_name, self.notes, self.mode),
            max_tool_iterations=PLAN_STEP_TOOL_ITERATIONS,
        )

    # ------------------------------------------------------------ 模型选择
    def _client(self, model: str):
        if model not in self._clients:
            self._clients[model] = create_llm(self.settings, model)
        return self._clients[model]

    def _apply_models(self) -> None:
        """按 self.models 设置各角色的模型客户端；已建好的主 Agent、PlanSolve、历史压缩与审批助手同步替换。"""
        self.llm = self._client(self.models[MAIN_ROLE])
        self.role_llms = {role: self._client(self.models[role]) for role in MODEL_ROLES}
        self.llms = list({id(c): c for c in [self.llm, *self.role_llms.values()]}.values())  # 本轮绑定预算的全部客户端
        self.approvals.advisor.llm = self.role_llms["utility"]
        if self.agent is not None:
            self.agent.llm = self.llm
            self.agent._component_options.summary_llm = self.role_llms["utility"]  # 历史压缩的智能摘要
            self._build_plan_agent()

    def model_options(self) -> dict[str, list[str]]:
        return {role: self.settings.options_for(role) for role in ALL_MODEL_ROLES}

    def set_model(self, role: str, model: str) -> dict[str, str]:
        """在 .env 配置的范围内切换某个角色的模型；下一轮生效，选择随会话保存。"""
        if role not in ALL_MODEL_ROLES:
            raise ValueError(f"未知角色 {role}，可选：{' / '.join(ALL_MODEL_ROLES)}")
        if model not in self.settings.options_for(role):
            raise ValueError(f"{role} 的可选模型为：{', '.join(self.settings.options_for(role))}")
        self.models[role] = model
        self._apply_models()
        self._save_session()
        return dict(self.models)

    def agent_models(self) -> dict[str, str]:
        """事件来源（agent_name）→ 模型，用于用量与运行记录中标注实际使用的模型。"""
        return {MAIN_AGENT: self.models[MAIN_ROLE], **{role: self.models[role] for role in MODEL_ROLES}}

    def _make_subagent(self, role: str, system_prompt: str, max_steps: int) -> ReActAgent:
        """子 Agent 工厂：按角色选择模型，共享主注册表（由 subagent_scope 按角色过滤），附带仓库地图。
        memory 使用只有记忆工具的注册表，不附带仓库地图；主 Agent 派出时记下本次运行的来源与会话。"""
        if role == "memory":
            if self._memory_run.trigger != "archive":
                self._memory_run = MemoryRun(trigger="main", task=self.task.id if self.task else "")
            return ReActAgent(role, self.role_llms["memory"], tool_registry=self.memory_registry,
                              config=agent_config(self.settings), system_prompt=system_prompt, max_steps=max_steps)
        return ReActAgent(
            role, self.role_llms[role], tool_registry=self.registry, config=agent_config(self.settings),
            system_prompt=system_prompt, max_steps=max_steps,
            context_builder=create_context_builder(),
            context_providers=[repo_map_provider(self.task.path)],
        )

    def _subagent_finished(self, role: str, run) -> None:
        """memory 子 Agent 未正常完成（出错、被停止、步数用尽）时撤销它本次的全部修改，保留原有记忆不变。"""
        if role == "memory" and getattr(run, "status", "") != "completed":
            self.memory_editor.rollback([c["id"] for c in self._memory_run.changes])
            self._memory_run.changes.clear()

    def _approval_log_file(self, task: ws.Task) -> Path:
        return self.settings.data_dir / "approvals" / self.repo_name / f"{task.id}.jsonl"

    def _ensure_task(self) -> ws.Task:
        if self.task is None:
            self.task = ws.create_task(self.settings, self.repo_name)
            self.usage.session = Usage()  # 会话累计用量从新会话开始计算
            self._build_agents(self.task)
            self.approvals.start_task(self._approval_log_file(self.task))
            self.requests = []
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

    def _on_approval_answer(self, request_id: str, answer: str) -> None:
        if self._runlog:
            self._runlog.answer_approval(request_id, answer, time.time())

    # ------------------------------------------------------------ 驱动一轮 Agent
    async def _drive(self, agent, text: str, scope=None):
        """运行 agent.arun_stream(text)，合并子 Agent 事件、统计用量与上下文窗口；对话轮与 /review 共用。"""
        self.approvals.request = text
        self.usage.start(self.llms, self.agent_models())
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
        self._stopping = False

        async def pump():
            try:
                with scope or nullcontext():
                    async for event in agent.arun_stream(text):
                        queue.put_nowait(event)
            except asyncio.CancelledError:
                if not self._stopping:  # 用户停止本轮：框架已补全中断的对话历史，正常结束
                    raise
            finally:
                loop.call_soon_threadsafe(queue.put_nowait, _DONE)

        runner = self._runner = asyncio.create_task(pump())
        self._loop = loop
        try:
            while (event := await queue.get()) is not _DONE:
                self.usage.observe(event)
                yield event
            await runner  # Agent 的异常在这里抛出
        finally:
            self._sink = self.approvals.sink = None
            self._runner = None
            if not runner.done():  # 外部取消（如 CLI 中断、连接断开）：等框架补全中断的对话历史
                runner.cancel()
                await asyncio.gather(runner, return_exceptions=True)
            self.usage.finish()  # 失败的轮次也计入用量
            self.last_context = context_usage(getattr(agent, "last_context_diagnostics", None))

    async def stream(self, text: str, paradigm: str = REACT):
        """一轮对话：异步事件流（StreamEvent），合并主 Agent 与子 Agent 的事件；结束后提交改动并保存会话。

        paradigm：react（默认，受当前工作模式约束）或 plansolve（框架范式演示，plan 模式下不可用）。
        事件的 agent_name 区分来源：forge-coder 为主 Agent，explorer / tester / reviewer 为子 Agent，utility 为辅助 Agent。
        CLI 与 Web 面板共用；提交结果放在 self.last_commit，本轮用量放在 self.usage.last，结果放在 self.last_outcome。
        无论完成、被停止还是出错，已产生的改动都会提交，本轮的运行记录照常保存（出错不再向外抛出）。
        """
        if paradigm == PLANSOLVE and self.mode == PLAN:
            raise ValueError("plan 模式只读，PlanSolve 会修改文件；请先切换到 auto 或 manual 模式")
        task = self._ensure_task()
        self._apply_mode()
        if paradigm == PLANSOLVE:
            self.plan_agent.executor.system_prompt = self.agent.system_prompt
        agent = self.plan_agent if paradigm == PLANSOLVE else self.agent
        self.last_commit = self.last_outcome = None
        self.requests.append(text)
        self.last_compaction = None
        self._turn_start = len(self.agent.history_manager.get_history())
        # 回溯所需：本轮开始前的对话位置、任务分支提交与已提交轮数
        start = {"head": ws.head(task), "rounds": task.rounds}
        self._runlog = RunLog(text, len(self.requests), time.time())
        model_text = f"{self._pending_note}\n\n{text}" if self._pending_note else text
        self._pending_note = None
        self.referenced.paths = find_references(task.path, text) if paradigm == REACT else []
        outcome = None
        try:
            async for event in self._drive(agent, model_text):
                self._runlog.observe(event)
                yield event
            outcome = self._outcome(agent)
        except Exception as exc:  # 失败原因随本轮结果返回（turn_end / CLI），不中断会话
            outcome = classify_error(exc)
        finally:
            self.referenced.paths = []  # 引用只作用于本轮
            self._finish_round(agent, task, text, paradigm, start, outcome or {"status": "stopped", "reason": "本轮已中断"})

    def _outcome(self, agent) -> dict:
        if self._stopping:
            return {"status": "stopped", "reason": "已停止本轮"}
        run = getattr(agent, "last_run", None)
        status = getattr(run, "status", "completed")
        if status == "max_iterations":
            return {"status": "failed", "kind": "max_steps", "reason": f"达到最大步数：主 Agent 用完 {MAX_STEPS} 步仍未完成",
                    "detail": ""}
        return {"status": "completed"}

    def _finish_round(self, agent, task, text: str, paradigm: str, start: dict, outcome: dict) -> None:
        """提交本轮改动并记录流程细节与运行记录（完成、停止、出错共用）。"""
        self.last_outcome = outcome
        self.last_commit = ws.commit_round(task, strip_attachments(text))
        start["history_len"] = self._turn_start  # 本轮结束时若发生了压缩，已在 _on_compress 中调整（可能失效为 None）
        self._turn_start = None
        self._record_round(agent, text, paradigm, start)
        self._runlog = None
        self.last_active = datetime.now().isoformat(timespec="seconds")
        self._save_session()

    # ------------------------------------------------------------ 会话标题与下一步建议（每轮结束后）
    def after_round(self) -> dict:
        """一轮对话结束后由辅助 Agent 生成：第一轮后的会话标题；开启时（设置中）2~3 条下一步建议。
        面板在释放对话锁之后于线程中调用（不阻塞下一轮），CLI 在每轮结束后调用。结果随会话保存。"""
        task, details = self.task, self.round_details
        if task is None or not details:
            return {}
        detail = details[-1]
        result = {}
        if not self.title:
            title = self._utility_text(SESSION_TITLE_PROMPT, strip_attachments(self.requests[0] if self.requests else detail["request"])[:2000])
            title = (title.strip().strip('"“”「」').splitlines() or [""])[0][:24]
            if title:
                result["title"] = self.title = title
        if load_options(self.settings.data_dir)["next_suggestions"] and (detail.get("outcome") or {}).get("status") == "completed":
            answer = next((e["data"].get("result") or "" for e in reversed(detail.get("events", []))
                           if e["type"] == "agent_finish" and e["agent"] == MAIN_AGENT), "")
            text = self._utility_text(NEXT_STEPS_PROMPT, NEXT_STEPS_REQUEST.format(
                request=detail["request"], answer=answer[-2000:] or "（无）",
                outcome=f"已提交改动 {detail['commit']}" if detail.get("commit") else "没有修改代码"))
            items = _json_list(text)[:3]
            if items:
                result["suggestions"] = detail["suggestions"] = items
                result["round"] = detail["round"]
                detail.setdefault("events", []).append(
                    {"type": "suggestions", "agent": "utility", "data": {"items": items, "round": detail["round"]}})
        if result and self.task is task:  # 期间切换了会话时不再保存（离开会话时已保存过）
            self._save_session()
        return result

    def _utility_text(self, system: str, user: str) -> str:
        """辅助 Agent 的单次调用（独立客户端，不受本轮预算影响），用量计入会话累计。"""
        try:
            response = create_llm(self.settings, self.models["utility"]).invoke(
                [{"role": "system", "content": system}, {"role": "user", "content": user}], temperature=0.3)
        except Exception:
            return ""
        tokens = (getattr(response, "usage", None) or {}).get("total_tokens", 0)
        self.usage.session.total_tokens += tokens
        self.usage.session.model_calls += 1
        return (response.content or "").strip()

    def footer(self) -> dict:
        """本轮的结束信息（面板 turn_end 与运行记录共用）。"""
        return {"commit": self.last_commit, "usage": self.usage.last.to_dict() if self.usage.last else None,
                "context": self.last_context, "round": len(self.requests), "outcome": self.last_outcome}

    def _record_round(self, agent, text: str, paradigm: str, start: dict) -> None:
        """记录本轮的流程细节：上下文分布与注入的参考资料、系统提示词、运行轨迹报告、运行记录与回溯信息。"""
        # 框架在每次运行结束时 finalize 运行轨迹（写入统计并关闭），下一轮新建一份，因此一轮对应一份报告
        logger = getattr(agent, "trace_logger", None)
        trace = str(logger.html_path) if logger is not None else None
        packets = [
            {"source": CONTEXT_LABELS.get("packet:" + str(p.metadata.get("source")), p.metadata.get("source")),
             "tokens": count_tokens(p.content), "content": p.content}
            for p in getattr(agent, "last_context_packets", None) or []
        ]
        # 对话历史中的压缩摘要也随请求发给模型：与参考资料一样记录，便于在上下文标签中查看
        if paradigm != PLANSOLVE:
            packets += [{"source": CONTEXT_LABELS["history_summary"], "tokens": count_tokens(m.content), "content": m.content}
                        for m in self.agent.history_manager.get_history() if m.role == "summary"]
        prompt = agent.executor.system_prompt if paradigm == PLANSOLVE else agent.system_prompt
        h = start["history_len"]
        # PlanSolve 不写入主 Agent 历史：恢复时按位置插入运行记录，而不是替换历史中的对应消息
        start["in_history"] = h is not None and len(self.agent.history_manager.get_history()) > h
        self.round_details.append({
            "round": len(self.requests), "request": text, "mode": self.mode, "paradigm": paradigm,
            "commit": self.last_commit, "context": self.last_context,
            "usage": self.usage.last.to_dict() if self.usage.last else None,
            "packets": packets, "system_prompt": prompt, "trace": trace,
            "outcome": self.last_outcome, "start": start, "models": self.agent_models(),
            "events": self._runlog.finish(self.footer(), time.time()) if self._runlog else [],
        })

    # ------------------------------------------------------------ 停止与回溯
    def stop(self) -> bool:
        """停止正在运行的一轮（面板"停止"、CLI Ctrl+C）：可从其他线程调用。没有运行中的一轮时返回 False。"""
        runner, loop = self._runner, self._loop
        if runner is None or runner.done() or loop is None:
            return False
        self._stopping = True  # 子 Agent 在下一个事件处结束
        self.approvals.cancel_pending()
        loop.call_soon_threadsafe(runner.cancel)
        return True

    def rewindable_rounds(self) -> list[int]:
        """可以回溯的轮次（从 1 开始）：有回溯信息、对话位置未被压缩，且在最近一次合并 / 丢弃之后。"""
        result = []
        for i, d in enumerate(self.round_details):
            start = d.get("start") or {}
            h = start.get("history_len")
            if h is None or "head" not in start:
                continue
            # 合并 / 丢弃记录了发生时的轮数；早期记录没有轮数时按对话位置判断
            if any(i < m["round"] if "round" in m else h < m["history_len"] for m in self.markers):
                continue
            result.append(d["round"])
        return result

    def rewind(self, round_no: int) -> dict:
        """回溯到第 round_no 轮开始前：代码回到该轮开始前的提交，主 Agent 历史截断到该轮之前，
        该轮及之后的需求、流程细节与运行记录一并移除。返回该轮的需求与范式（面板放回输入框）。"""
        if self.task is None:
            raise ValueError("当前没有进行中的任务")
        if round_no not in self.rewindable_rounds():
            raise ValueError(f"第 {round_no} 轮不能回溯（已合并到 main、已丢弃，或已被历史压缩）")
        index = next(i for i, d in enumerate(self.round_details) if d["round"] == round_no)
        detail = self.round_details[index]
        start = detail["start"]
        head = ws.reset_to(self.task, start["head"], start["rounds"])
        history = self.agent.history_manager.get_history()[: start["history_len"]]
        self.agent.history_manager.clear()
        for message in history:
            self.agent.history_manager.append(message)
        self.agent._history_token_count = self.agent.token_counter.count_messages(history)
        self.registry.read_metadata_cache.clear()  # 文件已回退，旧的读取元数据失效
        self.requests = self.requests[: round_no - 1]
        self.round_details = self.round_details[:index]
        previous = self.round_details[-1] if self.round_details else {}
        self.last_context = previous.get("context")
        self.usage.last = Usage.from_dict(previous["usage"]) if previous.get("usage") else None
        self.last_commit = None
        self._save_session()
        return {"round": round_no, "text": detail["request"], "paradigm": detail.get("paradigm", REACT), "head": head}

    def system_prompts(self) -> dict[str, str]:
        """主 Agent（当前模式）与各子 Agent 的系统提示词。"""
        prompts = {"主 Agent": build_system_prompt(self.repo_name, getattr(self, "notes", ""), self.mode)}
        for role, spec in ROLES.items():
            prompts[role] = build_subagent_prompt(spec["prompt"], self.repo_name)
        prompts["reviewer"] = build_subagent_prompt(REVIEWER_PROMPT, self.repo_name)
        return prompts

    # ------------------------------------------------------------ 历史压缩
    def compact(self, keep_rounds: int = 0) -> dict:
        """/compact：用辅助 Agent 模型把对话历史压缩为摘要（默认全部压缩，可保留最近几轮），摘要只留在本会话的历史中。"""
        if self.agent is None:
            raise ValueError("当前没有进行中的任务")
        self.last_compaction = None
        result = self.agent.compress_history(keep_rounds)
        if result is None:
            raise ValueError("对话轮数不足，没有可压缩的历史")
        self._save_session()
        return result

    def _on_compress(self, result: dict) -> None:
        """框架压缩历史后的回调（自动压缩与 /compact 共用）：摘要只留在当前会话的历史中（不写入项目记忆），
        这里调整各轮的对话位置。

        历史变为 [摘要] + 原历史[k:]（k 为被压缩的消息数）：之后的轮次位置减 k - 1；
        开始于被压缩部分的轮次无法再同步回退对话，位置记为 None（不能回溯，恢复时按历史显示）。
        """
        self.last_compaction = result
        k = result["compressed_messages"]
        shift = lambda h: h - k + 1 if h is not None and h >= k else None
        for d in self.round_details:
            if d.get("start"):
                d["start"]["history_len"] = shift(d["start"].get("history_len"))
        self.markers = [{**m, "history_len": max(m["history_len"] - k + 1, 0)} for m in self.markers]
        self._turn_start = shift(self._turn_start)

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

    def undo(self) -> dict:
        """撤销最近一轮：即回溯到最近一轮开始前。"""
        if self.task is None or not self.round_details:
            raise ValueError("当前任务没有可撤销的轮次")
        return self.rewind(self.round_details[-1]["round"])

    # ------------------------------------------------------------ 会话保存与恢复（框架 SessionStore）
    def _save_session(self) -> None:
        """把任务信息写入框架会话元数据后保存；文件名为任务 ID，每轮覆盖。"""
        with self._save_lock:
            self._save_session_locked()

    def _save_session_locked(self) -> None:
        if self.task is None or self.agent is None:
            return
        self.agent._session_metadata[SESSION_META_KEY] = {
            "repo": self.repo_name, "task_id": self.task.id, "base": self.task.base, "rounds": self.task.rounds,
            "mode": self.mode, "requests": self.requests,
            "origin": self.task.origin, "markers": self.markers, "usage": self.usage.session.to_dict(),
            "last_active": self.last_active,
            "models": self.models,  # 各角色选择的模型，恢复时沿用
            "title": self.title,
            # 子 Agent 已读文件随会话保存（路径与 mtime），切换或恢复会话后主 Agent 仍可直接使用
            "seen_files": self.seen_files.snapshot() if self.seen_files else {},
            # 流程细节随会话保存，恢复会话或重启面板后仍可查看（参考资料过长时截断）
            "round_details": [
                {**d, "packets": [{**pk, "content": pk["content"][:SAVED_PACKET_CHARS]} for pk in d["packets"]]}
                for d in self.round_details
            ],
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
        saved = meta.get("models") or {}  # 保存的选择不在当前 .env 范围内时回到默认
        self.models = {role: m if (m := saved.get(role)) in self.settings.options_for(role) else self.settings.model_for(role)
                       for role in ALL_MODEL_ROLES}
        self._apply_models()
        self._build_agents(self.task)
        with redirect_stdout(io.StringIO()):  # 框架加载时打印的提示不需要
            self.agent.load_session(meta["file"])
        # load_session 替换了读取缓存字典，plan 子集需要重新共享
        self.plan_registry.read_metadata_cache = self.registry.read_metadata_cache
        self.seen_files.restore(meta.get("seen_files"))  # 在 load_session 之后写回读取缓存
        self.requests = list(meta.get("requests", []))
        self.round_details = list(meta.get("round_details", []))
        if self.round_details:  # 恢复最近一轮的上下文分布与用量，面板的上下文 / 运行标签直接可见
            self.last_context = self.round_details[-1].get("context")
            self.usage.last = Usage.from_dict(self.round_details[-1].get("usage")) if self.round_details[-1].get("usage") else None
        self.markers = list(meta.get("markers", []))
        self.title = meta.get("title") or None
        self.last_active = meta.get("last_active") or None  # list_sessions 已为早期会话补上最后一条消息的时间
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
                             "history_len": len(self.agent.history_manager.get_history()), "round": len(self.requests)})

    def accept(self) -> str:
        """合并到 main，会话继续：之后的改动从合并点重新计算，可再次合并。返回合并后的提交。"""
        try:
            commit = ws.merge_task(self._require_task())
        except ws.WorkspaceError as exc:
            raise ValueError(str(exc)) from exc
        self._mark("merge", commit)  # 已合并的轮次不能再回溯
        self._save_session()
        return commit

    def discard(self) -> None:
        """丢弃尚未合并的改动，会话继续；下一轮提示主 Agent 代码已回退。"""
        task = self._require_task()
        if not self.diff().strip():
            raise ValueError("没有尚未合并的改动")
        ws.discard_changes(task)
        self._mark("discard", task.base[:7])
        self.registry.read_metadata_cache.clear()  # 文件已回退，旧的读取元数据失效
        self._pending_note = DISCARD_NOTE
        self._save_session()

    def archive(self, summarize: bool = True) -> dict:
        """归档会话：memory 子 Agent 写入会话摘要并整理项目记忆与用户偏好，对话保存为只读存档，删除 worktree 与任务分支
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
                "memory_changes": self.last_memory_changes,
            }
            target = archived_dir(self.settings, self.repo_name) / file.name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        ws.reject_task(task)
        self._end_task()
        return {"outcome": outcome, "merges": merges, "summary": self.last_task_summary,
                "memory_changes": self.last_memory_changes}

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
        """归档时派出 memory 子 Agent：写入会话摘要并整理已有记忆。它出错或没有写入摘要时撤销其修改，
        改由辅助 Agent 单独生成会话摘要写入（后备），保证每个归档会话都有摘要。"""
        self.last_task_summary, self.last_memory_changes = None, []
        if summary_input is None:
            return
        task_id = self.task.id
        self._memory_run = MemoryRun(trigger="archive", task=task_id, summary_kind=outcome)
        self.usage.start(self.llms, self.agent_models())  # 归档的模型调用计入会话用量
        try:
            agent = self._make_subagent("memory", build_subagent_prompt(ROLES["memory"]["prompt"], self.repo_name),
                                        ROLES["memory"]["max_steps"])
            with subagent_scope(agent, ROLES["memory"]["filter"]()):
                for event in sync_events(agent.arun_stream(MEMORY_ARCHIVE_REQUEST.format(task_id=task_id, material=summary_input))):
                    self.usage.observe(event)
                status = agent.last_run.status
        except Exception:  # 模型不可用等：按失败处理
            status = "failed"
        changes = self._memory_run.changes
        summary = next((c["after"] for c in changes if c.get("kind") == outcome and c["op"] == "add"), None)
        if status != "completed" or summary is None:
            self.memory_editor.rollback([c["id"] for c in changes])
            changes = [self.memory_editor.add("project", self._fallback_summary(summary_input), trigger="archive",
                                              task=task_id, kind=outcome, reason="memory 子 Agent 未完成，由辅助 Agent 生成会话摘要")]
            summary = changes[0]["after"]
        self.usage.finish()
        self._memory_run = MemoryRun()
        self.last_task_summary, self.last_memory_changes = summary, changes

    def _fallback_summary(self, summary_input: str) -> str:
        messages = [{"role": "system", "content": TASK_SUMMARY_PROMPT}, {"role": "user", "content": summary_input}]
        try:
            return self.role_llms["utility"].invoke(messages, temperature=0.2).content.strip()
        except Exception as exc:  # 模型不可用时只记录需求，任务照常结束
            return f"（摘要生成失败：{type(exc).__name__}）\n目标：" + "；".join(self.requests)

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
        self.last_commit = self.last_outcome = None
        self.title = None
        self.requests, self.round_details, self.markers = [], [], []
        self._pending_note = None
        self.last_active = None
        self.last_context = None
        self.usage.last = None
        self.usage.session = Usage()
        self.approvals.start_task(None)

    # ------------------------------------------------------------ @ 引用补全
    def files_root(self) -> Path:
        """当前可引用文件的位置：有任务时为任务 worktree，否则为测试项目的 main。"""
        return self.task.path if self.task else ws.repo_path(self.settings, self.repo_name)

    def list_files(self, query: str = "") -> list[str]:
        return ws.list_files(self.files_root(), query)

    def status(self) -> dict:
        return {
            "repo": self.repo_name,
            "model": self.llm.model,
            "models": dict(self.models),
            "mode": self.mode,
            "task": self.task.id if self.task else None,
            "branch": self.task.branch if self.task else None,
            "rounds": self.task.rounds if self.task else 0,
            "approvals": len(self.approvals.records),
            "preferences": len(self.preferences.items()),
            "project_memory": len(self.project_memory.entries()),
            "session_tokens": self.usage.session.total_tokens,
        }

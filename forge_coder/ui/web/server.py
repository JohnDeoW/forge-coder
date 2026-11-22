"""Web 面板服务：FastAPI 提供接口与 SSE 事件流，前端为 web/ 下的 React 应用（构建产物在 dist/）。

- 一个进程服务多个测试项目：每个项目一个 CoderSession（按需创建），左栏"项目 → 会话"可切换；
- 对话与 /review 以 SSE 推送 Agent 事件，本轮结束追加 turn_end（提交号、用量、上下文、预算、状态）；
  对话轮在释放锁之后再由辅助 Agent 生成会话标题与下一步建议，以 session_title / suggestions 事件推送（不阻塞下一轮）；
- 同一时间只运行一轮对话或一个修改性操作（全局锁）；审批回复与停止本轮不加锁（发生在对话进行中）。
"""

import asyncio
import json
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from ... import workspace as ws
from ...agents.modes import DEFAULT_MODE
from ...config import CONTEXT_WINDOW, MODEL_ROLES, load_options, load_settings, save_options
from ...memory import ProjectMemory
from ...metrics.runlog import event_payload
from ...permissions import WEB_APPROVAL
from ...session import CoderSession, list_archived, list_sessions, load_archived, search_sessions
from .history import history_events, session_events

DIST_DIR = Path(__file__).with_name("dist")  # web/ 前端 npm run build 的输出
NOT_BUILT = """<!doctype html><meta charset="utf-8"><title>ForgeCoder</title>
<body style="font:14px system-ui;padding:40px;line-height:1.8">
<h3>面板前端尚未构建</h3><p>在 forge-coder/web 目录执行：</p>
<pre>npm install
npm run build</pre><p>构建完成后刷新本页。开发时也可运行 <code>npm run dev</code>，由 Vite 把 /api 代理到本服务。</p></body>"""


class TextBody(BaseModel):
    text: str


class ChatRequest(BaseModel):
    text: str
    paradigm: str = "react"  # react（默认）或 plansolve（框架范式演示）


class ApprovalReply(BaseModel):
    request_id: str
    answer: str  # once / always / deny


class ValueBody(BaseModel):
    value: str


class CompactBody(BaseModel):
    keep_rounds: int = 0


class RoundBody(BaseModel):
    round: int


class OptionsBody(BaseModel):
    next_suggestions: bool | None = None


class ModelBody(BaseModel):
    role: str  # main / explorer / tester / reviewer / utility
    value: str


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False, default=str)}\n\n"


def _title(text: str, limit: int = 40) -> str:
    text = " ".join((text or "").split())
    return text[:limit] + ("…" if len(text) > limit else "")


class Hub:
    """多个测试项目的 CoderSession；current 为面板当前显示的项目。"""

    def __init__(self, repo: str, mode: str):
        self.settings = load_settings()
        self.mode = mode
        self.sessions: dict[str, CoderSession] = {}
        self.current = repo
        self.get(repo)

    def get(self, repo: str | None = None) -> CoderSession:
        repo = repo or self.current
        if repo not in ws.list_samples(self.settings):
            raise HTTPException(404, f"没有名为 {repo} 的测试项目")
        if repo not in self.sessions:
            self.sessions[repo] = CoderSession(self.settings, repo, approver=WEB_APPROVAL, mode=self.mode)
        return self.sessions[repo]

    @property
    def session(self) -> CoderSession:
        return self.get()

    def projects(self) -> list[dict]:
        """全部测试项目模板：模板说明、是否已生成，以及已生成项目的会话与项目记忆数。"""
        result = []
        for repo in ws.list_samples(self.settings):
            info = {"name": repo, "current": repo == self.current, "generated": ws.is_generated(self.settings, repo),
                    **ws.sample_info(self.settings, repo)}
            if not info["generated"]:  # 未生成的项目没有会话与记忆，生成（首次切换到该项目）时从模板创建仓库
                result.append({**info, "active": None, "resumable": [], "ended": [], "memory": 0, "sessions": 0})
                continue
            live = self.sessions.get(repo)
            active = None
            if live and live.task:
                active = {"task_id": live.task.id, "title": live.title or _title(live.requests[0] if live.requests else "新会话"),
                          "mode": live.mode, "rounds": live.task.rounds, "last_active": live.last_active}
            resumable = [
                {"task_id": s["task_id"], "title": s.get("title") or _title((s.get("requests") or ["（无需求）"])[0]), "mode": s.get("mode"),
                 "rounds": s.get("rounds", 0), "last_active": s.get("last_active") or s.get("saved_at")}
                for s in list_sessions(self.settings, repo) if not active or s["task_id"] != active["task_id"]
            ]
            ended = [
                {"task_id": a["task_id"], "title": a.get("title") or _title((a.get("requests") or ["（无需求）"])[0]),
                 "state": a["archived"]["outcome"], "time": a["archived"]["time"], "merges": a["archived"]["merges"],
                 "summary": a["archived"].get("summary") or "", "memory_changes": a["archived"].get("memory_changes") or []}
                for a in list_archived(self.settings, repo)
            ]
            memory = len(ProjectMemory(self.settings.data_dir, repo).entries())
            result.append({**info, "active": active, "resumable": resumable, "ended": ended, "memory": memory,
                           "sessions": len(resumable) + len(ended) + bool(active)})
        return result


def create_app(repo: str, mode: str = DEFAULT_MODE) -> FastAPI:
    app = FastAPI(title="ForgeCoder")
    hub = app.state.hub = Hub(repo, mode)
    lock = asyncio.Lock()  # 同一时间只运行一轮对话或一个修改性操作

    def task_session() -> CoderSession:
        session = hub.session
        if session.task is None:
            raise HTTPException(400, "当前没有进行中的任务")
        return session

    async def locked(fn, *args):
        """在锁内（线程中）执行同步操作；ValueError 转为 400。"""
        if lock.locked():
            raise HTTPException(409, "上一轮还在运行")
        async with lock:
            try:
                return await asyncio.to_thread(fn, *args)
            except ValueError as exc:
                raise HTTPException(400, str(exc))

    def state() -> dict:
        s = hub.session
        return {
            "status": s.status(),
            "usage": {"last": s.usage.last.to_dict() if s.usage.last else None, "session": s.usage.session.to_dict()},
            "context": s.last_context,
            "budget": {"limit": s.usage.token_limit},
            "running": lock.locked(),
            "rewindable": s.rewindable_rounds() if s.task else [],
        }

    # ---------------------------------------------------------------- 状态与项目、会话
    @app.get("/api/state")
    def get_state():
        return state()

    @app.get("/api/projects")
    def projects():
        return {"projects": hub.projects()}

    @app.post("/api/project")
    async def switch_project(body: ValueBody):
        await locked(hub.get, body.value)
        hub.current = body.value
        return state()

    @app.post("/api/sessions/new")
    async def new_session():
        if hub.session.task is not None:
            await locked(hub.session.detach)
        return state()

    @app.post("/api/sessions/resume")
    async def resume(body: ValueBody):
        session = hub.session
        if session.task is not None and session.task.id == body.value:
            return state()

        def run():
            if session.task is not None:
                session.detach()
            session.resume(body.value)

        await locked(run)
        return state()

    @app.get("/api/search")
    def search(q: str = ""):
        """在各项目会话的标题、需求、对话内容与归档摘要中查找：{项目: {任务ID: 片段}}。"""
        return {"hits": {repo: hits for repo in ws.list_samples(hub.settings)
                         if (hits := search_sessions(hub.settings, repo, q))}}

    @app.get("/api/history")
    def history():
        return {"events": session_events(hub.session)}

    @app.get("/api/archived/{task_id}")
    def archived(task_id: str):
        """归档会话的只读对话与会话摘要。"""
        try:
            messages, meta = load_archived(hub.settings, hub.current, task_id)
        except FileNotFoundError:
            raise HTTPException(404, "没有这个归档会话")
        return {"events": history_events(messages, meta.get("markers", []), meta.get("round_details", [])),
                "archived": meta["archived"], "rounds": meta.get("round_details", []),
                "requests": meta.get("requests", [])}

    # ---------------------------------------------------------------- 对话（SSE）
    def sse_turn(source, with_commit: bool):
        if lock.locked():
            raise HTTPException(409, "上一轮还在运行")
        session = hub.session

        async def events():
            async with lock:
                outcome = None
                try:
                    async for event in source():
                        yield _sse(event_payload(event))
                except Exception as exc:  # 对话轮的失败已由会话归类到 outcome；这里处理审查等其他失败
                    outcome = {"status": "failed", "kind": "program", "reason": "程序异常", "detail": f"{type(exc).__name__}: {exc}"}
                    yield _sse({"type": "error", "agent": "forge-coder", "data": {"error": outcome["detail"]}})
                if with_commit:
                    footer = session.footer()
                else:
                    usage = session.usage.last.to_dict() if session.usage.last else None
                    footer = {"commit": None, "usage": usage, "context": session.last_context, "round": None,
                              "outcome": outcome or ({"status": "stopped", "reason": "已停止本轮"} if session._stopping else None)}
                yield _sse({"type": "turn_end", "data": {
                    **footer, "budget": {"limit": session.usage.token_limit},
                    "compaction": session.last_compaction if with_commit else None, "status": session.status(),
                    "rewindable": session.rewindable_rounds() if session.task else [],
                }})
            if with_commit:  # 锁已释放：辅助 Agent 生成会话标题与下一步建议，期间可以开始下一轮
                extra = await asyncio.to_thread(session.after_round)
                if "title" in extra:
                    yield _sse({"type": "session_title", "agent": "utility", "data": {"title": extra["title"]}})
                if "suggestions" in extra:
                    yield _sse({"type": "suggestions", "agent": "utility",
                                "data": {"items": extra["suggestions"], "round": extra["round"]}})

        return StreamingResponse(events(), media_type="text/event-stream")

    @app.post("/api/chat")
    async def chat(req: ChatRequest):
        session = hub.session
        return sse_turn(lambda: session.stream(req.text, req.paradigm), with_commit=True)

    @app.post("/api/review")
    async def review():
        return sse_turn(task_session().review, with_commit=False)

    @app.post("/api/approval")
    def approval(reply: ApprovalReply):
        # 不加锁：审批发生在一轮对话进行中（对话持有锁），这里只是把结果交给等待中的工具调用
        if not hub.session.resolve_approval(reply.request_id, reply.answer):
            raise HTTPException(404, "审批请求不存在或已结束")
        return {"ok": True}

    @app.post("/api/stop")
    def stop():
        """停止正在运行的一轮（不加锁：对话持有锁）；SSE 随后以 turn_end 结束。"""
        return {"stopped": hub.session.stop()}

    @app.get("/api/approvals")
    def approvals():
        return {"records": hub.session.approval_log}

    @app.post("/api/mode")
    def set_mode(body: ValueBody):
        if lock.locked():
            raise HTTPException(409, "本轮对话进行中，请结束后再切换模式")
        try:
            hub.session.set_mode(body.value)
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        hub.mode = hub.session.mode
        return state()

    @app.post("/api/model")
    def set_model(body: ModelBody):
        """切换当前会话某个角色的模型（.env 配置的范围内），下一轮生效。"""
        if lock.locked():
            raise HTTPException(409, "本轮对话进行中，请结束后再切换模型")
        try:
            hub.session.set_model(body.role, body.value)
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        return state()

    # ---------------------------------------------------------------- 改动审查与任务结束
    @app.get("/api/changes")
    def changes():
        session = hub.session
        if session.task is None:
            return {"diff": "", "commits": [], "branch": None}
        return {"diff": session.diff(), "commits": ws.task_commits(session.task), "branch": session.task.branch}

    @app.post("/api/undo")
    async def undo():
        return {**await locked(task_session().undo), **state()}

    @app.post("/api/rewind")
    async def rewind(body: RoundBody):
        """回溯到第 round 轮开始前；返回该轮需求与范式，前端放回输入框。"""
        return {**await locked(task_session().rewind, body.round), **state()}

    @app.post("/api/accept")
    async def accept():
        """合并到 main，会话继续。"""
        return {"commit": await locked(task_session().accept), **state()}

    @app.post("/api/discard")
    async def discard():
        """丢弃尚未合并的改动，会话继续。"""
        await locked(task_session().discard)
        return state()

    @app.post("/api/archive")
    async def archive():
        # 归档时辅助 Agent 生成会话摘要（一次模型调用），在线程中执行不阻塞事件循环
        result = await locked(task_session().archive)
        return {**result, **state()}

    @app.post("/api/reset")
    async def reset():
        await locked(hub.session.reset)
        return state()

    @app.post("/api/compact")
    async def compact(body: CompactBody):
        result = await locked(task_session().compact, body.keep_rounds)
        return {"compaction": result, **state()}

    # ---------------------------------------------------------------- 流程细节与运行轨迹
    @app.get("/api/details")
    def details():
        session = hub.session
        return {"prompts": session.system_prompts(), "rounds": session.round_details, "window": CONTEXT_WINDOW}

    @app.get("/api/trace/{index}")
    def trace(index: int):
        rounds = hub.session.round_details
        path = rounds[index]["trace"] if 0 <= index < len(rounds) else None
        if not path or not Path(path).is_file():
            raise HTTPException(404, "该轮没有运行轨迹报告")
        return FileResponse(path, media_type="text/html")

    # ---------------------------------------------------------------- 用户偏好与项目记忆（用户操作经记忆编辑器记录，来源为用户）
    def user_edit(repo: str | None, fn, *args):
        session = hub.get(repo)
        task = session.task.id if session.task else ""
        try:
            return fn(session.memory_editor, task, *args)
        except (ValueError, KeyError) as exc:
            raise HTTPException(400, str(exc))

    def preferences_state() -> dict:
        editor = hub.session.memory_editor
        return {"items": hub.session.preferences.items(), "agent_items": sorted(editor.agent_preferences())}

    @app.get("/api/preferences")
    def preferences():
        return preferences_state()

    @app.post("/api/preferences")
    def add_preference(body: TextBody):
        user_edit(None, lambda e, t: e.add("preference", body.text, trigger="user", task=t))
        return preferences_state()

    @app.put("/api/preferences/{index}")
    def edit_preference(index: int, body: TextBody):
        user_edit(None, lambda e, t: e.update("preference", index, body.text, trigger="user", task=t))
        return preferences_state()

    @app.delete("/api/preferences/{index}")
    def delete_preference(index: int):
        """index 从 1 开始；0 表示全部删除。"""
        count = len(hub.session.preferences.items())
        for i in ([index] if index else range(count, 0, -1)):
            user_edit(None, lambda e, t, i=i: e.delete("preference", i, trigger="user", task=t))
        return preferences_state()

    @app.get("/api/memory/{repo}")
    def memory(repo: str):
        return {"entries": hub.get(repo).project_memory.entries()}

    @app.put("/api/memory/{repo}/{index}")
    def edit_memory(repo: str, index: int, body: TextBody):
        user_edit(repo, lambda e, t: e.update("project", index, body.text, trigger="user", task=t))
        return {"entries": hub.get(repo).project_memory.entries()}

    @app.delete("/api/memory/{repo}/{index}")
    def delete_memory(repo: str, index: int):
        """index 从 1 开始；0 表示清空该项目的记忆。"""
        count = len(hub.get(repo).project_memory.entries())
        for i in ([index] if index else range(count, 0, -1)):
            user_edit(repo, lambda e, t, i=i: e.delete("project", i, trigger="user", task=t))
        return {"entries": hub.get(repo).project_memory.entries()}

    @app.get("/api/memory-changes/{repo}")
    def memory_changes(repo: str):
        """该项目的项目记忆与用户偏好的修改记录（新到旧）。"""
        return {"changes": hub.get(repo).memory_editor.changes()}

    @app.post("/api/memory-changes/{repo}/{change_id}/undo")
    def undo_memory_change(repo: str, change_id: str):
        user_edit(repo, lambda e, t: e.undo(change_id))
        return {"changes": hub.get(repo).memory_editor.changes(), **preferences_state(),
                "entries": hub.get(repo).project_memory.entries()}

    @app.post("/api/options")
    def set_options(body: OptionsBody):
        """界面选项（如下一步建议开关），保存在 data/options.json，CLI 与面板共用。"""
        return save_options(hub.settings.data_dir, **body.model_dump(exclude_none=True))

    @app.get("/api/settings")
    def settings():
        s = hub.settings
        return {
            "models": {"主 Agent": s.model, **{role: s.model_for(role) for role in MODEL_ROLES}},  # 各角色默认模型
            "model_options": hub.session.model_options(),  # 各角色可选模型（.env 配置的范围）
            "context_window": CONTEXT_WINDOW, "compact_threshold": s.compact_threshold,
            "compact_keep_rounds": s.compact_keep_rounds, "round_token_limit": s.round_token_limit or None,
            "options": load_options(s.data_dir),
        }

    # ---------------------------------------------------------------- 前端页面
    if (DIST_DIR / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=DIST_DIR / "assets"), name="assets")

    @app.get("/", response_class=HTMLResponse)
    def index():
        page = DIST_DIR / "index.html"
        return page.read_text(encoding="utf-8") if page.is_file() else NOT_BUILT

    return app


def _bindable(port: int) -> bool:
    import socket

    with socket.socket() as sock:
        try:
            sock.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def serve(repo: str, port: int, mode: str = DEFAULT_MODE) -> None:
    import socket

    import uvicorn

    # Windows 会动态保留部分端口段（Hyper-V / WinNAT），重启后可能变化；首选端口不可用时改用系统分配的空闲端口
    if not _bindable(port):
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            fallback = sock.getsockname()[1]
        print(f"端口 {port} 不可用（可能被系统保留），改用 {fallback}；也可用 --port 指定", flush=True)
        port = fallback
    if not (DIST_DIR / "index.html").is_file():
        print("提示：面板前端尚未构建，请在 web/ 目录执行 npm install && npm run build", flush=True)
    print(f"ForgeCoder 面板: http://127.0.0.1:{port}", flush=True)
    uvicorn.run(create_app(repo, mode), host="127.0.0.1", port=port, log_level="warning")

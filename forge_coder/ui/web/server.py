"""简易 Web 面板：复用 CoderSession 与 Agent 事件流，通过 SSE 推送到浏览器。

供阶段检查使用：对话输入、步骤时间线、diff/修改记录查看、接受/拒绝任务、还原测试项目。
"""

import asyncio
import json
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel

from ..format import tool_result_text
from ...config import load_settings
from ...agents.modes import DEFAULT_MODE
from ...permissions import WEB_APPROVAL
from ...session import CoderSession

INDEX_HTML = Path(__file__).with_name("index.html")


class ApprovalReply(BaseModel):
    request_id: str
    answer: str  # once / always / deny


class ChatRequest(BaseModel):
    text: str
    paradigm: str = "react"  # react（默认）或 plansolve（框架范式演示）


class ModeRequest(BaseModel):
    mode: str  # plan / auto / manual


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False, default=str)}\n\n"


def _event_payload(event) -> dict:
    data = dict(event.data)
    if "result" in data and event.type.value == "tool_call_finish":
        data["result"] = tool_result_text(data["result"])
    data.pop("arguments", None)  # 已有解析后的 args
    return {"type": event.type.value, "agent": event.agent_name, "data": data}


def create_app(repo: str, mode: str = DEFAULT_MODE) -> FastAPI:
    app = FastAPI(title="ForgeCoder")
    # manual 模式下白名单外命令在面板中确认；auto 模式由辅助 Agent 审批
    session = CoderSession(load_settings(), repo, approver=WEB_APPROVAL, mode=mode)
    lock = asyncio.Lock()  # 同一时间只运行一轮对话或一个审查操作

    def require_task():
        if session.task is None:
            raise HTTPException(400, "当前没有进行中的任务")

    @app.get("/", response_class=HTMLResponse)
    def index():
        return INDEX_HTML.read_text(encoding="utf-8")

    @app.get("/api/status")
    def status():
        return session.status()

    def sse_turn(source, with_commit: bool):
        """把一轮事件流（对话或 /review）以 SSE 推送，结束时追加 turn_end（提交号、用量、上下文、状态）。"""
        if lock.locked():
            raise HTTPException(409, "上一轮还在运行")

        async def events():
            async with lock:
                try:
                    async for event in source():
                        yield _sse(_event_payload(event))
                except Exception as exc:
                    yield _sse({"type": "error", "data": {"error": str(exc)}})
                usage = session.usage.last.to_dict() if session.usage.last else None
                commit = session.last_commit if with_commit else None
                yield _sse({"type": "turn_end", "data": {"commit": commit, "usage": usage, "context": session.last_context, "status": session.status()}})

        return StreamingResponse(events(), media_type="text/event-stream")

    @app.post("/api/chat")
    async def chat(req: ChatRequest):
        return sse_turn(lambda: session.stream(req.text, req.paradigm), with_commit=True)

    @app.post("/api/review")
    async def review():
        require_task()
        return sse_turn(session.review, with_commit=False)

    @app.post("/api/undo")
    async def undo():
        require_task()
        async with lock:
            try:
                return {"commit": session.undo(), "status": session.status()}
            except ValueError as exc:
                raise HTTPException(400, str(exc))

    @app.post("/api/mode")
    def set_mode(req: ModeRequest):
        if lock.locked():
            raise HTTPException(409, "本轮对话进行中，请结束后再切换模式")
        try:
            session.set_mode(req.mode)
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        return session.status()

    @app.get("/api/approvals")
    def approvals():
        return {"records": session.approval_log}

    @app.post("/api/approval")
    def approval(reply: ApprovalReply):
        # 不加锁：审批发生在一轮对话进行中（对话持有锁），这里只是把结果交给等待中的工具调用
        if not session.resolve_approval(reply.request_id, reply.answer):
            raise HTTPException(404, "审批请求不存在或已结束")
        return {"ok": True}

    @app.get("/api/diff")
    def diff():
        return {"text": session.diff()}

    @app.get("/api/log")
    def log():
        return {"text": session.log()}

    @app.post("/api/accept")
    async def accept():
        require_task()
        async with lock:
            return {"commit": session.accept(), "status": session.status()}

    @app.post("/api/reject")
    async def reject():
        require_task()
        async with lock:
            session.reject()
            return {"status": session.status()}

    @app.post("/api/reset")
    async def reset():
        async with lock:
            session.reset()
            return {"status": session.status()}

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
    print(f"ForgeCoder 面板: http://127.0.0.1:{port}", flush=True)
    uvicorn.run(create_app(repo, mode), host="127.0.0.1", port=port, log_level="warning")

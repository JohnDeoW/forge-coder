"""简易 Web 面板：复用 CoderSession 与 Agent 事件流，通过 SSE 推送到浏览器。

供阶段检查使用：对话输入、步骤时间线、diff/修改记录查看、接受/拒绝任务、还原测试项目。
"""

import asyncio
import json
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel

from ..cli import tool_result_text
from ..config import load_settings
from ..session import CoderSession

INDEX_HTML = Path(__file__).with_name("index.html")


class ChatRequest(BaseModel):
    text: str
    mode: str = "react"  # react 或 plan


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False, default=str)}\n\n"


def _event_payload(event) -> dict:
    data = dict(event.data)
    if "result" in data and event.type.value == "tool_call_finish":
        data["result"] = tool_result_text(data["result"])
    data.pop("arguments", None)  # 已有解析后的 args
    return {"type": event.type.value, "agent": event.agent_name, "data": data}


def create_app(repo: str) -> FastAPI:
    app = FastAPI(title="ForgeCoder")
    session = CoderSession(load_settings(), repo)
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

    @app.post("/api/chat")
    async def chat(req: ChatRequest):
        if lock.locked():
            raise HTTPException(409, "上一轮还在运行")

        async def events():
            async with lock:
                try:
                    async for event in session.stream(req.text, req.mode):
                        yield _sse(_event_payload(event))
                except Exception as exc:
                    yield _sse({"type": "error", "data": {"error": str(exc)}})
                yield _sse({"type": "turn_end", "data": {"commit": session.last_commit, "status": session.status()}})

        return StreamingResponse(events(), media_type="text/event-stream")

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


def serve(repo: str, port: int) -> None:
    import uvicorn

    print(f"ForgeCoder 面板: http://127.0.0.1:{port}")
    uvicorn.run(create_app(repo), host="127.0.0.1", port=port, log_level="warning")

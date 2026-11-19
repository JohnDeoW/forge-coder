"""把对话历史转换为与 SSE 相同格式的事件，供面板在恢复会话、刷新页面或查看归档会话时重建时间线。

历史中只有主 Agent 的消息（子 Agent 只留下 Task 的结论），因此恢复后不显示子 Agent 的逐步轨迹。
会话中的合并 / 丢弃记录按发生时的历史位置插入为提示事件。
"""

import json

from ...session import DISCARD_NOTE
from ..format import MAIN_AGENT, tool_result_text


def _event(kind: str, **data) -> dict:
    return {"type": kind, "agent": MAIN_AGENT, "data": data, "restored": True}


def history_events(messages, markers=()) -> list[dict]:
    """messages：框架 Message 列表；markers：会话的合并 / 丢弃记录（含 history_len）。"""
    events, names = [], {}
    pending = sorted(markers, key=lambda m: m["history_len"])

    def flush(position: int):
        while pending and pending[0]["history_len"] <= position:
            m = pending.pop(0)
            events.append(_event(f"{m['kind']}_notice", commit=m["commit"], time=m["time"]))

    for index, message in enumerate(messages):
        flush(index)
        if message.role == "summary":
            events.append(_event("compaction_notice", summary=message.content))
            continue
        native = (message.metadata or {}).get("model_message") or {"role": message.role, "content": message.content}
        role, content = native.get("role"), native.get("content") or ""
        if role == "user":  # 丢弃改动后附加的系统提示只给模型看，界面显示用户原始需求
            events.append(_event("user", text=content.removeprefix(DISCARD_NOTE).lstrip()))
        elif role == "assistant":
            calls = native.get("tool_calls") or []
            if content and calls:  # 调用工具前的文字是中间思考，前端会收进可折叠的思考条目
                events.append(_event("llm_chunk", chunk=content))
            for call in calls:
                fn = call.get("function") or {}
                try:
                    args = json.loads(fn.get("arguments") or "{}")
                except ValueError:
                    args = {}
                names[call.get("id")] = fn.get("name")
                if fn.get("name") == "Finish":
                    events.append(_event("agent_finish", result=args.get("answer", ""), status="completed"))
                else:
                    events.append(_event("tool_call_start", tool_name=fn.get("name"), tool_call_id=call.get("id"), args=args))
            if content and not calls:
                events.append(_event("agent_finish", result=content, status="completed"))
        elif role == "tool":
            name = names.get(native.get("tool_call_id"))
            if name not in ("Finish", "Thought"):
                events.append(_event("tool_call_finish", tool_name=name, tool_call_id=native.get("tool_call_id"),
                                     result=tool_result_text(content)))
    flush(len(messages))
    return events


def session_events(session) -> list[dict]:
    """当前会话的事件；没有进行中的任务时为空。"""
    if session.agent is None:
        return []
    return history_events(session.agent.history_manager.get_history(), session.markers)

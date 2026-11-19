"""把当前任务的对话历史转换为与 SSE 相同格式的事件，供面板在恢复会话或刷新页面后重建时间线。

历史中只有主 Agent 的消息（子 Agent 只留下 Task 的结论），因此恢复后不显示子 Agent 的逐步轨迹。
"""

import json

from ..format import MAIN_AGENT, tool_result_text


def _event(kind: str, **data) -> dict:
    return {"type": kind, "agent": MAIN_AGENT, "data": data, "restored": True}


def history_events(session) -> list[dict]:
    if session.agent is None:
        return []
    events, names = [], {}
    for message in session.agent.history_manager.get_history():
        if message.role == "summary":
            events.append(_event("compaction_notice", summary=message.content))
            continue
        native = (message.metadata or {}).get("model_message") or {"role": message.role, "content": message.content}
        role, content = native.get("role"), native.get("content") or ""
        if role == "user":
            events.append(_event("user", text=content))
        elif role == "assistant":
            calls = native.get("tool_calls") or []
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
    return events

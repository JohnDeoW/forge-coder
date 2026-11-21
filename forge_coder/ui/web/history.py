"""把对话历史转换为与 SSE 相同格式的事件，供面板在恢复会话、刷新页面或查看归档会话时重建时间线。

- 保存了运行记录的轮次（round_details[i]["events"]）直接重放记录：含子 Agent 的逐步记录、时间戳、审批与失败原因；
  主 Agent 历史中该轮的消息被记录替换；PlanSolve 轮次不在主 Agent 历史中，按发生时的位置插入；
- 被历史压缩的轮次（对话位置已失效）在压缩摘要之前按顺序重放运行记录；
- 早期没有运行记录的轮次仍由消息重建（子 Agent 只留下 Task 的结论）；
- 会话中的合并 / 丢弃记录按发生时的历史位置插入为提示事件。
"""

import json

from ...session import DISCARD_NOTE
from ..format import MAIN_AGENT, tool_result_text


def _event(kind: str, **data) -> dict:
    return {"type": kind, "agent": MAIN_AGENT, "data": data, "restored": True}


def _replay(detail: dict) -> list[dict]:
    return [{**e, "restored": True} for e in detail["events"]]


def history_events(messages, markers=(), rounds=()) -> list[dict]:
    """messages：框架 Message 列表；markers：会话的合并 / 丢弃记录（含 history_len）；rounds：round_details。"""
    events, names = [], {}
    saved = [d for d in rounds if d.get("events") and (d.get("start") or {}).get("history_len") is not None]
    attached = {d["start"]["history_len"]: d for d in saved if d["start"].get("in_history")}
    # 按位置插入的条目：合并 / 丢弃记录与不在主 Agent 历史中的轮次；同一位置按发生顺序（轮数）排列
    pending = [(m["history_len"], m.get("round", 10**6) + 0.5, "marker", m) for m in markers]
    pending += [(d["start"]["history_len"], d["round"], "round", d) for d in saved if not d["start"].get("in_history")]
    pending.sort(key=lambda x: x[:2])
    replaying = False  # 正在跳过已由运行记录替换的消息
    # 已被历史压缩的轮次（位置记为 None）都在压缩摘要之前：先按顺序重放它们的运行记录
    for d in rounds:
        if d.get("events") and d.get("start") and d["start"].get("history_len") is None:
            events.extend(_replay(d))

    def flush(position: int):
        while pending and pending[0][0] <= position:
            _, _, kind, item = pending.pop(0)
            if kind == "marker":
                events.append(_event(f"{item['kind']}_notice", commit=item["commit"], time=item["time"]))
            else:
                events.extend(_replay(item))

    for index, message in enumerate(messages):
        flush(index)
        if message.role == "summary":
            replaying = False
            events.append(_event("compaction_notice", summary=message.content))
            continue
        native = (message.metadata or {}).get("model_message") or {"role": message.role, "content": message.content}
        role, content = native.get("role"), native.get("content") or ""
        if role == "user":
            replaying = index in attached
            if replaying:
                events.extend(_replay(attached[index]))
                continue
        if replaying:
            continue
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
    return history_events(session.agent.history_manager.get_history(), session.markers, session.round_details)

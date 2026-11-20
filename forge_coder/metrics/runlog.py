"""逐轮运行记录：把一轮中主 Agent、子 Agent 与审批的事件保存为与面板 SSE 相同格式的精简记录。

- 记录随会话保存（round_details[i]["events"]），恢复会话、重启面板或查看归档会话时按原样重放，
  因此子 Agent 的逐步记录、各步骤的开始 / 结束时间与审批等待都能在时间线和"运行"标签中查看；
- 连续的流式文本块合并为一条，过长的工具参数与结果截断，避免会话文件过大；
- 本轮结束时给出结果分类（完成 / 已停止 / 失败原因），面板据此显示失败原因与重试入口。
"""

from forge_agents.core.budget import BudgetExceeded

from ..ui.format import tool_result_text

SAVED_TEXT_CHARS = 2000  # 保存时每个工具参数 / 结果保留的最大长度
SKIPPED = {"agent_start", "step_finish", "step_start", "thinking"}  # 时间线不使用的事件（PlanSolve 的计划步骤除外）


def event_payload(event) -> dict:
    """框架 StreamEvent → 面板事件（SSE 与运行记录共用）。"""
    data = dict(event.data)
    if "result" in data and event.type.value == "tool_call_finish":
        data["result"] = tool_result_text(data["result"])
    data.pop("arguments", None)  # 已有解析后的 args
    return {"type": event.type.value, "agent": event.agent_name, "ts": event.timestamp, "data": data}


def _clip(value):
    if isinstance(value, str) and len(value) > SAVED_TEXT_CHARS:
        return value[:SAVED_TEXT_CHARS] + f"…（已截断，共 {len(value)} 字）"
    if isinstance(value, dict):
        return {k: _clip(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_clip(v) for v in value]
    return value


class RunLog:
    """一轮的事件记录。observe() 在事件流经会话时调用；finish() 得到保存用的事件列表。"""

    def __init__(self, request: str, round_no: int, ts: float):
        self.events: list[dict] = [{"type": "user", "agent": "forge-coder", "ts": ts, "data": {"text": request, "round": round_no}}]

    def observe(self, event) -> None:
        payload = event_payload(event)
        kind, data = payload["type"], payload["data"]
        if kind in SKIPPED and not (kind == "step_finish" and data.get("phase") == "planning") \
                and not (kind == "step_start" and data.get("phase") == "execution"):
            return
        last = self.events[-1]
        if kind == "llm_chunk":
            if last["type"] == "llm_chunk" and last["agent"] == payload["agent"]:
                last["data"]["chunk"] += data.get("chunk", "")
                return
            payload["data"] = {"chunk": data.get("chunk", "")}
        elif kind == "agent_finish":  # 只保留时间线需要的字段
            payload["data"] = {k: data.get(k) for k in ("result", "status", "total_steps", "total_tokens")}
        else:  # 框架内部字段与 tool_name / tool_call_id 重复
            payload["data"] = {k: v for k, v in data.items() if k not in ("kind", "name", "id")}
        self.events.append(_clip(payload))

    def answer_approval(self, request_id: str, answer: str, ts: float) -> None:
        """面板审批的回复不是 Agent 事件，写回对应的审批请求，恢复后显示为已处理并保留等待时长。"""
        for e in self.events:
            if e["type"] == "approval_required" and e["data"].get("request_id") == request_id:
                e["data"].update(answer=answer, answered_ts=ts)

    def finish(self, footer: dict, ts: float) -> list[dict]:
        self.events.append({"type": "turn_end", "agent": "forge-coder", "ts": ts, "data": footer})
        return self.events


def classify_error(exc: BaseException) -> dict:
    """本轮失败的原因分类：预算超限 / 模型超时 / 模型调用失败 / 程序异常。"""
    name = type(exc).__name__
    text = str(exc)
    if isinstance(exc, BudgetExceeded):
        kind, label = "budget", "预算超限：本轮 token 达到 FORGE_ROUND_TOKEN_LIMIT 上限"
    elif "Timeout" in name or "timed out" in text.lower() or "超时" in text:
        kind, label = "timeout", "模型超时：模型服务长时间没有响应"
    elif any(word in name for word in ("API", "Connection", "RateLimit", "Status", "Authentication")):
        kind, label = "model", "模型调用失败：模型服务返回错误或连接中断"
    else:
        kind, label = "program", "程序异常：工具或程序执行出错"
    return {"status": "failed", "kind": kind, "reason": label, "detail": f"{name}: {text}"[:1000]}

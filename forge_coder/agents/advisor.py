"""程序级辅助 Agent：auto 模式下审批需要确认的操作。

它不参与编码，只做一件事：总结待确认操作要做什么、评估风险，并给出批准 / 拒绝的决定。
一次无工具的模型调用（模型取自 LLM_SUBAGENT_MODEL_ID 的范围，可在设置中单独选择），输出 JSON；提示词见 prompts.py。
无法得到有效判断时一律拒绝（失败即关闭）。
"""

from dataclasses import dataclass
import json
import re

from .prompts import APPROVAL_ADVISOR_PROMPT, APPROVAL_REQUEST

@dataclass
class Review:
    approved: bool
    risk: str
    summary: str
    reason: str
    tokens: int = 0
    exchange: dict | None = None  # 与辅助 Agent 的完整对话（发送的消息与原始回复），写入审批记录

    def to_dict(self) -> dict:
        return {"approved": self.approved, "risk": self.risk, "summary": self.summary, "reason": self.reason}


def parse_review(text: str) -> Review:
    match = re.search(r"\{.*\}", text or "", re.S)
    if not match:
        raise ValueError("审批助手没有返回 JSON")
    data = json.loads(match.group(0))
    decision = str(data.get("decision", "")).lower()
    if decision not in ("approve", "deny"):
        raise ValueError(f"无效的审批决定: {decision!r}")
    return Review(
        approved=decision == "approve",
        risk=str(data.get("risk") or "unknown"),
        summary=str(data.get("summary") or ""),
        reason=str(data.get("reason") or ""),
    )


class ApprovalAdvisor:
    def __init__(self, llm):
        self.llm = llm

    async def review(self, tool_name: str, operation: str, reason: str, request: str) -> Review:
        user = APPROVAL_REQUEST.format(request=request or "（未知）", tool_name=tool_name, operation=operation, reason=reason)
        messages = [{"role": "system", "content": APPROVAL_ADVISOR_PROMPT}, {"role": "user", "content": user}]
        raw = None
        try:
            response = await self.llm.ainvoke(messages, temperature=0)
            raw = response.content
            review = parse_review(raw)
            review.tokens = (response.usage or {}).get("total_tokens", 0)
        except Exception as exc:  # 模型调用失败或输出无法解析：拒绝
            review = Review(False, "unknown", operation, f"审批助手无法给出有效判断（{type(exc).__name__}: {exc}），已拒绝")
        review.exchange = {"messages": messages, "response": raw}
        return review

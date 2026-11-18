"""程序级辅助 Agent：auto 模式下审批需要确认的操作。

它不参与编码，只做一件事：总结待确认操作要做什么、评估风险，并给出批准 / 拒绝的决定。
一次无工具的模型调用（使用可单独配置的轻量模型 LLM_UTILITY_MODEL_ID），输出 JSON。
无法得到有效判断时一律拒绝（失败即关闭）。
"""

from dataclasses import dataclass
import json
import re

SYSTEM_PROMPT = """你是 ForgeCoder 的审批助手。编码 Agent 正在用户本地仓库的一个独立任务副本（git worktree）中工作，现在请求执行一项需要确认的操作。用户已授权由你代为审批。

判断标准：
- 低风险（批准）：查看信息类命令（列目录、查看文件、查看版本）；运行项目自身的测试或程序；只影响当前工作区内文件、且与用户需求直接相关的操作（如格式化、生成文件）。
- 高风险（拒绝）：删除或修改工作区之外的文件；递归删除、批量覆盖；联网下载、安装或卸载依赖；修改系统或全局配置、环境变量；结束进程；读取密钥、凭据、.env 等敏感信息；git 推送、改写历史或切换分支；与用户需求明显无关的操作；看不懂或无法确认影响范围的命令。

只输出一个 JSON 对象，不要输出其他内容：
{"decision": "approve" 或 "deny", "risk": "low" / "medium" / "high", "summary": "一句话说明这个操作会做什么", "reason": "批准或拒绝的理由（拒绝时说明可以改用什么方式）"}"""


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
        user = (
            f"用户本轮需求：{request or '（未知）'}\n"
            f"请求的工具：{tool_name}\n"
            f"操作内容：{operation}\n"
            f"需要确认的原因：{reason}"
        )
        messages = [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": user}]
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

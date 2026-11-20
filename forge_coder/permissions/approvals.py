"""审批管理：作为框架 ToolRegistry 的 approval_handler，处理策略返回"需要确认"的操作。

按工作模式选择审批方式：
- auto：程序级辅助 Agent（agents/advisor.py）评估风险后自动批准或拒绝；
- manual：由宿主注入的 approver 决定——函数（CLI 终端询问）、"web"（面板确认卡片）、None（无人值守，一律拒绝）。
每次审批都写入审批记录（内存 + data/approvals/<项目>/<任务ID>.jsonl）。

审批函数可能在主事件循环中执行（主 Agent），也可能在子 Agent 工作线程自己的事件循环中执行；
面板的回复来自 Web 服务器的请求处理。因此等待面板回复使用线程安全的 concurrent.futures.Future。
"""

import asyncio
import concurrent.futures
from datetime import datetime
import json
from pathlib import Path
from typing import Callable
import uuid

from forge_agents.core.streaming import StreamEvent, StreamEventType
from forge_agents.tools import PermissionDecision

from ..agents.modes import AUTO, MANUAL
from .policy import Approver, program_of

WEB_APPROVAL = "web"
APPROVAL_TIMEOUT_S = 300  # 面板审批等待上限，超时视为拒绝


class ApprovalManager:
    def __init__(self, approver: Approver | str | None, advisor, usage):
        self.approver = approver
        self.advisor = advisor  # auto 模式使用的辅助 Agent
        self.usage = usage  # 辅助 Agent 的用量计入本轮来源分布
        self.always_allowed: set[str] = set()  # 用户选择"本会话总是允许"的程序；策略持有同一个集合
        self.pending: dict[str, concurrent.futures.Future] = {}
        self.records: list[dict] = []
        # 以下由会话在运行时设置
        self.mode: str = MANUAL
        self.sink: Callable | None = None  # 本轮事件队列的写入函数
        self.request: str = ""  # 本轮用户需求，供辅助 Agent 参考
        self.log_file: Path | None = None  # 当前任务的审批记录文件
        self.on_answer: Callable[[str, str], None] | None = None  # 面板审批得到回复时通知会话（写入本轮运行记录）

    def start_task(self, log_file: Path | None) -> None:
        self.records = []
        self.log_file = log_file

    def load_records(self) -> None:
        """恢复会话时读回该任务已有的审批记录。"""
        if self.log_file is not None and self.log_file.is_file():
            lines = self.log_file.read_text(encoding="utf-8").splitlines()
            self.records = [json.loads(line) for line in lines if line.strip()]

    async def __call__(self, name: str, arguments: dict, reason: str):
        """框架的审批函数（ToolRegistry.approval_handler）。"""
        operation = str(arguments.get("command") or "") if name == "RunCommand" else json.dumps(arguments, ensure_ascii=False)
        if self.mode == AUTO:
            return await self._auto(name, operation, reason)
        if callable(self.approver):
            # 终端输入会阻塞，放到线程中执行，不占用事件循环
            answer = await asyncio.to_thread(self.approver, operation)
        elif self.approver == WEB_APPROVAL:
            answer = await self._wait_web(name, operation, reason)
        else:
            answer = "deny"
        if answer == "always" and program_of(operation):
            self.always_allowed.add(program_of(operation))  # 策略持有同一个集合，下次直接放行
        approved = answer in ("once", "always")
        self._log({"mode": self.mode, "by": "user" if self.approver else "system",
                   "tool": name, "operation": operation, "reason": reason, "answer": answer, "approved": approved})
        return approved

    async def _auto(self, name: str, operation: str, reason: str) -> PermissionDecision:
        """auto 模式：由辅助 Agent 总结内容、评估风险并决定；完整对话与决定写入审批记录。"""
        review = await self.advisor.review(name, operation, reason, self.request)
        self.usage.add_external("utility", review.tokens)
        self._log({"mode": AUTO, "by": "utility", "tool": name, "operation": operation, "reason": reason,
                   "approved": review.approved, "risk": review.risk, "summary": review.summary,
                   "decision_reason": review.reason, "model": self.advisor.llm.model, "exchange": review.exchange})
        if self.sink:
            self.sink(StreamEvent.create(
                StreamEventType.APPROVAL_RESOLVED, "utility", tool_name=name, command=operation,
                approved=review.approved, risk=review.risk, summary=review.summary, reason=review.reason,
            ))
        if review.approved:
            return PermissionDecision(True)
        return PermissionDecision(False, f"审批助手拒绝了该操作（风险 {review.risk}）：{review.reason}")

    async def _wait_web(self, name: str, command: str, reason: str) -> str:
        """发出审批事件并等待面板回复（resolve）。"""
        if self.sink is None:
            return "deny"
        request_id = uuid.uuid4().hex[:8]
        future: concurrent.futures.Future = concurrent.futures.Future()
        self.pending[request_id] = future
        self.sink(StreamEvent.create(
            StreamEventType.APPROVAL_REQUIRED, "system",
            request_id=request_id, tool_name=name, command=command, reason=reason,
        ))
        try:
            answer = await asyncio.wait_for(asyncio.wrap_future(future), APPROVAL_TIMEOUT_S)
        except TimeoutError:
            answer = "deny"
        finally:
            self.pending.pop(request_id, None)
        if self.on_answer:
            self.on_answer(request_id, answer)
        return answer

    def cancel_pending(self) -> None:
        """用户停止本轮：等待中的面板审批一律视为拒绝。"""
        for future in list(self.pending.values()):
            if not future.done():
                future.set_result("deny")

    def resolve(self, request_id: str, answer: str) -> bool:
        """面板回复审批（once / always / deny）；请求不存在或已结束时返回 False。"""
        future = self.pending.get(request_id)
        if future is None or future.done():
            return False
        future.set_result(answer if answer in ("once", "always") else "deny")
        return True

    def _log(self, record: dict) -> None:
        record = {"time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "request": self.request, **record}
        self.records.append(record)
        if self.log_file is not None:
            self.log_file.parent.mkdir(parents=True, exist_ok=True)
            with self.log_file.open("a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")

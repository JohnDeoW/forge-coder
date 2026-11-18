"""用量统计：每轮对话的总 token 与按来源（主 Agent / 各子 Agent）的分布。

- 总量：每轮给共享的 LLM 绑定一个新的框架 RunBudget（core/budget.py）。主 Agent、PlanSolve 的
  规划器与执行器、子 Agent 使用同一个 LLM 实例，每次模型调用都由 @budgeted 装饰器记录 usage；
- 分布：每个 Agent 结束时的 AGENT_FINISH 事件携带它自己累计的 total_tokens 与步数，
  子 Agent 的事件也会转发到本轮事件流，按 agent_name 汇总即可。
"""

from dataclasses import dataclass, field

from forge_agents.core.budget import RunBudget
from forge_agents.core.streaming import StreamEventType


@dataclass
class AgentUsage:
    tokens: int = 0
    model_calls: int = 0
    runs: int = 0  # 该来源在本轮运行的次数（同一子 Agent 可能被派出多次）

    def add(self, other: "AgentUsage") -> None:
        self.tokens += other.tokens
        self.model_calls += other.model_calls
        self.runs += other.runs


@dataclass
class Usage:
    total_tokens: int = 0
    model_calls: int = 0
    elapsed_s: float = 0.0
    by_agent: dict[str, AgentUsage] = field(default_factory=dict)

    def add(self, other: "Usage") -> None:
        self.total_tokens += other.total_tokens
        self.model_calls += other.model_calls
        self.elapsed_s += other.elapsed_s
        for name, item in other.by_agent.items():
            self.by_agent.setdefault(name, AgentUsage()).add(item)

    def shares(self) -> dict[str, float]:
        """各来源 token 占比（以各来源之和为分母）。"""
        total = sum(a.tokens for a in self.by_agent.values()) or 1
        return {name: a.tokens / total for name, a in self.by_agent.items()}

    def to_dict(self) -> dict:
        return {
            "total_tokens": self.total_tokens,
            "model_calls": self.model_calls,
            "elapsed_s": round(self.elapsed_s, 1),
            "by_agent": {n: vars(a) for n, a in self.by_agent.items()},
            "shares": {n: round(s, 3) for n, s in self.shares().items()},
        }

    def summary(self) -> str:
        parts = " · ".join(f"{n} {s:.0%}" for n, s in sorted(self.shares().items(), key=lambda x: -x[1]))
        return f"{self.total_tokens} tokens · {self.model_calls} 次模型调用 · {self.elapsed_s:.0f}s（{parts}）"


class UsageTracker:
    """会话级用量统计：start → observe(每个事件) → finish。"""

    def __init__(self):
        self.session = Usage()
        self.last: Usage | None = None
        self._budget: RunBudget | None = None
        self._turn: Usage | None = None

    def start(self, llms) -> None:
        """llms：本会话用到的全部模型客户端（各角色模型不同时有多个），共享同一个 RunBudget。"""
        self._budget = RunBudget()  # 不设上限，仅用于统计
        for llm in llms if isinstance(llms, (list, tuple)) else [llms]:
            llm.budget = self._budget
        self._turn = Usage()

    def observe(self, event) -> None:
        if event.type == StreamEventType.AGENT_FINISH and self._turn is not None:
            item = self._turn.by_agent.setdefault(event.agent_name, AgentUsage())
            item.tokens += event.data.get("total_tokens") or 0
            item.model_calls += event.data.get("total_steps") or 0
            item.runs += 1

    def finish(self) -> Usage:
        snap = self._budget.snapshot()
        turn = self._turn
        turn.total_tokens = snap["reported_tokens"]
        turn.model_calls = snap["model_calls"]
        turn.elapsed_s = snap["elapsed_seconds"]
        self.session.add(turn)
        self.last, self._turn = turn, None
        return turn

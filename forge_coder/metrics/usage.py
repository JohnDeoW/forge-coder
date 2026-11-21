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
    models: dict[str, str] = field(default_factory=dict)  # 来源（agent_name）-> 本轮实际使用的模型

    def add(self, other: "Usage") -> None:
        self.total_tokens += other.total_tokens
        self.model_calls += other.model_calls
        self.elapsed_s += other.elapsed_s
        for name, item in other.by_agent.items():
            self.by_agent.setdefault(name, AgentUsage()).add(item)
        # 会话累计只记录用到过的来源最近一次使用的模型
        self.models.update({n: m for n, m in other.models.items() if n in other.by_agent})

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
            "models": {n: m for n, m in self.models.items() if n in self.by_agent},
        }

    @classmethod
    def from_dict(cls, data: dict | None) -> "Usage":
        """恢复会话时读回保存的会话累计用量。"""
        data = data or {}
        return cls(
            total_tokens=data.get("total_tokens", 0), model_calls=data.get("model_calls", 0),
            elapsed_s=data.get("elapsed_s", 0.0),
            by_agent={n: AgentUsage(**a) for n, a in (data.get("by_agent") or {}).items()},
            models=dict(data.get("models") or {}),
        )

    def summary(self) -> str:
        name = lambda n: f"{n}（{self.models[n]}）" if n in self.models else n
        parts = " · ".join(f"{name(n)} {s:.0%}" for n, s in sorted(self.shares().items(), key=lambda x: -x[1]))
        return f"{self.total_tokens} tokens · {self.model_calls} 次模型调用 · {self.elapsed_s:.0f}s（{parts}）"


class UsageTracker:
    """会话级用量统计：start → observe(每个事件) → finish。"""

    def __init__(self, token_limit: int = 0):
        self.token_limit = token_limit or None  # 单轮 token 预算；None 表示只统计不限制
        self.session = Usage()
        self.last: Usage | None = None
        self._budget: RunBudget | None = None
        self._turn: Usage | None = None

    def start(self, llms, models: dict[str, str] | None = None) -> None:
        """llms：本会话用到的全部模型客户端（各角色模型不同时有多个），共享同一个 RunBudget；
        models：各来源本轮使用的模型，随用量一起记录。"""
        self._budget = RunBudget(max_tokens=self.token_limit)
        for llm in llms if isinstance(llms, (list, tuple)) else [llms]:
            llm.budget = self._budget
        self._turn = Usage(models=dict(models or {}))

    def observe(self, event) -> None:
        if event.type == StreamEventType.AGENT_FINISH and self._turn is not None:
            item = self._turn.by_agent.setdefault(event.agent_name, AgentUsage())
            item.tokens += event.data.get("total_tokens") or 0
            item.model_calls += event.data.get("total_steps") or 0
            item.runs += 1

    def add_external(self, name: str, tokens: int) -> None:
        """不经过 Agent 循环的模型调用（如辅助 Agent 的单次审批）也计入来源分布；总量已由 RunBudget 记录。"""
        if self._turn is not None:
            item = self._turn.by_agent.setdefault(name, AgentUsage())
            item.tokens += tokens or 0
            item.model_calls += 1
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

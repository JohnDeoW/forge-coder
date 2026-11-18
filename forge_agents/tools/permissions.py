"""由宿主控制的执行策略，不等同于操作系统沙箱或模型指令。

策略返回三种结果：允许、拒绝、需要人工确认（PermissionDecision.ask）。
"需要确认"由 ToolRegistry 交给宿主注入的审批处理函数（approval_handler）裁决：
策略本身保持同步、无副作用，等待用户的过程由审批函数完成，可以是异步的（如等待网页上的点击）。
"""

from dataclasses import dataclass
from typing import Any, Awaitable, Protocol, Union


@dataclass(frozen=True)
class PermissionDecision:
    allowed: bool
    reason: str = ""
    requires_approval: bool = False  # True 表示需要宿主审批后才能执行

    def __post_init__(self):
        if type(self.allowed) is not bool or not isinstance(self.reason, str):
            raise TypeError("PermissionDecision requires bool allowed and str reason")
        if type(self.requires_approval) is not bool:
            raise TypeError("requires_approval must be bool")
        if self.allowed and self.requires_approval:
            raise ValueError("an allowed decision cannot also require approval")

    @classmethod
    def ask(cls, reason: str = "") -> "PermissionDecision":
        """需要人工确认：未获批准前视为拒绝。"""
        return cls(False, reason, requires_approval=True)


class ToolPolicy(Protocol):
    def __call__(self, name: str, arguments: Any) -> PermissionDecision: ...


class ApprovalHandler(Protocol):
    """宿主审批函数：返回 True 表示批准，或返回带理由的 PermissionDecision。可以是普通函数，也可以是协程函数。

    同步入口（ToolRegistry.execute_tool）只能使用普通函数；异步入口（aexecute_tool）两者都支持。
    """

    def __call__(
        self, name: str, arguments: Any, reason: str
    ) -> Union[bool, "PermissionDecision", Awaitable[Union[bool, "PermissionDecision"]]]: ...


class AllowlistPolicy:
    """拒绝宿主允许列表之外的工具，也适用于后续新增工具。"""

    def __init__(self, names):
        if isinstance(names, str):
            raise TypeError("names must be a collection, not a string")
        self.names = frozenset(names)
        if any(not isinstance(name, str) or not name for name in self.names):
            raise ValueError("names must contain nonempty strings")

    def __call__(self, name, arguments):
        return PermissionDecision(
            name in self.names, "Tool is outside the host allowlist"
        )

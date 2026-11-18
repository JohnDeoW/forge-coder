"""工具执行策略：基于框架的 ToolPolicy 接口（ToolRegistry 在执行工具前同步调用）。

- 所有带 path 参数的工具限制在工作区内，越界直接拒绝；
- RunCommand：白名单命令与本会话"总是允许"的程序直接放行，其余返回"需要确认"，
  由框架交给会话注入的审批函数处理（CLI 在终端询问，面板等待网页上的确认）。
策略本身只做判断、不与用户交互，因此保持同步且无副作用。
"""

from pathlib import Path
import re
import shlex
from typing import Callable, Literal

from forge_agents.tools import PermissionDecision

from .modes import MANUAL, PLAN, PLAN_SUBAGENT_ROLES, PLAN_TOOLS

# 白名单程序：仅在命令不含管道、重定向、命令串联等 shell 语法时自动执行
WHITELIST = {"pytest", "python", "py"}
_SHELL_SYNTAX = re.compile(r"[&|;<>`$\n]")

Approval = Literal["once", "always", "deny"]
Approver = Callable[[str], Approval]  # CLI 等同步界面的审批函数：输入命令，返回用户的选择


def resolve_in_root(root: Path, path: str) -> Path | None:
    """把相对/绝对路径解析为工作区内的绝对路径；越界返回 None。"""
    candidate = Path(path.replace("\\", "/"))
    full = (candidate if candidate.is_absolute() else root / candidate).resolve()
    return full if full.is_relative_to(root) else None


def program_of(command: str) -> str:
    try:
        parts = shlex.split(command, posix=False)
    except ValueError:
        return ""
    return Path(parts[0]).stem.lower() if parts else ""


def is_whitelisted(command: str) -> bool:
    return not _SHELL_SYNTAX.search(command) and program_of(command) in WHITELIST


class WorkspacePolicy:
    def __init__(self, root: Path, always_allowed: set[str] | None = None, mode_getter: Callable[[], str] | None = None):
        self.root = Path(root).resolve()
        # 用户选择"本会话总是允许"的程序；由会话持有并在审批时更新，跨任务保留
        self.always_allowed = always_allowed if always_allowed is not None else set()
        self.mode_getter = mode_getter or (lambda: MANUAL)

    def __call__(self, name, arguments):
        if not isinstance(arguments, dict):
            return PermissionDecision(True)
        if self.mode_getter() == PLAN:
            # plan 模式只读：工具列表已过滤，这里再兜底一次（子 Agent 派生的注册表也会经过这里）
            if name == "Task" and arguments.get("role") not in PLAN_SUBAGENT_ROLES:
                return PermissionDecision(False, "plan 模式只能派出 explorer 子 Agent")
            if name not in PLAN_TOOLS:
                return PermissionDecision(False, f"plan 模式只读，不能使用 {name}；请先输出方案，由用户切换到执行模式")
        path = arguments.get("path")
        if isinstance(path, str) and path and resolve_in_root(self.root, path) is None:
            return PermissionDecision(False, f"路径超出工作区: {path}")
        if name == "RunCommand":
            return self._check_command(str(arguments.get("command") or ""))
        return PermissionDecision(True)

    def _check_command(self, command: str) -> PermissionDecision:
        if is_whitelisted(command):
            return PermissionDecision(True)
        if program_of(command) in self.always_allowed and not _SHELL_SYNTAX.search(command):
            return PermissionDecision(True)
        return PermissionDecision.ask(f"执行白名单外的命令：{command}")

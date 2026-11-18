"""工具执行策略：基于框架的 ToolPolicy 接口（ToolRegistry 在执行工具前同步调用）。

- 所有带 path 参数的工具限制在工作区内；
- RunCommand：白名单命令自动执行，其余命令交给宿主的审批函数（CLI 中询问用户）。
"""

from pathlib import Path
import re
import shlex
from typing import Callable, Literal

from forge_agents.tools import PermissionDecision

# 白名单程序：仅在命令不含管道、重定向、命令串联等 shell 语法时自动执行
WHITELIST = {"pytest", "python", "py"}
_SHELL_SYNTAX = re.compile(r"[&|;<>`$\n]")

Approval = Literal["once", "always", "deny"]
Approver = Callable[[str], Approval]


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
    def __init__(self, root: Path, approver: Approver | None = None, always_allowed: set[str] | None = None):
        self.root = Path(root).resolve()
        self.approver = approver
        # 用户选择"本会话总是允许"的程序；由会话传入，跨任务保留
        self.always_allowed = always_allowed if always_allowed is not None else set()

    def __call__(self, name, arguments):
        if not isinstance(arguments, dict):
            return PermissionDecision(True)
        path = arguments.get("path")
        if isinstance(path, str) and path and resolve_in_root(self.root, path) is None:
            return PermissionDecision(False, f"路径超出工作区: {path}")
        if name == "RunCommand":
            return self._check_command(str(arguments.get("command") or ""))
        return PermissionDecision(True)

    def _check_command(self, command: str) -> PermissionDecision:
        if is_whitelisted(command):
            return PermissionDecision(True)
        program = program_of(command)
        if program in self.always_allowed and not _SHELL_SYNTAX.search(command):
            return PermissionDecision(True)
        if self.approver is None:
            return PermissionDecision(
                False, "该命令不在白名单内且当前界面无法确认，请改用 pytest / python 命令"
            )
        answer = self.approver(command)
        if answer == "always" and program:
            self.always_allowed.add(program)
        if answer in ("once", "always"):
            return PermissionDecision(True)
        return PermissionDecision(False, "用户拒绝执行该命令")

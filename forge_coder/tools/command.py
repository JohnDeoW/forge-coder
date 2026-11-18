"""RunCommand：在工作区（任务 worktree）内执行命令，带超时与输出截断。

是否允许执行由宿主策略（forge_coder.policy）在调用前决定，本工具只负责执行。
"""

import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Any, Dict, List

from forge_agents.tools import Tool, ToolParameter, ToolResponse, ToolErrorCode

DEFAULT_TIMEOUT = 120
MAX_TIMEOUT = 600
HEAD_CHARS, TAIL_CHARS = 2000, 4000  # 截断时保留开头与结尾（测试摘要通常在结尾）


def truncate_output(text: str) -> str:
    if len(text) <= HEAD_CHARS + TAIL_CHARS:
        return text
    omitted = len(text) - HEAD_CHARS - TAIL_CHARS
    return f"{text[:HEAD_CHARS]}\n…（省略 {omitted} 个字符）…\n{text[-TAIL_CHARS:]}"


def _kill_tree(proc: subprocess.Popen) -> None:
    if sys.platform == "win32":  # shell=True 时需连同子进程一起结束
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True)
    else:
        proc.kill()


class RunCommandTool(Tool):
    def __init__(self, root: Path):
        super().__init__(
            name="RunCommand",
            description=(
                "在工作区根目录执行一条命令并返回退出码与输出，例如 `pytest -q` 或 "
                "`python -m inventory.cli`。pytest / python 命令自动执行，其他命令需用户确认。"
            ),
        )
        self.root = Path(root).resolve()

    def get_parameters(self) -> List[ToolParameter]:
        return [
            ToolParameter(name="command", type="string", description="要执行的命令"),
            ToolParameter(
                name="timeout", type="integer", required=False, default=DEFAULT_TIMEOUT,
                description=f"超时秒数，默认 {DEFAULT_TIMEOUT}，最大 {MAX_TIMEOUT}",
            ),
        ]

    def run(self, parameters: Dict[str, Any]) -> ToolResponse:
        command = (parameters.get("command") or "").strip()
        if not command:
            return ToolResponse.error(code=ToolErrorCode.INVALID_PARAM, message="缺少参数 command")
        timeout = min(int(parameters.get("timeout") or DEFAULT_TIMEOUT), MAX_TIMEOUT)
        env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8", PYTHONDONTWRITEBYTECODE="1")

        started = time.time()
        proc = subprocess.Popen(
            command, shell=True, cwd=self.root, env=env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        )
        try:
            raw, _ = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            _kill_tree(proc)
            raw, _ = proc.communicate()
            output = truncate_output(raw.decode("utf-8", errors="replace"))
            return ToolResponse.error(
                code=ToolErrorCode.TIMEOUT,
                message=f"命令超过 {timeout} 秒未结束，已终止。已有输出：\n{output}",
            )
        elapsed = time.time() - started
        output = truncate_output(raw.decode("utf-8", errors="replace").strip())
        last_line = next((l for l in reversed(output.splitlines()) if l.strip()), "")
        text = f"退出码 {proc.returncode} · {elapsed:.1f}s · {last_line}\n\n{output}"
        data = {"exit_code": proc.returncode, "output": output, "elapsed_s": round(elapsed, 2)}
        # 非零退出码（如测试失败）是正常的执行结果而不是工具故障：返回 partial，
        # 不计入熔断器的失败次数，避免"修复—重跑测试"的正常循环触发熔断。
        if proc.returncode == 0:
            return ToolResponse.success(text=text, data=data)
        return ToolResponse.partial(text=text, data=data)

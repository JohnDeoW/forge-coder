"""主 Agent 的上下文组装：仓库地图、子 Agent 已读文件、项目说明文件（FORGE.md）。

框架机制：SimpleAgent 在每次模型调用前调用 context_providers 收集 ContextPacket，再由 ContextBuilder
以"参考资料"消息插入当前任务之前，并在 last_context_diagnostics 中给出各类内容的 token 分布。
"""

import json
import os
from pathlib import Path

from forge_agents.context import ContextBuilder, ContextConfig, ContextPacket, RepoMapProvider
from forge_agents.core.streaming import StreamEventType

from ..config import CONTEXT_WINDOW

PROJECT_NOTES_FILE = "FORGE.md"
MAIN_AGENT = "forge-coder"


def create_context_builder() -> ContextBuilder:
    # 框架默认预算仅 8000 token，会让较长的对话直接超预算报错；按模型上下文窗口设置，预留 15% 给输出
    return ContextBuilder(ContextConfig(max_tokens=CONTEXT_WINDOW, reserve_ratio=0.15))


def read_project_notes(root: Path) -> str:
    """读取项目根目录的 FORGE.md（项目约定、测试命令等），不存在时返回空字符串。"""
    path = Path(root) / PROJECT_NOTES_FILE
    return path.read_text(encoding="utf-8").strip() if path.is_file() else ""


def _mtime_ms(path: Path) -> int | None:
    try:
        return int(os.path.getmtime(path) * 1000)
    except OSError:
        return None


class SeenFilesProvider:
    """记录子 Agent 完整读取过的文件，作为主 Agent 的参考资料，避免主 Agent 重复读取。

    - 观察子 Agent 的 Read 调用（TOOL_CALL_START 取路径、TOOL_CALL_FINISH 取内容与 mtime），只记录完整读取；
    - 每次提供前比对文件当前 mtime，文件被修改过（如主 Agent 已编辑）则丢弃，保证内容是最新的；
    - 同时把 mtime 写入主注册表的读取缓存，主 Agent 不再 Read 也能让 Edit 的乐观锁生效。
    """

    def __init__(self, root: Path, registry, max_chars: int = 40000):
        self.root = Path(root).resolve()
        self.registry = registry
        self.max_chars = max_chars
        self.files: dict[str, tuple[str, int]] = {}  # 相对路径 -> (内容, mtime_ms)
        self._pending: dict[tuple[str, str], str] = {}  # (agent, tool_call_id) -> 路径

    def observe(self, event) -> None:
        """处理子 Agent 的事件（在子 Agent 的工作线程中调用；此时主 Agent 正在等待 Task 结果）。"""
        if event.agent_name == MAIN_AGENT or event.data.get("tool_name") != "Read":
            return
        key = (event.agent_name, event.data.get("tool_call_id") or "")
        if event.type == StreamEventType.TOOL_CALL_START:
            path = (event.data.get("args") or {}).get("path")
            if path:
                self._pending[key] = path.replace("\\", "/")
        elif event.type == StreamEventType.TOOL_CALL_FINISH and key in self._pending:
            self._record(self._pending.pop(key), event.data.get("result"))

    def _record(self, path: str, result) -> None:
        try:
            data = json.loads(result).get("data") or {}
        except (TypeError, ValueError, AttributeError):
            return
        content, mtime = data.get("content"), data.get("file_mtime_ms")
        complete = data.get("lines") == data.get("total_lines") and not data.get("offset")
        if isinstance(content, str) and isinstance(mtime, int) and complete:
            self.files[path] = (content, mtime)
            self.registry.cache_read_metadata(
                path, {"file_mtime_ms": mtime, "file_size_bytes": data.get("file_size_bytes")}
            )

    def get_context(self, query: str):
        blocks, used = [], 0
        for path, (content, mtime) in list(self.files.items()):
            if _mtime_ms(self.root / path) != mtime:
                del self.files[path]  # 文件已变化，内容失效
                continue
            block = f"### {path}\n```\n{content}\n```"
            if used + len(block) > self.max_chars:
                break
            blocks.append(block)
            used += len(block)
        if not blocks:
            return []
        header = "子 Agent 已完整读取的文件（读取后未被修改，内容为最新；可直接据此 Edit，无需再次 Read）："
        return [
            ContextPacket(
                content=header + "\n\n" + "\n\n".join(blocks),
                metadata={"id": "seen-files", "source": "seen_files", "required": True},
            )
        ]


def repo_map_provider(root: Path) -> RepoMapProvider:
    return RepoMapProvider(str(root))


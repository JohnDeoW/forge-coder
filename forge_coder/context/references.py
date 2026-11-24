"""需求中的 @ 文件引用与粘贴的附件。

- @ 引用：需求中的 `@路径`（相对任务 worktree，且文件存在）在本轮作为参考资料注入主 Agent：每次模型调用前
  从磁盘读取最新内容（本轮中被编辑后仍是最新），并把 mtime 写入读取缓存，主 Agent 不必再 Read 即可 Edit；
  本轮结束后清除，之后的轮次需要时重新引用或由 Agent 自行读取。
- 附件：面板把粘贴的长文本（日志、报错）作为附件，以 `<attachment name="...">` 包裹附在需求之后发给模型；
  时间线据此折叠显示，标题、提交说明与 @ 解析时先去掉附件。
"""

import re
from pathlib import Path

from forge_agents.context import ContextPacket

REF_PATTERN = re.compile(r"(?<![\w@/])@([\w\-./\\]*[\w\-])")  # @路径：前面不能是字母数字（排除邮箱）
ATTACHMENT_PATTERN = re.compile(r'\n*<attachment name="([^"]*)">\n(.*?)\n</attachment>', re.S)
MAX_FILE_CHARS = 30000  # 单个引用文件注入的最大字符数，超出截断
MAX_TOTAL_CHARS = 80000


def strip_attachments(text: str) -> str:
    return ATTACHMENT_PATTERN.sub("", text or "").strip()


def attachment(name: str, content: str) -> str:
    return f'\n\n<attachment name="{name}">\n{content.strip()}\n</attachment>'


def find_references(root: Path, text: str) -> list[str]:
    """需求中引用的、位于 root 内且存在的文件（相对路径，去重保持顺序）；附件中的内容不解析。"""
    root = Path(root).resolve()
    found = []
    for raw in REF_PATTERN.findall(strip_attachments(text)):
        rel = raw.replace("\\", "/").rstrip(".")
        path = (root / rel).resolve()
        if path.is_file() and path.is_relative_to(root) and rel not in found:
            found.append(rel)
    return found


class ReferencedFilesProvider:
    """本轮用 @ 引用的文件：作为参考资料注入（source=referenced_files）。"""

    def __init__(self, root: Path, registry):
        self.root = Path(root).resolve()
        self.registry = registry
        self.paths: list[str] = []

    def get_context(self, query: str):
        blocks, used = [], 0
        for rel in self.paths:
            file = self.root / rel
            try:
                content = file.read_text(encoding="utf-8")
                stat = file.stat()
            except (OSError, UnicodeDecodeError):
                blocks.append(f"### {rel}\n（无法以文本读取）")
                continue
            # 与 Read 工具相同：记录 mtime，主 Agent 直接 Edit 时乐观锁仍然生效
            self.registry.cache_read_metadata(rel, {"file_mtime_ms": int(stat.st_mtime * 1000), "file_size_bytes": stat.st_size})
            note = ""
            if len(content) > MAX_FILE_CHARS:
                content, note = content[:MAX_FILE_CHARS], f"\n（文件较长，只包含前 {MAX_FILE_CHARS} 个字符，其余部分请用 Read 读取）"
            if used + len(content) > MAX_TOTAL_CHARS:
                blocks.append(f"### {rel}\n（引用的文件总量过大，本文件未注入，请用 Read 读取）")
                continue
            blocks.append(f"### {rel}\n```\n{content}\n```{note}")
            used += len(content)
        if not blocks:
            return []
        header = "用户在本轮需求中用 @ 引用的文件（每次请求前读取的最新内容，可直接据此修改，无需再次 Read）："
        return [ContextPacket(content=header + "\n\n" + "\n\n".join(blocks),
                              metadata={"id": "referenced-files", "source": "referenced_files", "required": True})]

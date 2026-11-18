"""只读代码搜索工具：Glob、Grep、ListDir，均限制在工作区内。"""

from pathlib import Path
import re
from typing import Any, Dict, List

from forge_agents.tools import Tool, ToolParameter, ToolResponse, ToolErrorCode

from ..policy import resolve_in_root

SKIP_DIRS = {".git", "__pycache__", ".pytest_cache", ".venv", "node_modules", ".backups"}
MAX_RESULTS = 100


def _visible(root: Path, path: Path) -> bool:
    path = path.resolve()
    if not path.is_relative_to(root):  # 如 pattern 含 ../ 时越界
        return False
    return not any(part in SKIP_DIRS for part in path.relative_to(root).parts)


def _rel(root: Path, path: Path) -> str:
    return path.resolve().relative_to(root).as_posix()


class _WorkspaceTool(Tool):
    def __init__(self, root: Path, name: str, description: str):
        super().__init__(name=name, description=description)
        self.root = Path(root).resolve()

    def _base(self, parameters: Dict[str, Any]) -> Path | None:
        return resolve_in_root(self.root, parameters.get("path") or ".")

    def _out_of_root(self, parameters):
        return ToolResponse.error(
            code=ToolErrorCode.ACCESS_DENIED,
            message=f"路径超出工作区: {parameters.get('path')}",
        )


class GlobTool(_WorkspaceTool):
    def __init__(self, root: Path):
        super().__init__(root, "Glob", "按通配符查找文件，如 **/*.py；返回相对工作区的路径列表")

    def get_parameters(self) -> List[ToolParameter]:
        return [
            ToolParameter(name="pattern", type="string", description="通配符，如 **/*.py 或 tests/test_*.py"),
            ToolParameter(name="path", type="string", description="搜索起始目录，默认工作区根目录", required=False),
        ]

    def run(self, parameters: Dict[str, Any]) -> ToolResponse:
        pattern = parameters.get("pattern")
        if not pattern:
            return ToolResponse.error(code=ToolErrorCode.INVALID_PARAM, message="缺少参数 pattern")
        base = self._base(parameters)
        if base is None:
            return self._out_of_root(parameters)
        files = sorted(
            _rel(self.root, p) for p in base.glob(pattern) if p.is_file() and _visible(self.root, p)
        )
        shown = files[:MAX_RESULTS]
        text = "\n".join(shown) if shown else "没有匹配的文件"
        if len(files) > MAX_RESULTS:
            text += f"\n……共 {len(files)} 个，仅显示前 {MAX_RESULTS} 个"
        return ToolResponse.success(text=text, data={"files": shown, "total": len(files)})


class GrepTool(_WorkspaceTool):
    def __init__(self, root: Path):
        super().__init__(root, "Grep", "在文件内容中按正则表达式搜索，返回 路径:行号: 内容")

    def get_parameters(self) -> List[ToolParameter]:
        return [
            ToolParameter(name="pattern", type="string", description="正则表达式"),
            ToolParameter(name="path", type="string", description="搜索目录或文件，默认工作区根目录", required=False),
            ToolParameter(name="glob", type="string", description="只搜索匹配该通配符的文件，如 *.py", required=False),
            ToolParameter(name="ignore_case", type="boolean", description="是否忽略大小写", required=False, default=False),
        ]

    def run(self, parameters: Dict[str, Any]) -> ToolResponse:
        try:
            regex = re.compile(
                parameters.get("pattern") or "",
                re.IGNORECASE if parameters.get("ignore_case") else 0,
            )
        except re.error as exc:
            return ToolResponse.error(code=ToolErrorCode.INVALID_PARAM, message=f"正则表达式无效: {exc}")
        if not parameters.get("pattern"):
            return ToolResponse.error(code=ToolErrorCode.INVALID_PARAM, message="缺少参数 pattern")
        base = self._base(parameters)
        if base is None:
            return self._out_of_root(parameters)

        candidates = [base] if base.is_file() else base.rglob(parameters.get("glob") or "*")
        matches = []
        for file in sorted(candidates):
            if not file.is_file() or not _visible(self.root, file):
                continue
            try:
                lines = file.read_text(encoding="utf-8").splitlines()
            except (UnicodeDecodeError, OSError):
                continue  # 跳过二进制或无法读取的文件
            for no, line in enumerate(lines, 1):
                if regex.search(line):
                    matches.append(f"{_rel(self.root, file)}:{no}: {line.strip()}")
            if len(matches) > MAX_RESULTS:
                break

        shown = matches[:MAX_RESULTS]
        text = "\n".join(shown) if shown else "没有匹配的内容"
        if len(matches) > MAX_RESULTS:
            text += f"\n……结果过多，仅显示前 {MAX_RESULTS} 条，请缩小范围"
        return ToolResponse.success(text=text, data={"matches": shown})


class ListDirTool(_WorkspaceTool):
    def __init__(self, root: Path):
        super().__init__(root, "ListDir", "以树形结构列出目录内容，用于了解项目结构")

    def get_parameters(self) -> List[ToolParameter]:
        return [
            ToolParameter(name="path", type="string", description="目录路径，默认工作区根目录", required=False),
            ToolParameter(name="depth", type="integer", description="展开层数，默认 2", required=False, default=2),
        ]

    def run(self, parameters: Dict[str, Any]) -> ToolResponse:
        base = self._base(parameters)
        if base is None:
            return self._out_of_root(parameters)
        if not base.is_dir():
            return ToolResponse.error(code=ToolErrorCode.NOT_FOUND, message=f"目录不存在: {parameters.get('path')}")
        depth = int(parameters.get("depth") or 2)
        lines = [(_rel(self.root, base) if base != self.root else ".") + "/"]

        def walk(directory: Path, level: int):
            entries = sorted(
                (p for p in directory.iterdir() if p.name not in SKIP_DIRS),
                key=lambda p: (p.is_file(), p.name),
            )
            for entry in entries:
                lines.append("  " * level + entry.name + ("/" if entry.is_dir() else ""))
                if entry.is_dir() and level < depth:
                    walk(entry, level + 1)

        walk(base, 1)
        return ToolResponse.success(text="\n".join(lines), data={"entries": len(lines) - 1})

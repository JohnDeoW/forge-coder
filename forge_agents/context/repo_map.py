"""仓库地图：用 ast 提取 Python 文件中的类、函数签名与行号，生成紧凑的项目结构概览。

作为 ContextProvider 挂到 Agent 上，每次模型调用前重新生成（按文件 mtime/size 缓存解析结果），
让模型不必逐个打开文件就能知道"有哪些模块、符号在哪一行"，减少探索步骤与 token。
非 Python 文件只列出路径。
"""

import ast
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

from .types import ContextPacket

DEFAULT_SKIP_DIRS = frozenset({".git", "__pycache__", ".pytest_cache", ".venv", "venv", "node_modules", ".backups"})
DEFAULT_SKIP_SUFFIXES = frozenset({".pyc", ".pyo", ".so", ".dll", ".exe", ".png", ".jpg", ".gif", ".lock"})


def _signature(node: ast.AST) -> str:
    args = ast.unparse(node.args)
    returns = f" -> {ast.unparse(node.returns)}" if getattr(node, "returns", None) else ""
    prefix = "async " if isinstance(node, ast.AsyncFunctionDef) else ""
    return f"{prefix}{node.name}({args}){returns}"


def _decorators(node: ast.AST) -> str:
    names = [ast.unparse(d) for d in getattr(node, "decorator_list", [])]
    return f"  [@{', @'.join(names)}]" if names else ""


def summarize_python(source: str) -> List[str]:
    """返回该文件的符号行：顶层类/函数，以及类中的方法（带行号）。"""
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return [f"  （语法错误，无法解析：第 {exc.lineno} 行）"]
    lines = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            lines.append(f"  L{node.lineno} def {_signature(node)}{_decorators(node)}")
        elif isinstance(node, ast.ClassDef):
            bases = f"({', '.join(ast.unparse(b) for b in node.bases)})" if node.bases else ""
            lines.append(f"  L{node.lineno} class {node.name}{bases}{_decorators(node)}")
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    lines.append(f"    L{item.lineno} def {_signature(item)}{_decorators(item)}")
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            names = [t.id for t in targets if isinstance(t, ast.Name) and t.id.isupper()]
            if names:  # 只列出模块级常量
                lines.append(f"  L{node.lineno} {', '.join(names)}")
    return lines


class RepoMap:
    def __init__(
        self,
        root: str,
        skip_dirs: Iterable[str] = DEFAULT_SKIP_DIRS,
        max_chars: int = 12000,
    ):
        self.root = Path(root).resolve()
        self.skip_dirs = frozenset(skip_dirs)
        self.max_chars = max_chars
        self._cache: Dict[str, Tuple[Tuple[int, int], List[str]]] = {}

    def _files(self) -> List[Path]:
        files = []
        for path in sorted(self.root.rglob("*")):
            rel = path.relative_to(self.root)
            if any(part in self.skip_dirs for part in rel.parts) or not path.is_file():
                continue
            if path.suffix.lower() in DEFAULT_SKIP_SUFFIXES:
                continue
            files.append(path)
        return files

    def _symbols(self, path: Path, rel: str) -> List[str]:
        stat = path.stat()
        key = (stat.st_mtime_ns, stat.st_size)
        cached = self._cache.get(rel)
        if cached and cached[0] == key:
            return cached[1]
        try:
            lines = summarize_python(path.read_text(encoding="utf-8"))
        except (UnicodeDecodeError, OSError):
            lines = []
        self._cache[rel] = (key, lines)
        return lines

    def render(self) -> str:
        out: List[str] = []
        files = self._files()
        for path in files:
            rel = path.relative_to(self.root).as_posix()
            out.append(rel)
            if path.suffix == ".py":
                out.extend(self._symbols(path, rel))
        text = "\n".join(out)
        if len(text) > self.max_chars:  # 超长时保留开头，并提示用工具查看其余部分
            text = text[: self.max_chars].rsplit("\n", 1)[0] + f"\n…（地图过长已截断，共 {len(files)} 个文件，其余请用 Glob / Grep 查看）"
        return text


class RepoMapProvider:
    """ContextProvider：每次模型调用前提供最新的仓库地图（必需包，不参与相关性筛选）。"""

    def __init__(self, root: str, max_chars: int = 12000, header: Optional[str] = None):
        self.repo_map = RepoMap(root, max_chars=max_chars)
        self.header = header or "仓库地图（文件路径；Python 文件附类、函数签名与行号 L<行号>，随文件变化自动更新）："

    def get_context(self, query: str) -> Iterable[ContextPacket]:
        return [
            ContextPacket(
                content=f"{self.header}\n{self.repo_map.render()}",
                metadata={"id": "repo-map", "source": "repo_map", "required": True},
            )
        ]

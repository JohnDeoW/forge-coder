"""待办事项的存取：保存在 JSON 文件中，每条为 {"id", "title", "done"}。"""

import json
from pathlib import Path


class TodoStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def _load(self) -> list[dict]:
        if not self.path.is_file():
            return []
        return json.loads(self.path.read_text(encoding="utf-8"))

    def _save(self, todos: list[dict]) -> None:
        self.path.write_text(json.dumps(todos, ensure_ascii=False, indent=2), encoding="utf-8")

    def add(self, title: str) -> dict:
        title = title.strip()
        if not title:
            raise ValueError("标题不能为空")
        todos = self._load()
        todo = {"id": max((t["id"] for t in todos), default=0) + 1, "title": title, "done": False}
        todos.append(todo)
        self._save(todos)
        return todo

    def list(self, done: bool | None = None) -> list[dict]:
        """done 为 None 时返回全部；True / False 分别只返回已完成 / 未完成的事项。"""
        todos = self._load()
        return todos if done is None else [t for t in todos if t["done"] == done]

    def complete(self, todo_id: int) -> dict:
        todos = self._load()
        for todo in todos:
            if todo["id"] == todo_id:
                todo["done"] = True
                self._save(todos)
                return todo
        raise ValueError(f"没有编号为 {todo_id} 的待办事项")

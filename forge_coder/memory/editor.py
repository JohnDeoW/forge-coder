"""记忆修改的统一入口：项目记忆与用户偏好的新增、修改、删除都经过这里，记录修改并支持撤销。

- 调用方：memory 子 Agent 的工具（tools/memory_tools.py，来源为主 Agent 派出或归档会话），用户的 CLI 命令与面板操作；
- 修改记录：data/memory/changes.jsonl，每条记下对象（preferences / project:<项目>）、操作、条目修改前后的内容、
  理由、来源（main / archive / user）、所在会话与时间；撤销后标记 undone；
- 用户优先：用户写入或编辑过的条目，memory 子 Agent 只读——项目记忆按记录的作者（source 前缀 user:）判断；
  用户偏好按修改记录判断：最近一次由 memory 子 Agent 写入的内容才属于它，其余（含早期 /remember 写入的）都视为用户的；
- 撤销：按记录反向操作（新增 → 删除、修改 → 改回原内容、删除 → 重新加入）；条目在之后又被改动过时拒绝撤销。
"""

from datetime import datetime
import json
from pathlib import Path
import uuid

from .preferences import PreferenceMemory, clean
from .project import AGENT, NOTE, USER, ProjectMemory

PREFERENCES = "preferences"
TRIGGER_LABELS = {"main": "主 Agent 派出", "archive": "归档会话", "user": "用户"}


def changes_file(data_dir: Path) -> Path:
    return Path(data_dir) / "memory" / "changes.jsonl"


def drop_project_changes(data_dir: Path, repo_name: str) -> None:
    """还原测试项目时删除该项目的修改记录（项目记忆已整份删除）。"""
    file = changes_file(data_dir)
    if file.is_file():
        keep = [line for line in file.read_text(encoding="utf-8").splitlines()
                if line.strip() and json.loads(line)["target"] != f"project:{repo_name}"]
        file.write_text("".join(line + "\n" for line in keep), encoding="utf-8")


class MemoryEditor:
    def __init__(self, data_dir: Path, project: ProjectMemory, preferences: PreferenceMemory):
        self.file = changes_file(data_dir)
        self.project = project
        self.preferences = preferences
        self.project_target = f"project:{project.scope.removeprefix('project:')}"

    # ------------------------------------------------------------ 修改记录
    def _load(self) -> list[dict]:
        if not self.file.is_file():
            return []
        return [json.loads(line) for line in self.file.read_text(encoding="utf-8").splitlines() if line.strip()]

    def _save(self, records: list[dict]) -> None:
        self.file.parent.mkdir(parents=True, exist_ok=True)
        self.file.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records), encoding="utf-8")

    def _log(self, **change) -> dict:
        change = {"id": uuid.uuid4().hex[:8], "time": datetime.now().strftime("%Y-%m-%d %H:%M"), "undone": False, **change}
        self._save(self._load() + [change])
        return change

    def changes(self, task_id: str | None = None) -> list[dict]:
        """本项目与用户偏好的修改记录，新到旧；task_id 给出时只取该会话的。"""
        items = [c for c in self._load() if c["target"] in (PREFERENCES, self.project_target)]
        if task_id:
            items = [c for c in items if c.get("task") == task_id]
        return items[::-1]

    def agent_preferences(self) -> set[str]:
        """当前由 memory 子 Agent 写入的偏好内容（最近一次写入该内容的是 memory 子 Agent 且未撤销）。"""
        owner: dict[str, str] = {}
        for c in self._load():
            if c["target"] == PREFERENCES and not c["undone"] and c.get("after"):
                owner[c["after"]] = c["trigger"]
        current = set(self.preferences.items())
        return {text for text, trigger in owner.items() if trigger != "user" and text in current}

    # ------------------------------------------------------------ 查看（供 memory 子 Agent）
    def listing(self) -> str:
        agent_prefs = self.agent_preferences()
        prefs = self.preferences.items()
        lines = ["## 用户偏好（target=preference，ref 为序号）"]
        lines += [f"{i}. {'' if p in agent_prefs else '［用户编写，只读］'}{p}" for i, p in enumerate(prefs, 1)] or ["（无）"]
        lines.append("\n## 项目记忆（target=project，ref 为 ID）")
        entries = self.project.entries()
        for e in entries:
            lock = "［用户编辑，只读］" if e["by_user"] else ""
            lines.append(f"- [{e['id'][:8]}] {e['label']} · 会话 {e['task']} · {e['time']} {lock}\n  {e['content']}")
        if not entries:
            lines.append("（无）")
        return "\n".join(lines)

    # ------------------------------------------------------------ 新增 / 修改 / 删除
    def _check_agent(self, by_user: bool, locked: bool) -> None:
        if not by_user and locked:
            raise ValueError("该条目由用户写入或编辑过，memory 子 Agent 不能修改或删除；如需调整，请在回答中提醒用户")

    def add(self, target: str, content: str, *, trigger: str, task: str = "", reason: str = "", kind: str = NOTE) -> dict:
        by_user = trigger == "user"
        if target == "preference":
            text = self.preferences.add(content, source=f"{trigger}:add")
            return self._log(target=PREFERENCES, op="add", before=None, after=text, reason=reason, trigger=trigger, task=task)
        record = self.project.add(task, kind, content, author=USER if by_user else AGENT)
        return self._log(target=self.project_target, op="add", record=record.memory_id, kind=kind, before=None,
                         after=record.content, reason=reason, trigger=trigger, task=task)

    def update(self, target: str, ref: str | int, content: str, *, trigger: str, task: str = "", reason: str = "") -> dict:
        by_user = trigger == "user"
        if target == "preference":
            index = int(ref)
            items = self.preferences.items()
            self._check_agent(by_user, 1 <= index <= len(items) and items[index - 1] not in self.agent_preferences())
            before, after = self.preferences.update(index, content, source=f"{trigger}:update")
            return self._log(target=PREFERENCES, op="update", before=before, after=after, reason=reason, trigger=trigger, task=task)
        entry = self._project_entry(ref)
        self._check_agent(by_user, entry["by_user"])
        record = self.project.revise(entry["id"], content, author=USER if by_user else AGENT)
        return self._log(target=self.project_target, op="update", record=record.memory_id, kind=entry["kind"],
                         before=entry["content"], after=record.content, reason=reason, trigger=trigger, task=task,
                         by_user=entry["by_user"])

    def delete(self, target: str, ref: str | int, *, trigger: str, task: str = "", reason: str = "") -> dict:
        by_user = trigger == "user"
        if target == "preference":
            index = int(ref)
            items = self.preferences.items()
            self._check_agent(by_user, 1 <= index <= len(items) and items[index - 1] not in self.agent_preferences())
            before = self.preferences.remove(index, source=f"{trigger}:delete")
            return self._log(target=PREFERENCES, op="delete", before=before, after=None, reason=reason, trigger=trigger, task=task)
        entry = self._project_entry(ref)
        self._check_agent(by_user, entry["by_user"])
        self.project.retract(entry["id"])
        return self._log(target=self.project_target, op="delete", record=entry["id"], kind=entry["kind"],
                         before=entry["content"], after=None, reason=reason, trigger=trigger, task=task,
                         source_task=entry["task"], by_user=entry["by_user"])

    def _project_entry(self, ref) -> dict:
        # 用户按序号操作（CLI / 面板），memory 子 Agent 按 ID 前缀操作
        return self.project.at(ref) if isinstance(ref, int) else self.project.resolve(str(ref))

    # ------------------------------------------------------------ 撤销
    def undo(self, change_id: str) -> dict:
        records = self._load()
        change = next((c for c in records if c["id"] == change_id), None)
        if change is None or change["undone"]:
            raise ValueError("修改记录不存在或已撤销")
        stale = ValueError("该条目在这次修改之后又被改动过，不能撤销")
        if change["target"] == PREFERENCES:
            index = self.preferences.find(change["after"]) if change["after"] else None
            if change["op"] == "add":
                if index is None:
                    raise stale
                self.preferences.remove(index, source="undo")
            elif change["op"] == "update":
                if index is None:
                    raise stale
                self.preferences.update(index, change["before"], source="undo")
            else:
                self.preferences.add(change["before"], source="undo")
        else:
            active = {e["id"]: e for e in self.project.entries()}
            if change["op"] == "add":
                if change["record"] not in active:
                    raise stale
                self.project.retract(change["record"])
            elif change["op"] == "update":
                if change["record"] not in active:
                    raise stale
                self.project.revise(change["record"], change["before"], author=USER if change.get("by_user") else AGENT)
            else:
                author = USER if change.get("by_user") else AGENT
                self.project.add(change.get("source_task") or change.get("task", ""), change["kind"], change["before"], author=author)
        change.update(undone=True, undone_time=datetime.now().strftime("%Y-%m-%d %H:%M"))
        self._save(records)
        return change

    def rollback(self, change_ids: list[str]) -> None:
        """memory 子 Agent 运行失败时撤销它本次做的全部修改（从后往前），保留原有记忆不变。"""
        for change_id in reversed(change_ids):
            try:
                self.undo(change_id)
            except ValueError:
                pass


def summarize_change(change: dict) -> str:
    """一行描述，供 CLI 与归档结果展示。"""
    target = "用户偏好" if change["target"] == PREFERENCES else "项目记忆"
    op = {"add": "新增", "update": "修改", "delete": "删除"}[change["op"]]
    text = clean(change["after"] or change["before"] or "")
    return f"{op}{target}：{text[:60]}{'…' if len(text) > 60 else ''}" + (f"（{change['reason']}）" if change.get("reason") else "")

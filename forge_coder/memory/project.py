"""项目记忆：基于框架 memory.MemoryStore，按项目保存跨会话、跨任务的项目知识。

- 存储：data/memory/projects/<项目>.sqlite；MemoryStore 的作用域 (user_id, task_id) 取 (本地用户, "project:<项目>")，
  即一个项目一个作用域；每条记录的 source 记下来源任务与类型（task:<任务ID>:merged / discarded / compact），
  created_at 为写入时间；
- 写入：任务结束（合并 / 丢弃）时由辅助 Agent 生成的任务摘要；主 Agent 历史压缩时的压缩摘要。Agent 没有写入工具；
- 使用：ProjectMemoryProvider 每次请求按当前需求检索相关记录（框架 MemoryContextProvider），作为参考资料注入主 Agent；
- 管理：/project-memory 查看与删除（框架 retract，保留历史）；还原测试项目时整份删除（代码已回到初始状态）。
"""

from datetime import datetime
from pathlib import Path

from forge_agents.context import ContextPacket, MemoryContextProvider
from forge_agents.memory import MemoryRecord, MemoryStore

from .preferences import USER_ID

MERGED, DISCARDED, COMPACT = "merged", "discarded", "compact"
LABELS = {MERGED: "任务摘要·已合并", DISCARDED: "任务摘要·已丢弃", COMPACT: "压缩摘要·任务进行中"}


def project_memory_file(data_dir: Path, repo_name: str) -> Path:
    return Path(data_dir) / "memory" / "projects" / f"{repo_name}.sqlite"


def parse_source(source: str) -> tuple[str, str]:
    """task:<任务ID>:<类型> → (任务ID, 类型)"""
    _, task_id, kind = (source.split(":") + ["", ""])[:3]
    return task_id, kind


def local_time(iso: str) -> str:
    return datetime.fromisoformat(iso).astimezone().strftime("%Y-%m-%d %H:%M")


def describe(record: MemoryRecord) -> dict:
    task_id, kind = parse_source(record.source)
    return {"id": record.memory_id, "time": local_time(record.created_at), "task": task_id,
            "kind": kind, "label": LABELS.get(kind, kind), "content": record.content}


class ProjectMemory:
    def __init__(self, data_dir: Path, repo_name: str):
        self.file = project_memory_file(data_dir, repo_name)
        self.scope = f"project:{repo_name}"

    @property
    def store(self) -> MemoryStore:
        # 每次按需打开（建表幂等）：还原测试项目会删除数据库文件，之后仍可直接使用
        return MemoryStore(str(self.file), user_id=USER_ID, task_id=self.scope)

    def add(self, task_id: str, kind: str, content: str) -> MemoryRecord:
        # 任务摘要与压缩摘要都是对一段经历的记录，对应框架的 episode 类型
        return self.store.add(content.strip(), source=f"task:{task_id}:{kind}", kind="episode")

    def entries(self) -> list[dict]:
        """全部有效记录，按写入时间从旧到新。"""
        records = self.store.search("", 100)
        return [describe(r) for r in sorted(records, key=lambda r: r.created_at)]

    def delete(self, index: int | None = None) -> int:
        """删除第 index 条（从 1 开始，对应 entries 的顺序）；None 删除全部。返回剩余条数。"""
        entries = self.entries()
        if index is None:
            targets = entries
        elif 1 <= index <= len(entries):
            targets = [entries[index - 1]]
        else:
            raise ValueError(f"没有第 {index} 条项目记忆（共 {len(entries)} 条）")
        for entry in targets:
            self.store.retract(entry["id"])
        return len(entries) - len(targets)

    def retract_task(self, task_id: str, kind: str) -> None:
        for entry in self.entries():
            if entry["task"] == task_id and entry["kind"] == kind:
                self.store.retract(entry["id"])


class ProjectMemoryProvider:
    """ContextProvider：按当前需求检索相关项目记忆（框架 MemoryContextProvider），合并为一个参考资料包。

    当前任务自己的压缩摘要已在对话历史中，不重复注入。
    """

    def __init__(self, memory: ProjectMemory, current_task, limit: int = 5):
        self.memory = memory
        self.current_task = current_task  # 返回当前任务 ID 的函数
        self.limit = limit

    def get_context(self, query: str):
        current = self.current_task()
        lines = []
        inner = MemoryContextProvider(self.memory.store, limit=self.limit * 2, use_query=True)  # 框架：按查询检索
        for packet in inner.get_context(query):
            task_id, kind = parse_source(packet.metadata["source"])
            if task_id == current:
                continue
            when = local_time(packet.metadata["updated_at"])
            lines.append(f"### {LABELS.get(kind, kind)} · 任务 {task_id} · {when}\n{packet.content}")
            if len(lines) >= self.limit:
                break
        if not lines:
            return []
        header = ("项目记忆（以往任务的摘要，按与当前需求的相关性检索；其中的约定与决策应继续遵守，"
                  "已丢弃任务的改动不在代码中；可能过时，与当前代码不一致时以代码为准）：")
        return [ContextPacket(content=header + "\n\n" + "\n\n".join(lines),
                              metadata={"id": "project-memory", "source": "project_memory", "required": True})]

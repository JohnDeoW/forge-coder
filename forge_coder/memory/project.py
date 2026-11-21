"""项目记忆：基于框架 memory.MemoryStore，按项目保存跨会话、跨任务的项目知识。

- 存储：data/memory/projects/<项目>.sqlite；MemoryStore 的作用域 (user_id, task_id) 取 (本地用户, "project:<项目>")，
  即一个项目一个作用域；每条记录的 source 为 <作者>:<任务ID>:<类型>，作者 task（memory 子 Agent）或 user（用户编辑过），
  类型为会话摘要 merged / discarded、项目约定 note，以及早期版本写入的压缩摘要 compact；
- 写入：memory 子 Agent 经 memory/editor.py 写入与整理（归档会话时写会话摘要，会话中由主 Agent 派出同步约定）；
  用户可编辑（框架 revise，保留历史）与删除（框架 retract）；用户编辑过的条目 memory 子 Agent 不能修改；
- 使用：ProjectMemoryProvider 每次请求按当前需求检索相关记录（框架 MemoryContextProvider），作为参考资料注入主 Agent；
- 还原测试项目时整份删除（代码已回到初始状态）。
"""

from datetime import datetime
from pathlib import Path

from forge_agents.context import ContextPacket, MemoryContextProvider
from forge_agents.memory import MemoryRecord, MemoryStore

from .preferences import USER_ID

MERGED, DISCARDED, NOTE, COMPACT = "merged", "discarded", "note", "compact"
LABELS = {MERGED: "会话摘要·已合并", DISCARDED: "会话摘要·未合并", NOTE: "项目约定", COMPACT: "压缩摘要（旧版本写入）"}
AGENT, USER = "task", "user"  # source 中的作者


def project_memory_file(data_dir: Path, repo_name: str) -> Path:
    return Path(data_dir) / "memory" / "projects" / f"{repo_name}.sqlite"


def parse_source(source: str) -> tuple[str, str]:
    """<作者>:<任务ID>:<类型> → (任务ID, 类型)"""
    _, task_id, kind = (source.split(":") + ["", ""])[:3]
    return task_id, kind


def local_time(iso: str) -> str:
    return datetime.fromisoformat(iso).astimezone().strftime("%Y-%m-%d %H:%M")


def describe(record: MemoryRecord) -> dict:
    task_id, kind = parse_source(record.source)
    return {"id": record.memory_id, "time": local_time(record.created_at), "task": task_id,
            "kind": kind, "label": LABELS.get(kind, kind), "content": record.content,
            "by_user": record.source.startswith(USER + ":")}


class ProjectMemory:
    def __init__(self, data_dir: Path, repo_name: str):
        self.file = project_memory_file(data_dir, repo_name)
        self.scope = f"project:{repo_name}"

    @property
    def store(self) -> MemoryStore:
        # 每次按需打开（建表幂等）：还原测试项目会删除数据库文件，之后仍可直接使用
        return MemoryStore(str(self.file), user_id=USER_ID, task_id=self.scope)

    def add(self, task_id: str, kind: str, content: str, author: str = AGENT) -> MemoryRecord:
        # 会话摘要与项目约定都是对以往经历的记录，对应框架的 episode 类型
        return self.store.add(content.strip(), source=f"{author}:{task_id}:{kind}", kind="episode")

    def get(self, memory_id: str) -> dict:
        return describe(self.store.get(memory_id))

    def revise(self, memory_id: str, content: str, author: str) -> MemoryRecord:
        """修改一条记录（框架 revise：旧版本标记为 superseded 并追加新版本），保留任务与类型，记下作者。"""
        old = self.store.get(memory_id)
        task_id, kind = parse_source(old.source)
        return self.store.revise(memory_id, content.strip(), source=f"{author}:{task_id}:{kind}")

    def resolve(self, ref: str) -> dict:
        """按完整 ID 或前缀（memory 子 Agent 看到的短 ID）找到有效记录。"""
        ref = (ref or "").strip()
        matches = [e for e in self.entries() if ref and e["id"].startswith(ref)]
        if len(matches) != 1:
            raise ValueError(f"没有找到唯一的项目记忆 {ref!r}，请先用 MemoryList 查看 ID")
        return matches[0]

    def entries(self) -> list[dict]:
        """全部有效记录，按写入时间从旧到新。"""
        records = self.store.search("", 100)
        return [describe(r) for r in sorted(records, key=lambda r: r.created_at)]

    def at(self, index: int) -> dict:
        """第 index 条（从 1 开始，对应 entries 的顺序；CLI 与面板按序号操作）。"""
        entries = self.entries()
        if not 1 <= index <= len(entries):
            raise ValueError(f"没有第 {index} 条项目记忆（共 {len(entries)} 条）")
        return entries[index - 1]

    def retract(self, memory_id: str) -> None:
        self.store.retract(memory_id)


class ProjectMemoryProvider:
    """ContextProvider：按当前需求检索相关项目记忆（框架 MemoryContextProvider），合并为一个参考资料包。

    当前会话自己的记录（如会话中写入的项目约定）已在对话中，不重复注入。
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
                  "未合并会话的改动不在代码中；可能过时，与当前代码不一致时以代码为准）：")
        return [ContextPacket(content=header + "\n\n" + "\n\n".join(lines),
                              metadata={"id": "project-memory", "source": "project_memory", "required": True})]

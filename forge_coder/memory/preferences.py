"""用户偏好：基于框架 memory.profile.ProfileStore 的结构化画像，跨会话、跨任务生效。

- 存储：data/memory/profile.sqlite（框架负责 Schema 校验、修订版本与审计快照）；
- 写入：用户（/remember、编辑、删除）与 memory 子 Agent（依据用户在对话中表达的偏好），都经 memory/editor.py
  记录修改；条目归属由修改记录判断（见 MemoryEditor），memory 子 Agent 不能修改用户写入或编辑过的条目；
- 使用：PreferencesProvider 作为上下文组件，每次模型调用前把当前偏好作为必需资料注入主 Agent。
"""

from pathlib import Path

from forge_agents.context import ContextPacket
from forge_agents.memory.profile import ProfileModel, ProfileStore

USER_ID = "local-user"  # 本地单用户应用
NAMESPACE = "forge-coder"


class Preferences(ProfileModel):
    items: list[str] = []  # Schema 保持不变（框架按 Schema 指纹校验已保存的画像）


def clean(text: str) -> str:
    return " ".join((text or "").split())


class PreferenceMemory:
    def __init__(self, data_dir: Path):
        self.store = ProfileStore(
            str(Path(data_dir) / "memory" / "profile.sqlite"),
            user_id=USER_ID, namespace=NAMESPACE, schema=Preferences,
        )

    def _snapshot(self):
        snapshot = self.store.get()
        return snapshot, list(snapshot.data.get("items", []) if snapshot else [])

    def _write(self, items: list[str], source: str, snapshot) -> list[str]:
        # 写入时比较修订版本（框架的乐观并发控制），并记录来源
        self.store.put({"items": items}, source=source, expected_revision=snapshot.revision if snapshot else 0)
        return items

    def items(self) -> list[str]:
        return self._snapshot()[1]

    @staticmethod
    def _index(items: list[str], index: int) -> int:
        if not 1 <= index <= len(items):
            raise ValueError(f"没有第 {index} 条偏好（共 {len(items)} 条）")
        return index - 1

    def add(self, text: str, source: str) -> str:
        text = clean(text)
        if not text:
            raise ValueError("偏好内容不能为空")
        snapshot, items = self._snapshot()
        self._write(items + [text], source, snapshot)
        return text

    def update(self, index: int, text: str, source: str) -> tuple[str, str]:
        """修改第 index 条（从 1 开始）；返回 (修改前, 修改后)。"""
        text = clean(text)
        if not text:
            raise ValueError("偏好内容不能为空")
        snapshot, items = self._snapshot()
        i = self._index(items, index)
        before, items[i] = items[i], text
        self._write(items, source, snapshot)
        return before, text

    def remove(self, index: int, source: str) -> str:
        snapshot, items = self._snapshot()
        before = items.pop(self._index(items, index))
        self._write(items, source, snapshot)
        return before

    def find(self, text: str) -> int | None:
        """按内容定位条目（撤销时使用），返回从 1 开始的序号。"""
        items = self.items()
        return items.index(text) + 1 if text in items else None

    def clear(self, source: str) -> None:
        snapshot, items = self._snapshot()
        if items:
            self._write([], source, snapshot)


class PreferencesProvider:
    """ContextProvider：每次模型调用前读取最新偏好，作为必需资料注入。"""

    def __init__(self, memory: PreferenceMemory):
        self.memory = memory

    def get_context(self, query: str):
        items = self.memory.items()
        if not items:
            return []
        content = "用户偏好（由用户记录，或依据用户在对话中表达的要求整理；跨会话生效，与项目说明冲突时以项目说明为准）：\n" + "\n".join(
            f"- {item}" for item in items
        )
        return [ContextPacket(content=content, metadata={"id": "preferences", "source": "preferences", "required": True})]

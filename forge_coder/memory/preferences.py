"""用户偏好：基于框架 memory.profile.ProfileStore 的结构化画像，跨会话、跨任务生效。

- 存储：data/memory/profile.sqlite（框架负责 Schema 校验、修订版本与审计快照）；
- 写入：只由用户显式命令（/remember、/forget）写入，Agent 不能自行修改；
- 使用：PreferencesProvider 作为上下文组件，每次模型调用前把当前偏好作为必需资料注入主 Agent。
"""

from pathlib import Path

from forge_agents.context import ContextPacket
from forge_agents.memory.profile import ProfileModel, ProfileStore

USER_ID = "local-user"  # 本地单用户应用
NAMESPACE = "forge-coder"


class Preferences(ProfileModel):
    items: list[str] = []


class PreferenceMemory:
    def __init__(self, data_dir: Path):
        self.store = ProfileStore(
            str(Path(data_dir) / "memory" / "profile.sqlite"),
            user_id=USER_ID, namespace=NAMESPACE, schema=Preferences,
        )

    def _snapshot(self):
        snapshot = self.store.get()
        return snapshot, (snapshot.data.get("items", []) if snapshot else [])

    def items(self) -> list[str]:
        return self._snapshot()[1]

    def remember(self, text: str) -> list[str]:
        text = " ".join(text.split())
        if not text:
            raise ValueError("请在 /remember 后写上要记住的偏好")
        snapshot, items = self._snapshot()
        # 写入时比较修订版本（框架的乐观并发控制），并记录来源
        self.store.put({"items": items + [text]}, source="user:/remember",
                       expected_revision=snapshot.revision if snapshot else 0)
        return self.items()

    def forget(self, index: int | None = None) -> list[str]:
        """删除第 index 条（从 1 开始）；index 为 None 时清空全部。"""
        snapshot, items = self._snapshot()
        if snapshot is None or not items:
            return []
        if index is None:
            remaining = []
        elif 1 <= index <= len(items):
            remaining = items[: index - 1] + items[index:]
        else:
            raise ValueError(f"没有第 {index} 条偏好（共 {len(items)} 条）")
        self.store.put({"items": remaining}, source="user:/forget", expected_revision=snapshot.revision)
        return remaining


class PreferencesProvider:
    """ContextProvider：每次模型调用前读取最新偏好，作为必需资料注入。"""

    def __init__(self, memory: PreferenceMemory):
        self.memory = memory

    def get_context(self, query: str):
        items = self.memory.items()
        if not items:
            return []
        content = "用户偏好（用户通过 /remember 记录，跨会话生效；与项目说明冲突时以项目说明为准）：\n" + "\n".join(
            f"- {item}" for item in items
        )
        return [ContextPacket(content=content, metadata={"id": "preferences", "source": "preferences", "required": True})]

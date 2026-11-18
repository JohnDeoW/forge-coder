"""记忆：跨会话的用户偏好（preferences）。会话恢复由框架 SessionStore 完成，见 session.py。"""

from .preferences import PreferenceMemory, PreferencesProvider

__all__ = ["PreferenceMemory", "PreferencesProvider"]

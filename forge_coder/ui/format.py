"""CLI 与 Web 面板共用的显示辅助函数。"""

import json

MAIN_AGENT = "forge-coder"
TODO_MARK = {"pending": "☐", "in_progress": "▶", "completed": "✔"}


def short(value, limit=160) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:limit] + "…"


def tool_result_text(result) -> str:
    """工具结果是 ToolResponse 的 JSON 字符串时，只取其中的 text 便于阅读。"""
    if isinstance(result, str):
        try:
            data = json.loads(result)
            if isinstance(data, dict):
                return data.get("text") or data.get("error", {}).get("message") or result
        except ValueError:
            pass
    return result if isinstance(result, str) else json.dumps(result, ensure_ascii=False)


def render_todos(todos) -> str:
    return "\n".join(f"   {TODO_MARK.get(t.get('status'), '·')} {t.get('content', '')}" for t in todos or [])

"""上下文窗口用量：把框架 ContextBuilder 的诊断（breakdown）整理为已用量、窗口大小与各部分分布。"""

from ..config import CONTEXT_WINDOW

LABELS = {
    "system": "系统提示词",
    "packet:repo_map": "仓库地图",
    "packet:seen_files": "子 Agent 已读文件",
    "conversation": "对话",
    "tool_calls": "工具调用",
    "tool_results": "工具结果",
    "tool_schemas": "工具定义",
}


def context_usage(diagnostics: dict | None) -> dict | None:
    """把框架 ContextBuilder 的诊断整理为上下文窗口用量：总量、窗口大小与各部分分布。"""
    if not diagnostics or "breakdown" not in diagnostics:
        return None
    return {
        "used": diagnostics.get("estimated_tokens", 0),
        "window": CONTEXT_WINDOW,
        "breakdown": {LABELS.get(k, k): v for k, v in diagnostics["breakdown"].items() if v},
    }


def format_context_usage(usage: dict) -> str:
    def k(n):
        return f"{n / 1000:.1f}k"

    parts = " · ".join(f"{name} {k(v)}" for name, v in sorted(usage["breakdown"].items(), key=lambda x: -x[1]))
    return f"{k(usage['used'])} / {k(usage['window'])}（{usage['used'] / usage['window']:.1%}）：{parts}"

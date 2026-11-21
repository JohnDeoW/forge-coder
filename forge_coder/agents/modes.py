"""工作模式：plan（规划）、auto（自动审批）、manual（人工审批）。

三种模式共用同一个主 Agent 与对话历史，切换模式只改变：
- 可用工具：plan 只读（Read / Grep / Glob / ListDir + explorer 子 Agent），auto / manual 全部工具；
- 审批方式：auto 由程序级辅助 Agent 判断，manual 由用户确认（plan 不会产生需要审批的操作）；
- 系统提示词末尾的模式说明（文本见 prompts.py:MODE_PROMPTS，每轮按当前模式重新生成）。
"""

PLAN, AUTO, MANUAL = "plan", "auto", "manual"
MODES = (PLAN, AUTO, MANUAL)
DEFAULT_MODE = MANUAL

# plan 模式下主 Agent 能看到的工具；Task 只允许 explorer 与 memory（由策略检查）
PLAN_TOOLS = ("Read", "Grep", "Glob", "ListDir", "Task")
PLAN_SUBAGENT_ROLES = ("explorer", "memory")  # memory 只读写记忆，不改代码

LABELS = {PLAN: "规划", AUTO: "自动审批", MANUAL: "人工审批"}


def parse_mode(value: str) -> str:
    value = (value or "").strip().lower()
    if value not in MODES:
        raise ValueError(f"未知模式 {value!r}，可选: {', '.join(MODES)}")
    return value

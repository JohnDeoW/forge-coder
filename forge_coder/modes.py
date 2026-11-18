"""工作模式：plan（规划）、auto（自动审批）、manual（人工审批）。

三种模式共用同一个主 Agent 与对话历史，切换模式只改变：
- 可用工具：plan 只读（Read / Grep / Glob / ListDir + explorer 子 Agent），auto / manual 全部工具；
- 审批方式：auto 由程序级辅助 Agent 判断，manual 由用户确认（plan 不会产生需要审批的操作）；
- 系统提示词末尾的模式说明（每轮按当前模式重新生成）。
"""

PLAN, AUTO, MANUAL = "plan", "auto", "manual"
MODES = (PLAN, AUTO, MANUAL)
DEFAULT_MODE = MANUAL

# plan 模式下主 Agent 能看到的工具；Task 只允许 explorer（由策略检查）
PLAN_TOOLS = ("Read", "Grep", "Glob", "ListDir", "Task")
PLAN_SUBAGENT_ROLES = ("explorer",)

LABELS = {PLAN: "规划", AUTO: "自动审批", MANUAL: "人工审批"}

_SECTIONS = {
    PLAN: """当前模式：plan（规划，只读）。
你只能阅读代码（Read / Grep / Glob / ListDir，以及 Task 派出 explorer），不能修改文件、不能执行命令、不能使用 tester。
目标是和用户一起把需求弄清楚，形成可执行的方案：
1. 先阅读相关代码，了解现状；
2. 需求有歧义或缺少关键信息时，在回答中列出具体问题（最多 3 个，可附选项和你的建议），然后结束本轮，等待用户回答；
3. 信息充分后输出方案：目标、涉及的文件与改动要点、编号的实施步骤、验证方式、风险与待确认点；
4. 方案末尾提醒用户：确认方案后切换到 auto 或 manual 模式即可按方案执行。""",
    AUTO: """当前模式：auto（执行，自动审批）。
可以修改文件与执行命令。白名单外的命令由审批助手自动判断风险并批准或拒绝；被拒绝时理由会返回给你，请换用其他方式完成，不要重复尝试同一命令。
如果对话中已有用户确认过的方案，按方案执行：先用 TodoWrite 列出方案中的步骤，再逐项完成并验证。""",
    MANUAL: """当前模式：manual（执行，人工审批）。
可以修改文件与执行命令。白名单外的命令需要用户确认；被拒绝时请换用其他方式完成，不要重复尝试同一命令。
如果对话中已有用户确认过的方案，按方案执行：先用 TodoWrite 列出方案中的步骤，再逐项完成并验证。""",
}


def mode_section(mode: str) -> str:
    return "\n\n## 工作模式\n\n" + _SECTIONS[mode]


def parse_mode(value: str) -> str:
    value = (value or "").strip().lower()
    if value not in MODES:
        raise ValueError(f"未知模式 {value!r}，可选: {', '.join(MODES)}")
    return value

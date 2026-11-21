"""固定角色的子 Agent：Explorer（只读探索）、Tester（运行测试并汇报）与 Memory（维护项目记忆与用户偏好）。

基于框架的子代理机制实现：
- forge_agents.core.subagent_scope：隔离子 Agent 的会话状态，并按 ToolFilter fork 出受限的工具注册表；
- forge_agents.tools.tool_filter：ReadOnlyFilter / CustomFilter 控制角色可用的工具；
- 子 Agent 以 arun_stream 运行，事件通过回调转发给宿主（CLI / 面板）实时展示。

与框架自带 TaskTool 的区别：角色、工具范围与提示词由宿主固定，模型只能选择角色，
不能自行放开工具权限；子 Agent 的步骤可以实时展示。修改代码只由主 Agent 完成。
"""

from contextlib import closing
from typing import Any, Callable, Dict, List

from forge_agents import ReActAgent
from forge_agents.core.runtime import sync_events
from forge_agents.core.subagent_scope import subagent_scope
from forge_agents.tools import Tool, ToolParameter, ToolResponse, ToolErrorCode
from forge_agents.tools.tool_filter import CustomFilter, ReadOnlyFilter

from ..tools.memory_tools import MEMORY_TOOL_NAMES
from .prompts import EXPLORER_PROMPT, MEMORY_PROMPT, TESTER_PROMPT, build_subagent_prompt

ROLES = {
    "explorer": {
        "prompt": EXPLORER_PROMPT,
        "filter": lambda: ReadOnlyFilter(additional_allowed=["ListDir"]),
        "max_steps": 15,
    },
    "tester": {
        "prompt": TESTER_PROMPT,
        "filter": lambda: CustomFilter(allowed=["Read", "ListDir", "Glob", "Grep", "RunCommand"]),
        "max_steps": 10,
    },
    # 工具来自会话为它单独准备的注册表（只有记忆工具），不接触代码与命令
    "memory": {
        "prompt": MEMORY_PROMPT,
        "filter": lambda: CustomFilter(allowed=MEMORY_TOOL_NAMES),
        "max_steps": 15,
    },
}


AgentFactory = Callable[[str, str, int], ReActAgent]  # (角色, 系统提示词, 最大步数) -> Agent


class SubAgentTool(Tool):
    def __init__(self, factory: AgentFactory, repo_name: str, on_event: Callable[[Any], None] | None = None,
                 should_stop: Callable[[], bool] = lambda: False, on_finish: Callable[[str, Any], None] | None = None):
        """factory 由会话提供：决定子 Agent 使用的模型（按角色）、注册表、框架配置与上下文组件。
        should_stop：用户停止本轮时返回 True，子 Agent 在下一个事件处结束（工作线程无法被直接取消）；
        on_finish(role, run)：子 Agent 结束后回调（如 memory 未完成时撤销本次修改）。"""
        super().__init__(
            name="Task",
            description=(
                "派出子 Agent 处理独立子任务，子 Agent 有独立上下文，只返回结果摘要。"
                "role=explorer：只读探索代码（结构、相关文件、关键函数与行号）；"
                "role=tester：运行测试或程序并汇报结果（不修改文件）；"
                "role=memory：维护项目记忆与用户偏好（记录约定与用户偏好、合并或更正记忆）。"
                "两个子任务互不依赖时可在同一步并行派出。"
            ),
        )
        self.factory = factory
        self.repo_name = repo_name
        self.on_event = on_event
        self.should_stop = should_stop
        self.on_finish = on_finish

    def get_parameters(self) -> List[ToolParameter]:
        return [
            ToolParameter(
                name="role", type="string", description="子 Agent 角色：explorer、tester 或 memory",
                json_schema={"type": "string", "enum": list(ROLES)},
            ),
            ToolParameter(name="task", type="string", description="给子 Agent 的具体任务描述与期望的汇报内容"),
        ]

    def run(self, parameters: Dict[str, Any]) -> ToolResponse:
        role, task = parameters.get("role"), (parameters.get("task") or "").strip()
        if role not in ROLES or not task:
            return ToolResponse.error(code=ToolErrorCode.INVALID_PARAM, message="需要有效的 role（explorer / tester / memory）与 task")
        spec = ROLES[role]
        agent = self.factory(role, build_subagent_prompt(spec["prompt"], self.repo_name), spec["max_steps"])
        # 本方法由框架放在工作线程中执行（Tool.arun → to_thread），线程内没有事件循环，
        # 因此可以用 sync_events 驱动子 Agent 的异步事件流。
        with subagent_scope(agent, spec["filter"]()):
            with closing(sync_events(agent.arun_stream(task))) as events:
                for event in events:
                    if self.on_event:
                        self.on_event(event)
                    if self.should_stop():
                        break  # 关闭事件流：框架把子 Agent 标记为 cancelled 并补全中断的对话
            run = agent.last_run  # 退出 subagent_scope 会还原 Agent 状态，须在范围内读取
        if self.on_finish:
            self.on_finish(role, run)

        data = {
            "role": role,
            "status": run.status,
            "steps": run.model_calls,
            "tokens": run.usage.get("total_tokens", 0),
        }
        text = f"[{role}] {run.answer or '（无结论）'}"
        if run.status == "completed":
            return ToolResponse.success(text=text, data=data)
        return ToolResponse.partial(text=f"{text}\n（子任务未完成：{run.status}）", data=data)

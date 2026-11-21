"""memory 子 Agent 专用工具：查看、新增、修改、删除项目记忆与用户偏好（不能读写代码、不能执行命令）。

所有修改经 memory/editor.py:MemoryEditor 完成并写入修改记录；run 给出本次运行的上下文：
来源（main：主 Agent 派出 / archive：归档会话）、所在会话、归档时会话摘要的类型（merged / discarded），
并收集本次运行的修改（运行失败时整体撤销，归档结果中展示）。
"""

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List

from forge_agents.tools import Tool, ToolErrorCode, ToolParameter, ToolResponse

from ..memory.editor import MemoryEditor, summarize_change
from ..memory.project import NOTE

TARGETS = ["project", "preference"]


@dataclass
class MemoryRun:
    trigger: str = "main"  # main / archive
    task: str = ""
    summary_kind: str | None = None  # 归档时会话摘要的类型；会话进行中为 None（不能写会话摘要）
    changes: List[dict] = field(default_factory=list)


def _target(parameters) -> str | None:
    target = parameters.get("target")
    return target if target in TARGETS else None


class _MemoryTool(Tool):
    def __init__(self, editor: MemoryEditor, run: Callable[[], MemoryRun], name: str, description: str):
        super().__init__(name=name, description=description)
        self.editor = editor
        self.run_context = run

    def _done(self, change: dict) -> ToolResponse:
        self.run_context().changes.append(change)
        return ToolResponse.success(text=f"已{summarize_change(change)}", data={"change": change["id"]})

    @staticmethod
    def _fail(exc: Exception) -> ToolResponse:
        return ToolResponse.error(code=ToolErrorCode.INVALID_PARAM, message=str(exc))


TARGET_PARAM = ToolParameter(name="target", type="string", description="project：项目记忆；preference：用户偏好",
                             json_schema={"type": "string", "enum": TARGETS})
REASON_PARAM = ToolParameter(name="reason", type="string", description="修改理由（一句话，会记入修改记录给用户查看）")


class MemoryListTool(_MemoryTool):
    def __init__(self, editor, run):
        super().__init__(editor, run, "MemoryList", "列出全部用户偏好（带序号）与项目记忆（带 ID），标注用户编写、你不能修改的条目")

    def get_parameters(self) -> List[ToolParameter]:
        return []

    def run(self, parameters: Dict[str, Any]) -> ToolResponse:
        return ToolResponse.success(text=self.editor.listing())


class MemoryAddTool(_MemoryTool):
    def __init__(self, editor, run):
        super().__init__(editor, run, "MemoryAdd",
                         "新增一条记忆。项目记忆的 kind：note（项目约定、决策与知识）或 summary（会话摘要，只在归档会话时可用）")

    def get_parameters(self) -> List[ToolParameter]:
        return [
            TARGET_PARAM,
            ToolParameter(name="content", type="string", description="记忆内容，简洁、自成一体"),
            ToolParameter(name="kind", type="string", description="项目记忆的类型，默认 note", required=False,
                          json_schema={"type": "string", "enum": ["note", "summary"]}),
            REASON_PARAM,
        ]

    def run(self, parameters: Dict[str, Any]) -> ToolResponse:
        target, ctx = _target(parameters), self.run_context()
        if target is None or not (parameters.get("content") or "").strip():
            return ToolResponse.error(code=ToolErrorCode.INVALID_PARAM, message="需要有效的 target 与 content")
        kind = NOTE
        if target == "project" and parameters.get("kind") == "summary":
            if not ctx.summary_kind:
                return ToolResponse.error(code=ToolErrorCode.INVALID_PARAM, message="会话摘要只在归档会话时写入；会话进行中请用 note")
            kind = ctx.summary_kind
        try:
            return self._done(self.editor.add(target, parameters["content"], trigger=ctx.trigger, task=ctx.task,
                                              reason=parameters.get("reason", ""), kind=kind))
        except ValueError as exc:
            return self._fail(exc)


class MemoryUpdateTool(_MemoryTool):
    def __init__(self, editor, run):
        super().__init__(editor, run, "MemoryUpdate", "修改一条记忆（合并重复、更正过时或冲突的内容）；用户编写的条目不能修改")

    def get_parameters(self) -> List[ToolParameter]:
        return [
            TARGET_PARAM,
            ToolParameter(name="ref", type="string", description="项目记忆的 ID（MemoryList 中方括号内）或用户偏好的序号"),
            ToolParameter(name="content", type="string", description="修改后的完整内容"),
            REASON_PARAM,
        ]

    def run(self, parameters: Dict[str, Any]) -> ToolResponse:
        target, ctx = _target(parameters), self.run_context()
        if target is None or not parameters.get("ref") or not (parameters.get("content") or "").strip():
            return ToolResponse.error(code=ToolErrorCode.INVALID_PARAM, message="需要有效的 target、ref 与 content")
        try:
            return self._done(self.editor.update(target, parameters["ref"], parameters["content"], trigger=ctx.trigger,
                                                 task=ctx.task, reason=parameters.get("reason", "")))
        except ValueError as exc:
            return self._fail(exc)


class MemoryDeleteTool(_MemoryTool):
    def __init__(self, editor, run):
        super().__init__(editor, run, "MemoryDelete", "删除一条过时、重复或错误的记忆；用户编写的条目不能删除")

    def get_parameters(self) -> List[ToolParameter]:
        return [
            TARGET_PARAM,
            ToolParameter(name="ref", type="string", description="项目记忆的 ID（MemoryList 中方括号内）或用户偏好的序号"),
            REASON_PARAM,
        ]

    def run(self, parameters: Dict[str, Any]) -> ToolResponse:
        target, ctx = _target(parameters), self.run_context()
        if target is None or not parameters.get("ref"):
            return ToolResponse.error(code=ToolErrorCode.INVALID_PARAM, message="需要有效的 target 与 ref")
        try:
            return self._done(self.editor.delete(target, parameters["ref"], trigger=ctx.trigger, task=ctx.task,
                                                 reason=parameters.get("reason", "")))
        except ValueError as exc:
            return self._fail(exc)


MEMORY_TOOLS = (MemoryListTool, MemoryAddTool, MemoryUpdateTool, MemoryDeleteTool)
MEMORY_TOOL_NAMES = ["MemoryList", "MemoryAdd", "MemoryUpdate", "MemoryDelete"]

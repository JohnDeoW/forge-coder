"""命令行入口：

    forge-coder                 进入 REPL（默认项目 inventory）
    forge-coder --repo NAME     指定测试项目
    forge-coder --web [--port]  启动简易 Web 面板
    forge-coder reset [NAME]    把测试项目还原为初始状态
    forge-coder list            列出可用的测试项目模板
    forge-coder eval [...]      运行固定评估任务集（见 evaluation.py）
"""

import argparse
import asyncio
import json
import os

from forge_agents.core.streaming import StreamEventType

from .config import load_settings
from .context import format_context_usage
from .workspace import list_samples, reset_repo

DEFAULT_REPO = "inventory"
HELP = """直接输入需求发送给 Agent（ReAct 模式）；/plan <需求> 先规划再逐步执行（PlanSolve 模式）；命令：
  /diff    查看当前任务相对 main 的全部改动    /log     查看逐轮修改记录
  /accept  合并当前任务到 main                /reject  丢弃当前任务
  /status  当前任务信息    /reset  还原测试项目到初始状态    /help  帮助    /exit  退出"""

DIM, CYAN, YELLOW, RED, GREEN, RESET = (
    "\033[2m", "\033[36m", "\033[33m", "\033[31m", "\033[32m", "\033[0m"
)


def _short(value, limit=160) -> str:
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


MAIN_AGENT = "forge-coder"
TODO_MARK = {"pending": "☐", "in_progress": "▶", "completed": "✔"}


def render_todos(todos) -> str:
    return "\n".join(f"   {TODO_MARK.get(t.get('status'), '·')} {t.get('content', '')}" for t in todos or [])


def _print_sub_event(event) -> None:
    """子 Agent 的事件缩进显示；其结论通过 Task 工具结果返回给主 Agent，这里只显示步骤。"""
    data, prefix = event.data, f"   {DIM}│{RESET} {YELLOW}[{event.agent_name}]{RESET}"
    if event.type == StreamEventType.TOOL_CALL_START:
        name, args = data.get("tool_name"), data.get("args") or {}
        if name == "Thought":
            print(f"{prefix} {DIM}💭 {_short(args.get('reasoning', ''), 120)}{RESET}")
        elif name != "Finish":
            print(f"{prefix} 🔧 {name} {DIM}{_short(args, 120)}{RESET}")
    elif event.type == StreamEventType.TOOL_CALL_FINISH and data.get("tool_name") not in ("Thought", "Finish"):
        print(f"{prefix} {DIM}  ↳ {_short(tool_result_text(data.get('result')), 120)}{RESET}")


async def run_turn(session, text: str, mode: str = "react") -> None:
    streamed = False
    line_open = False  # 流式文本块之后还没换行
    async for event in session.stream(text, mode):
        data = event.data
        if event.type == StreamEventType.LLM_CHUNK and event.agent_name == MAIN_AGENT:
            print(data.get("chunk", ""), end="", flush=True)
            streamed = line_open = True
            continue
        if line_open:
            print()
            line_open = False
        if event.agent_name != MAIN_AGENT:
            _print_sub_event(event)
            continue
        if event.type == StreamEventType.TOOL_CALL_START:
            name, args = data.get("tool_name"), data.get("args") or {}
            if name == "Thought":
                print(f"{DIM}💭 {args.get('reasoning', '')}{RESET}")
            elif name == "Task":
                print(f"{CYAN}🤖 派出子 Agent [{args.get('role')}]{RESET} {_short(args.get('task', ''))}")
            elif name == "TodoWrite":
                print(f"{CYAN}📝 待办清单{RESET}\n{render_todos(args.get('todos'))}")
            elif name != "Finish":
                print(f"{CYAN}🔧 {name}{RESET} {_short(args)}")
        elif event.type == StreamEventType.TOOL_CALL_FINISH:
            if data.get("tool_name") not in ("Thought", "Finish", "TodoWrite"):
                print(f"{DIM}   ↳ {_short(tool_result_text(data.get('result')))}{RESET}")
        elif event.type == StreamEventType.STEP_FINISH and data.get("phase") == "planning":
            plan = "\n".join(f"   {i}. {s}" for i, s in enumerate(data.get("plan") or [], 1))
            print(f"{CYAN}📋 执行计划{RESET}\n{plan}")
        elif event.type == StreamEventType.STEP_START and data.get("phase") == "execution":
            print(f"{CYAN}▶ 步骤 {data.get('step')}/{data.get('total_steps')}：{data.get('description')}{RESET}")
        elif event.type == StreamEventType.AGENT_FINISH:
            if not streamed:
                print(f"\n{data.get('result') or ''}")
            print(
                f"{DIM}[{data.get('status')} · 主 Agent {data.get('total_steps')} 步 · "
                f"{data.get('total_tokens')} tokens]{RESET}"
            )
        elif event.type == StreamEventType.ERROR:
            print(f"{RED}错误: {data.get('error')}{RESET}")
    if session.usage.last:
        print(f"{DIM}本轮用量：{session.usage.last.summary()}{RESET}")
    if session.last_context:
        print(f"{DIM}上下文窗口：{format_context_usage(session.last_context)}{RESET}")
    if session.last_commit:
        print(f"{GREEN}✔ 本轮改动已提交到 {session.task.branch}（{session.last_commit}），/diff 查看{RESET}")


def color_diff(text: str) -> str:
    lines = []
    for line in text.splitlines():
        if line.startswith(("+++", "---", "diff --git", "index ", "new file", "deleted file")):
            lines.append(f"{DIM}{line}{RESET}")
        elif line.startswith("+"):
            lines.append(f"{GREEN}{line}{RESET}")
        elif line.startswith("-"):
            lines.append(f"{RED}{line}{RESET}")
        elif line.startswith("@@"):
            lines.append(f"{CYAN}{line}{RESET}")
        else:
            lines.append(line)
    return "\n".join(lines)


def handle_command(session, text: str) -> None:
    if text == "/help":
        print(HELP)
    elif text == "/status":
        print(json.dumps(session.status(), ensure_ascii=False, indent=2))
    elif text == "/reset":
        session.reset()
        print("已还原测试项目到初始状态（含所有任务分支），对话已清空。")
    elif text not in ("/diff", "/log", "/accept", "/reject"):
        print(f"未知命令 {text}，输入 /help 查看帮助")
    elif session.task is None:
        print("当前没有进行中的任务，发送一条需求即可开始。")
    elif text == "/diff":
        print(color_diff(session.diff()) or "当前任务没有改动。")
    elif text == "/log":
        print(session.log() or "当前任务还没有提交记录。")
    elif text == "/accept":
        print(f"{GREEN}已合并到 main（{session.accept()}），任务结束。{RESET}")
    elif text == "/reject":
        session.reject()
        print("已丢弃当前任务，main 未受影响。")


def cli_approver(command: str) -> str:
    """白名单外命令的确认：返回 once / always / deny（供 WorkspacePolicy 调用）。"""
    print(f"{YELLOW}⚠ Agent 请求执行白名单外的命令：{RESET}{command}")
    try:
        answer = input("  允许吗？[y] 本次  [a] 本会话总是允许该程序  [N] 拒绝: ").strip().lower()
    except EOFError:
        answer = ""
    return {"y": "once", "a": "always"}.get(answer, "deny")


def repl(repo: str) -> None:
    from .session import CoderSession

    session = CoderSession(load_settings(), repo, approver=cli_approver)
    print(f"ForgeCoder · 项目 {repo} · 模型 {session.llm.model}\n{HELP}")
    while True:
        try:
            text = input(f"\n{YELLOW}forge> {RESET}").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not text:
            continue
        if text in ("/exit", "/quit"):
            break
        try:
            if text.startswith("/plan "):
                asyncio.run(run_turn(session, text[len("/plan "):].strip(), mode="plan"))
            elif text.startswith("/"):
                handle_command(session, text)
            else:
                asyncio.run(run_turn(session, text))
        except KeyboardInterrupt:
            print(f"\n{RED}已中断{RESET}")
        except Exception as exc:  # 单次失败不退出 REPL
            print(f"{RED}失败: {exc}{RESET}")


def main(argv=None) -> None:
    os.system("")  # 让 Windows 控制台启用 ANSI 颜色
    parser = argparse.ArgumentParser(prog="forge-coder", description="ForgeCoder 代码开发 Agent")
    parser.add_argument("--repo", default=DEFAULT_REPO, help="测试项目名称")
    parser.add_argument("--web", action="store_true", help="启动简易 Web 面板")
    parser.add_argument("--port", type=int, default=8765, help="Web 面板端口")
    sub = parser.add_subparsers(dest="command")
    reset_p = sub.add_parser("reset", help="把测试项目还原为初始状态")
    reset_p.add_argument("name", nargs="?", default=DEFAULT_REPO)
    sub.add_parser("list", help="列出测试项目模板")
    eval_p = sub.add_parser("eval", help="运行固定评估任务集")
    eval_p.add_argument("--task", action="append", help="只运行指定任务，可多次指定")
    eval_p.add_argument("--repeat", type=int, default=1, help="每个任务的运行次数")
    eval_p.add_argument("--save-baseline", action="store_true", help="同时保存为对比基线")
    args = parser.parse_args(argv)

    settings = load_settings()
    if args.command == "reset":
        print(f"已还原: {reset_repo(settings, args.name)}")
    elif args.command == "list":
        print("\n".join(list_samples(settings)))
    elif args.command == "eval":
        from .evaluation import TASK_IDS, run_eval

        unknown = set(args.task or []) - set(TASK_IDS)
        if unknown:
            parser.error(f"未知任务 {sorted(unknown)}，可选: {TASK_IDS}")
        run_eval(settings, args.task, max(args.repeat, 1), args.save_baseline)
    elif args.web:
        from .web.server import serve

        serve(args.repo, args.port)
    else:
        repl(args.repo)


if __name__ == "__main__":
    main()

"""命令行入口：

    forge-coder                 进入 REPL（默认项目 inventory）
    forge-coder --repo NAME     指定测试项目
    forge-coder --mode MODE     以指定工作模式启动（plan / auto / manual，默认 manual）
    forge-coder --web [--port]  启动简易 Web 面板
    forge-coder reset [NAME]    把测试项目还原为初始状态
    forge-coder list            列出可用的测试项目模板
    forge-coder eval [...]      运行固定评估任务集（见 evaluation.py）
"""

import argparse
import asyncio
import signal
import json
import os

from forge_agents.core.streaming import StreamEventType

from ..agents.modes import DEFAULT_MODE, LABELS, MODES
from ..config import load_settings
from ..context import format_context_usage
from ..workspace import list_samples, reset_repo
from .format import MAIN_AGENT, render_todos, short, tool_result_text

DEFAULT_REPO = "inventory"
HELP = """直接输入需求发送给 Agent；命令：
  /mode [plan|auto|manual]  查看或切换工作模式（plan 只读规划 · auto 辅助 Agent 自动审批 · manual 人工审批）
  /plansolve <需求>         用框架的 PlanSolve 范式完成一次需求（先规划再逐步执行，范式演示，plan 模式下不可用）
  /diff    查看当前任务相对 main 的全部改动    /log        查看逐轮修改记录
  /review  Reviewer 子 Agent 审查当前改动      /undo       撤销最近一轮（代码与对话回到该轮之前）
  /rewind [轮次]  回溯到某一轮之前（不带参数列出可回溯的轮次），该轮及之后的改动与对话一并移除
  运行中按 Ctrl+C 停止本轮：已产生的改动照常提交，对话历史保持完整
  /accept  合并到 main（会话继续，可再次合并）  /discard    丢弃尚未合并的改动
  /archive 归档会话：总结写入项目记忆并结束任务（未合并的改动会被丢弃）
  /remember <偏好>  记住偏好（跨会话生效）    /memory     查看偏好    /forget [序号|all] 删除偏好
  /project-memory [序号]  查看项目记忆（会话摘要等）   /project-memory del <序号|all>  删除项目记忆
  /compact [保留轮数]     压缩对话历史为摘要（默认全部压缩），摘要写入项目记忆
  /approvals 查看本任务的审批记录            /status     当前任务信息
  /reset   还原测试项目到初始状态            /help 帮助  /exit 退出
退出后可用 forge-coder --resume 恢复未结束的任务会话。"""

DIM, CYAN, YELLOW, RED, GREEN, RESET = (
    "\033[2m", "\033[36m", "\033[33m", "\033[31m", "\033[32m", "\033[0m"
)
REVIEWER = "reviewer"


def _print_sub_event(event) -> None:
    """子 Agent 的事件缩进显示；其结论通过 Task 工具结果返回给主 Agent，这里只显示步骤。"""
    data, prefix = event.data, f"   {DIM}│{RESET} {YELLOW}[{event.agent_name}]{RESET}"
    if event.type == StreamEventType.TOOL_CALL_START:
        name, args = data.get("tool_name"), data.get("args") or {}
        if name == "Thought":
            print(f"{prefix} {DIM}💭 {short(args.get('reasoning', ''), 120)}{RESET}")
        elif name != "Finish":
            print(f"{prefix} 🔧 {name} {DIM}{short(args, 120)}{RESET}")
    elif event.type == StreamEventType.TOOL_CALL_FINISH and data.get("tool_name") not in ("Thought", "Finish"):
        print(f"{prefix} {DIM}  ↳ {short(tool_result_text(data.get('result')), 120)}{RESET}")


def _print_auto_approval(data) -> None:
    mark, color = ("允许", GREEN) if data.get("approved") else ("拒绝", RED)
    print(f"   {color}🛡 审批助手：{mark}{RESET}（风险 {data.get('risk')}）{short(data.get('summary', ''), 80)}"
          f"\n   {DIM}理由：{short(data.get('reason', ''), 160)}{RESET}")


async def run_turn(session, text: str, paradigm: str = "react") -> None:
    await stoppable(session, render(session, session.stream(text, paradigm)))


async def run_review(session) -> None:
    print(f"{CYAN}🔍 Reviewer 子 Agent 正在审查当前任务的改动…{RESET}")
    await stoppable(session, render(session, session.review()))


async def stoppable(session, job) -> None:
    """运行期间 Ctrl+C 停止本轮（session.stop），而不是直接打断进程；结束后恢复默认处理。"""
    loop = asyncio.get_running_loop()
    previous = signal.getsignal(signal.SIGINT)

    def on_interrupt(*_):
        print(f"\n{YELLOW}正在停止本轮…{RESET}", flush=True)
        loop.call_soon_threadsafe(session.stop)

    signal.signal(signal.SIGINT, on_interrupt)
    try:
        await job
    finally:
        signal.signal(signal.SIGINT, previous)


async def render(session, events) -> None:
    """渲染一轮事件流（对话轮与 /review 共用）。"""
    streamed = False
    line_open = False  # 流式文本块之后还没换行
    async for event in events:
        data = event.data
        if event.type == StreamEventType.LLM_CHUNK and event.agent_name == MAIN_AGENT:
            print(data.get("chunk", ""), end="", flush=True)
            streamed = line_open = True
            continue
        if line_open:
            print()
            line_open = False
        if event.type == StreamEventType.APPROVAL_RESOLVED:
            _print_auto_approval(data)
            continue
        if event.agent_name == REVIEWER and event.type == StreamEventType.AGENT_FINISH:
            print(f"\n{CYAN}📋 审查结论{RESET}\n{data.get('result') or ''}")
            continue
        if event.agent_name != MAIN_AGENT:
            _print_sub_event(event)
            continue
        if event.type == StreamEventType.TOOL_CALL_START:
            name, args = data.get("tool_name"), data.get("args") or {}
            if name == "Thought":
                print(f"{DIM}💭 {args.get('reasoning', '')}{RESET}")
            elif name == "Task":
                print(f"{CYAN}🤖 派出子 Agent [{args.get('role')}]{RESET} {short(args.get('task', ''))}")
            elif name == "TodoWrite":
                print(f"{CYAN}📝 待办清单{RESET}\n{render_todos(args.get('todos'))}")
            elif name != "Finish":
                print(f"{CYAN}🔧 {name}{RESET} {short(args)}")
        elif event.type == StreamEventType.TOOL_CALL_FINISH:
            if data.get("tool_name") not in ("Thought", "Finish", "TodoWrite"):
                print(f"{DIM}   ↳ {short(tool_result_text(data.get('result')))}{RESET}")
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
    outcome = getattr(session, "last_outcome", None)
    if outcome and outcome["status"] == "stopped":
        print(f"{YELLOW}■ {outcome['reason']}，已产生的改动照常提交{RESET}")
    elif outcome and outcome["status"] == "failed":
        print(f"{RED}✖ {outcome['reason']}{RESET}{DIM}（{outcome.get('detail', '')}）；可用 /undo 撤销本轮后重新发送{RESET}")
    if session.usage.last:
        print(f"{DIM}本轮用量：{session.usage.last.summary()}{RESET}")
    if session.last_context:
        print(f"{DIM}上下文窗口：{format_context_usage(session.last_context)}{RESET}")
    if session.last_compaction:
        print_compaction(session.last_compaction)
    if session.last_commit:
        print(f"{GREEN}✔ 本轮改动已提交到 {session.task.branch}（{session.last_commit}），/diff 查看{RESET}")


def print_compaction(result: dict) -> None:
    print(f"{CYAN}🗜 对话历史已压缩：{result['compressed_messages']} 条消息 → 摘要，历史 "
          f"{result['tokens_before']} → {result['tokens_after']} tokens；摘要已写入项目记忆{RESET}")


def print_task_summary(session) -> None:
    if session.last_task_summary:
        print(f"{DIM}📚 会话摘要已写入项目记忆：\n{session.last_task_summary}{RESET}")


def project_memory_command(session, arg: str) -> None:
    """/project-memory 列表；/project-memory <序号> 查看全文；/project-memory del <序号|all> 删除。"""
    memory = session.project_memory
    if arg.startswith("del"):
        target = arg[3:].strip()
        if not target:
            raise ValueError("用法：/project-memory del <序号|all>")
        remaining = memory.delete(None if target == "all" else int(target))
        print(f"已删除，剩余 {remaining} 条项目记忆。")
        return
    entries = memory.entries()
    if arg:
        e = entries[int(arg) - 1] if 1 <= int(arg) <= len(entries) else None
        if e is None:
            raise ValueError(f"没有第 {arg} 条项目记忆（共 {len(entries)} 条）")
        print(f"[{e['time']}] {e['label']} · 任务 {e['task']}\n{e['content']}")
        return
    if not entries:
        print("项目记忆为空：归档会话时会自动写入会话摘要。")
    for i, e in enumerate(entries, 1):
        print(f"{i}. [{e['time']}] {e['label']} · 任务 {e['task']}  {DIM}{short(e['content'].replace(chr(10), ' '), 100)}{RESET}")


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


def print_approvals(records: list[dict]) -> None:
    if not records:
        print("本任务还没有审批记录。")
    for i, r in enumerate(records, 1):
        result = f"{GREEN}允许{RESET}" if r.get("approved") else f"{RED}拒绝{RESET}"
        print(f"{i}. [{r['time']}] {r['mode']} · {r.get('by')} · {result} · {r['tool']}: {r['operation']}")
        if r.get("by") == "utility":
            print(f"   风险 {r.get('risk')} · {r.get('summary')}\n   理由：{r.get('decision_reason')}")
            print(f"   {DIM}辅助 Agent 原始回复：{short(((r.get('exchange') or {}).get('response')) or '（无）', 200)}{RESET}")
    if records:
        print(f"{DIM}完整记录（含发给辅助 Agent 的提示词）：data/approvals/<项目>/<任务ID>.jsonl{RESET}")


def handle_command(session, text: str) -> None:
    if text == "/help":
        print(HELP)
    elif text == "/mode" or text.startswith("/mode "):
        arg = text[len("/mode"):].strip()
        if arg:
            session.set_mode(arg)
        print(f"当前模式：{session.mode}（{LABELS[session.mode]}）；可选 {' / '.join(MODES)}")
    elif text == "/approvals":
        print_approvals(session.approval_log)
    elif text.startswith("/remember"):
        items = session.preferences.remember(text[len("/remember"):])
        print(f"已记住（共 {len(items)} 条偏好，后续每次请求都会带上）")
    elif text == "/memory":
        items = session.preferences.items()
        print("\n".join(f"{i}. {item}" for i, item in enumerate(items, 1)) or "还没有记录任何偏好，用 /remember <偏好> 添加。")
    elif text.startswith("/forget"):
        arg = text[len("/forget"):].strip()
        remaining = session.preferences.forget(None if arg in ("", "all") else int(arg))
        print(f"已删除，剩余 {len(remaining)} 条偏好。")
    elif text == "/project-memory" or text.startswith("/project-memory "):
        project_memory_command(session, text[len("/project-memory"):].strip())
    elif text == "/status":
        print(json.dumps(session.status(), ensure_ascii=False, indent=2))
    elif text == "/reset":
        session.reset()
        print("已还原测试项目到初始状态（含所有任务分支与项目记忆），对话已清空。")
    elif text not in ("/diff", "/log", "/accept", "/discard", "/archive", "/undo", "/review", "/compact", "/rewind") \
            and not text.startswith(("/compact ", "/rewind ")):
        print(f"未知命令 {text}，输入 /help 查看帮助")
    elif session.task is None:
        print("当前没有进行中的任务，发送一条需求即可开始。")
    elif text == "/review":
        asyncio.run(run_review(session))
    elif text.startswith("/compact"):
        arg = text[len("/compact"):].strip()
        print(f"{CYAN}🗜 正在用辅助 Agent 模型压缩对话历史…{RESET}")
        result = session.compact(int(arg) if arg else 0)
        print_compaction(result)
        print(f"{DIM}{result['summary']}{RESET}")
    elif text == "/undo":
        result = session.undo()
        print(f"{GREEN}已撤销第 {result['round']} 轮，任务分支回到 {result['head']}。该轮需求：{RESET}{result['text']}")
    elif text.startswith("/rewind"):
        arg = text[len("/rewind"):].strip()
        allowed = session.rewindable_rounds()
        if not arg:
            for i, request in enumerate(session.requests, 1):
                mark = f"{GREEN}可回溯{RESET}" if i in allowed else f"{DIM}不可回溯{RESET}"
                print(f"  {i}. [{mark}] {short(request, 80)}")
            print("用 /rewind <轮次> 回溯；已合并、已丢弃或已被历史压缩的轮次不能回溯。" if session.requests else "还没有对话轮次。")
            return
        result = session.rewind(int(arg))
        print(f"{GREEN}已回溯到第 {result['round']} 轮之前，任务分支回到 {result['head']}。该轮需求（可修改后重新发送）：{RESET}\n{result['text']}")
    elif text == "/diff":
        print(color_diff(session.diff()) or "当前任务没有改动。")
    elif text == "/log":
        print(session.log() or "当前任务还没有提交记录。")
    elif text == "/accept":
        print(f"{GREEN}已合并到 main（{session.accept()}），会话继续；之后的改动可再次合并，/archive 归档会话。{RESET}")
    elif text == "/discard":
        session.discard()
        print("已丢弃尚未合并的改动，代码回到最近一次合并（或会话开始）时的状态，会话继续。")
    elif text == "/archive":
        if session.diff().strip():
            try:
                ok = input(f"{YELLOW}还有尚未合并的改动，归档会丢弃它们。继续吗？[y/N] {RESET}").strip().lower() == "y"
            except EOFError:
                ok = False
            if not ok:
                print("已取消；可先 /accept 合并。")
                return
        print(f"{CYAN}正在归档：辅助 Agent 总结本次会话…{RESET}")
        result = session.archive()
        print(f"{GREEN}会话已归档（合并 {result['merges']} 次），下一条需求将开始新会话。{RESET}")
        print_task_summary(session)


def cli_approver(command: str) -> str:
    """白名单外命令的确认：返回 once / always / deny（供 WorkspacePolicy 调用）。"""
    print(f"{YELLOW}⚠ Agent 请求执行白名单外的命令：{RESET}{command}")
    try:
        answer = input("  允许吗？[y] 本次  [a] 本会话总是允许该程序  [N] 拒绝: ").strip().lower()
    except EOFError:
        answer = ""
    return {"y": "once", "a": "always"}.get(answer, "deny")


def repl(repo: str, mode: str, resume: str | None = None) -> None:
    from ..session import PLANSOLVE, CoderSession

    session = CoderSession(load_settings(), repo, approver=cli_approver, mode=mode)
    print(f"ForgeCoder · 项目 {repo} · 模型 {session.llm.model} · 模式 {session.mode}\n{HELP}")
    if resume:
        meta = session.resume(None if resume == "latest" else resume)
        print(f"{GREEN}已恢复任务 {meta['task_id']}（模式 {session.mode}，已提交 {meta['rounds']} 轮，"
              f"{len(meta.get('requests', []))} 条需求，保存于 {meta['saved_at']}）{RESET}")
    while True:
        try:
            text = input(f"\n{YELLOW}forge[{session.mode}]> {RESET}").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not text:
            continue
        if text in ("/exit", "/quit"):
            break
        try:
            if text.startswith("/plansolve "):
                asyncio.run(run_turn(session, text[len("/plansolve "):].strip(), paradigm=PLANSOLVE))
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
    parser.add_argument("--mode", default=DEFAULT_MODE, choices=MODES, help="工作模式")
    parser.add_argument("--web", action="store_true", help="启动简易 Web 面板")
    parser.add_argument("--port", type=int, default=8765, help="Web 面板端口")
    parser.add_argument("--resume", nargs="?", const="latest", metavar="TASK_ID",
                        help="恢复未结束的任务会话（不指定则恢复最近一次）")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("sessions", help="列出可恢复的任务会话")
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
    elif args.command == "sessions":
        from ..session import list_sessions

        rows = list_sessions(settings, args.repo)
        for s in rows:
            first = (s.get("requests") or ["（无）"])[0]
            print(f"{s['task_id']}  保存于 {s['saved_at']}  模式 {s['mode']}  已提交 {s['rounds']} 轮  首条需求：{short(first, 60)}")
        print("" if rows else "没有可恢复的会话。", end="")
    elif args.command == "eval":
        from ..metrics.evaluation import TASK_IDS, run_eval

        unknown = set(args.task or []) - set(TASK_IDS)
        if unknown:
            parser.error(f"未知任务 {sorted(unknown)}，可选: {TASK_IDS}")
        run_eval(settings, args.task, max(args.repeat, 1), args.save_baseline)
    elif args.web:
        from .web.server import serve

        serve(args.repo, args.port, args.mode)
    else:
        repl(args.repo, args.mode, args.resume)


if __name__ == "__main__":
    main()

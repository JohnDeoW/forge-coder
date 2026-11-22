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
from ..workspace import is_generated, list_samples, reset_repo, sample_info
from ..memory.editor import TRIGGER_LABELS, summarize_change
from .format import MAIN_AGENT, render_todos, short, tool_result_text

DEFAULT_REPO = "inventory"
HELP = """直接输入需求发送给 Agent；命令：
  /mode [plan|auto|manual]  查看或切换工作模式（plan 只读规划 · auto 辅助 Agent 自动审批 · manual 人工审批）
  /model [角色] [模型]      查看或切换模型（角色 main / explorer / tester / reviewer / utility，省略角色为 main），下一轮生效
  /plansolve <需求>         用框架的 PlanSolve 范式完成一次需求（先规划再逐步执行，范式演示，plan 模式下不可用）
  /diff    查看当前任务相对 main 的全部改动    /log        查看逐轮修改记录
  /review  Reviewer 子 Agent 审查当前改动      /undo       撤销最近一轮（代码与对话回到该轮之前）
  /rewind [轮次]  回溯到某一轮之前（不带参数列出可回溯的轮次），该轮及之后的改动与对话一并移除
  运行中按 Ctrl+C 停止本轮：已产生的改动照常提交，对话历史保持完整
  /accept  合并到 main（会话继续，可再次合并）  /discard    丢弃尚未合并的改动
  /archive 归档会话：总结写入项目记忆并结束任务（未合并的改动会被丢弃）
  /remember <偏好>  记住偏好（跨会话生效）    /memory     查看偏好    /forget [序号|all] 删除偏好
  /memory edit <序号> <内容>    编辑一条偏好（编辑过的条目 memory 子 Agent 不会再改）
  /project-memory [序号]  查看项目记忆（会话摘要、项目约定）   /project-memory edit <序号> <内容> | del <序号|all>
  /memory-log [all]  查看本会话（all：全部）的记忆修改记录    /memory-undo <记录ID>  撤销一次修改
  也可以直接在对话中要求记住或修改记忆，主 Agent 会派 memory 子 Agent 处理；归档会话时会自动整理记忆
  /compact [保留轮数]     压缩对话历史为摘要（默认全部压缩），摘要只用于本会话后续的上下文
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
    extra = await asyncio.to_thread(session.after_round)  # 会话标题与下一步建议（设置中可关闭建议）
    if extra.get("title"):
        print(f"{DIM}会话标题：{extra['title']}{RESET}")
    for i, item in enumerate(extra.get("suggestions", []), 1):
        print(f"{CYAN}💡 {i}. {item}{RESET}" if i == 1 else f"   {i}. {item}")


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
          f"{result['tokens_before']} → {result['tokens_after']} tokens；摘要作为本会话后续的上下文{RESET}")


def print_task_summary(session) -> None:
    if session.last_task_summary:
        print(f"{DIM}📚 会话摘要已写入项目记忆：\n{session.last_task_summary}{RESET}")
    others = [c for c in session.last_memory_changes if c["after"] != session.last_task_summary]
    if others:
        print(f"{CYAN}🧠 memory 子 Agent 整理了记忆：{RESET}")
        for c in others:
            print(f"   {summarize_change(c)}  {DIM}[{c['id']}]{RESET}")


def _split_edit(arg: str) -> tuple[int, str]:
    index, _, text = arg.partition(" ")
    if not index.isdigit() or not text.strip():
        raise ValueError("用法：edit <序号> <新内容>")
    return int(index), text


def preferences_command(session, text: str) -> None:
    """/remember、/memory、/memory edit、/forget：用户对偏好的操作，经记忆编辑器记录（来源为用户）。"""
    editor, task = session.memory_editor, session.task.id if session.task else ""
    if text.startswith("/remember"):
        editor.add("preference", text[len("/remember"):], trigger="user", task=task)
        print(f"已记住（共 {len(session.preferences.items())} 条偏好，后续每次请求都会带上）")
    elif text.startswith("/memory edit"):
        index, content = _split_edit(text[len("/memory edit"):].strip())
        editor.update("preference", index, content, trigger="user", task=task)
        print("已修改。")
    elif text.startswith("/forget"):
        arg = text[len("/forget"):].strip()
        indexes = range(len(session.preferences.items()), 0, -1) if arg in ("", "all") else [int(arg)]
        for index in indexes:
            editor.delete("preference", index, trigger="user", task=task)
        print(f"已删除，剩余 {len(session.preferences.items())} 条偏好。")
    else:
        items, agent_items = session.preferences.items(), editor.agent_preferences()
        for i, item in enumerate(items, 1):
            print(f"{i}. {item}" + (f"  {DIM}（memory 子 Agent 整理）{RESET}" if item in agent_items else ""))
        if not items:
            print("还没有记录任何偏好，用 /remember <偏好> 添加，或在对话中告诉 Agent。")


def memory_log_command(session, arg: str) -> None:
    changes = session.memory_editor.changes(None if arg == "all" or not session.task else session.task.id)
    if not changes:
        print("没有记忆修改记录。")
    for c in changes[:30]:
        state = f"{DIM}（已撤销）{RESET}" if c["undone"] else ""
        print(f"[{c['id']}] {c['time']} · {TRIGGER_LABELS.get(c['trigger'], c['trigger'])} · {summarize_change(c)}{state}")


def project_memory_command(session, arg: str) -> None:
    """/project-memory 列表；<序号> 查看全文；edit <序号> <内容> 编辑；del <序号|all> 删除。"""
    memory, editor = session.project_memory, session.memory_editor
    task = session.task.id if session.task else ""
    if arg.startswith("edit"):
        index, content = _split_edit(arg[4:].strip())
        editor.update("project", index, content, trigger="user", task=task)
        print("已修改（memory 子 Agent 不会再改动这条记忆）。")
        return
    if arg.startswith("del"):
        target = arg[3:].strip()
        if not target:
            raise ValueError("用法：/project-memory del <序号|all>")
        indexes = range(len(memory.entries()), 0, -1) if target == "all" else [int(target)]
        for index in indexes:
            editor.delete("project", index, trigger="user", task=task)
        print(f"已删除，剩余 {len(memory.entries())} 条项目记忆。")
        return
    entries = memory.entries()
    if arg:
        e = memory.at(int(arg))
        print(f"[{e['time']}] {e['label']} · 会话 {e['task']}\n{e['content']}")
        return
    if not entries:
        print("项目记忆为空：归档会话时会自动写入会话摘要，也可以在对话中要求记录项目约定。")
    for i, e in enumerate(entries, 1):
        mark = "（用户编辑）" if e["by_user"] else ""
        print(f"{i}. [{e['time']}] {e['label']}{mark} · 会话 {e['task']}  {DIM}{short(e['content'].replace(chr(10), ' '), 100)}{RESET}")


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
    elif text == "/model" or text.startswith("/model "):
        args = text.split()[1:]
        if args:
            role, model = (args[0], args[1]) if len(args) > 1 else ("main", args[0])
            session.set_model(role, model)
            print(f"{GREEN}{role} 已切换为 {model}，下一轮生效{RESET}")
        for role, options in session.model_options().items():
            marks = " · ".join(f"{GREEN}{m}{RESET}" if m == session.models[role] else m for m in options)
            print(f"  {role:<9} {marks}")
    elif text == "/approvals":
        print_approvals(session.approval_log)
    elif text.startswith(("/remember", "/forget")) or text == "/memory" or text.startswith("/memory edit"):
        preferences_command(session, text)
    elif text == "/memory-log" or text.startswith("/memory-log "):
        memory_log_command(session, text[len("/memory-log"):].strip())
    elif text.startswith("/memory-undo"):
        change = session.memory_editor.undo(text[len("/memory-undo"):].strip())
        print(f"已撤销：{summarize_change(change)}")
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
    eval_p.add_argument("--project", action="append", help="只运行指定测试项目的任务，可多次指定")
    eval_p.add_argument("--task", action="append", help="只运行指定任务，可多次指定")
    eval_p.add_argument("--repeat", type=int, default=1, help="每个任务的运行次数")
    eval_p.add_argument("--save-baseline", action="store_true", help="同时保存为对比基线")
    eval_p.add_argument("--list", action="store_true", help="按项目列出评估任务，不运行")
    args = parser.parse_args(argv)

    settings = load_settings()
    if args.command == "reset":
        print(f"已还原: {reset_repo(settings, args.name)}")
    elif args.command == "list":
        for name in list_samples(settings):
            info = sample_info(settings, name)
            state = "已生成" if is_generated(settings, name) else "未生成"
            print(f"{name:<12}{info['title']}（{state}）\n{'':<12}{info['summary']}")
    elif args.command == "sessions":
        from ..session import list_sessions

        rows = list_sessions(settings, args.repo)
        for s in rows:
            first = (s.get("requests") or ["（无）"])[0]
            print(f"{s['task_id']}  保存于 {s['saved_at']}  模式 {s['mode']}  已提交 {s['rounds']} 轮  首条需求：{short(first, 60)}")
        print("" if rows else "没有可恢复的会话。", end="")
    elif args.command == "eval":
        from ..metrics.evaluation import EVAL_PROJECTS, TASK_IDS, TASKS, run_eval, select_tasks

        unknown = set(args.task or []) - set(TASK_IDS)
        if unknown:
            parser.error(f"未知任务 {sorted(unknown)}，可选: {TASK_IDS}")
        unknown = set(args.project or []) - set(EVAL_PROJECTS)
        if unknown:
            parser.error(f"没有评估任务的项目 {sorted(unknown)}，可选: {EVAL_PROJECTS}")
        if args.list:
            for repo in EVAL_PROJECTS:
                print(repo)
                for t in TASKS:
                    if t.repo == repo:
                        print(f"  {t.id:<21}{t.title}：{t.prompt}")
        elif not select_tasks(args.task, args.project):
            parser.error("指定的任务不属于指定的项目")
        else:
            run_eval(settings, args.task, max(args.repeat, 1), args.save_baseline, args.project)
    elif args.web:
        from .web.server import serve

        serve(args.repo, args.port, args.mode)
    else:
        repl(args.repo, args.mode, args.resume)


if __name__ == "__main__":
    main()

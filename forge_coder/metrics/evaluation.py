"""评估集：固定任务 + 自动判定，记录成功与否、步数、token 与耗时，用于对比优化前后的效果。

任务按测试项目组织（EvalTask.repo），每个任务都从还原后的对应测试项目开始，在新会话中运行（白名单外命令一律拒绝，无需人工交互），
运行结束后在任务 worktree 上执行判定，然后丢弃任务。结果保存在 data/eval/。

    forge-coder eval                      运行全部任务
    forge-coder eval --project library    只运行指定项目的任务（可多次指定）
    forge-coder eval --task fix-tests     只运行指定任务（可多次指定）
    forge-coder eval --repeat 2           每个任务重复运行，取平均
    forge-coder eval --save-baseline      同时保存为基线（只替换本次运行的任务），后续运行自动与基线对比
"""

import asyncio
from dataclasses import dataclass
from datetime import datetime
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Callable

from forge_agents.core.streaming import StreamEventType

from .. import workspace as ws
from ..config import Settings

MAIN_AGENT = "forge-coder"
Check = Callable[[Path, str, str], tuple[bool, str]]  # (worktree, 最终回答, 任务基准提交) -> (是否成功, 说明)


@dataclass(frozen=True)
class EvalTask:
    id: str
    title: str
    prompt: str
    check: Check
    repo: str = "inventory"  # 所属测试项目
    paradigm: str = "react"  # Agent 范式：react 或 plansolve


# ---------------------------------------------------------------- 判定辅助


def _run(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    env = dict(os.environ, PYTHONUTF8="1", PYTHONDONTWRITEBYTECODE="1")
    return subprocess.run(
        [sys.executable, *args], cwd=cwd, env=env, capture_output=True, text=True, encoding="utf-8"
    )


def _pytest(cwd: Path, *targets: str) -> subprocess.CompletedProcess:
    return _run(cwd, "-m", "pytest", "-q", "-p", "no:cacheprovider", *targets)


def _last_line(proc: subprocess.CompletedProcess) -> str:
    lines = [l for l in (proc.stdout + proc.stderr).splitlines() if l.strip()]
    return lines[-1] if lines else ""


def _tests_unchanged(path: Path, base: str) -> bool:
    return not ws.git(path, "diff", base, "--", "tests").strip()


def _code_unchanged(path: Path, base: str) -> bool:
    ws.git(path, "add", "-A")
    return not ws.git(path, "diff", "--cached", base).strip()


def _script(path: Path, code: str) -> tuple[bool, str]:
    proc = _run(path, "-c", code)
    return proc.returncode == 0, _last_line(proc)


def _test_count(path: Path) -> int:
    return sum(f.read_text(encoding="utf-8").count("\ndef test_") for f in (path / "tests").glob("test_*.py"))


# ---------------------------------------------------------------- 判定规则


def check_explain(path: Path, answer: str, base: str) -> tuple[bool, str]:
    missing = [m for m in ("store", "pricing", "report", "cli") if m not in answer.lower()]
    return not missing, "覆盖全部模块" if not missing else f"回答未提到: {missing}"


def check_locate(path: Path, answer: str, base: str) -> tuple[bool, str]:
    ok = "store.py" in answer and "27" in answer
    return ok, "定位正确" if ok else "回答中没有 store.py:27"


def check_fix_tests(path: Path, answer: str, base: str) -> tuple[bool, str]:
    if ws.git(path, "diff", base, "--", "tests").strip():
        return False, "修改了测试文件"
    proc = _pytest(path)
    return proc.returncode == 0, _last_line(proc)


_CATEGORY_CHECK = (
    "from inventory.cli import demo_inventory\n"
    "from inventory.report import category_summary\n"
    "result = category_summary(demo_inventory())\n"
    "expected = {'外设': 5175.0, '配件': 1393.0}\n"
    "assert set(result) == set(expected), result\n"
    "assert all(abs(result[k] - v) < 0.01 for k, v in expected.items()), result\n"
)


def check_feature(path: Path, answer: str, base: str) -> tuple[bool, str]:
    proc = _run(path, "-c", _CATEGORY_CHECK)
    if proc.returncode != 0:
        return False, f"功能校验失败: {_last_line(proc)}"
    test_file = path / "tests" / "test_report.py"
    if "category_summary" not in test_file.read_text(encoding="utf-8"):
        return False, "未补充 category_summary 的测试"
    proc = _pytest(path, "tests/test_report.py")
    return proc.returncode == 0, _last_line(proc)


# library：失败测试的现象与根源在不同文件
_LIBRARY_ROOTS = (
    "from datetime import date\n"
    "from library.dates import days_overdue\n"
    "from library.catalog import Book, Catalog\n"
    "from library.members import Member, borrow_limit\n"
    "assert days_overdue(date(2024, 3, 15), date(2024, 3, 17)) == 2, '逾期天数仍不正确'\n"
    "assert borrow_limit(Member.from_record({'id': 'x', 'name': 'x', 'level': 'PREMIUM'})) == 6, '会员等级未规范化'\n"
    "from library.loans import Loan\n"
    "c = Catalog(); c.add_book(Book('b', 't', 'a', 1))\n"
    "back = Loan(1, 'b', 'x', date(2024, 3, 1), date(2024, 3, 15), returned=date(2024, 3, 2))\n"
    "assert c.available_copies('b', [back]) == 1, '已归还的借阅仍占用副本'\n"
)


def check_library_fix(path: Path, answer: str, base: str) -> tuple[bool, str]:
    if not _tests_unchanged(path, base):
        return False, "修改了测试文件"
    ok, detail = _script(path, _LIBRARY_ROOTS)  # 在根源处修复，而不是在调用方绕过
    if not ok:
        return False, f"根源未修复: {detail}"
    proc = _pytest(path)
    return proc.returncode == 0, _last_line(proc)


def check_library_locate(path: Path, answer: str, base: str) -> tuple[bool, str]:
    if not _code_unchanged(path, base):
        return False, "要求不改代码，但产生了改动"
    ok = "dates.py" in answer and "days_overdue" in answer
    return ok, "定位到 dates.py:days_overdue" if ok else "回答中没有指出 dates.py 的 days_overdue"


# textstats：重构前后 analyze / compare 的结果必须一致
_TEXTSTATS_SAME = (
    "from textstats.analyzer import analyze, compare\n"
    "from textstats.report import format_report\n"
    "text = 'The quick brown fox jumps over the lazy dog. The dog sleeps!\\nIs the fox quick? Yes, the fox is very quick.'\n"
    "assert analyze(text) == {'chars': 106, 'chars_no_spaces': 85, 'words': 22, 'sentences': 4, 'avg_word_length': 3.64, "
    "'avg_sentence_words': 5.5, 'top_words': [('fox', 3), ('quick', 3), ('dog', 2)], 'reading_minutes': 1}, analyze(text)\n"
    "assert analyze('Hello world.\\n\\nNew line here!  And more') == {'chars': 38, 'chars_no_spaces': 30, 'words': 7, "
    "'sentences': 3, 'avg_word_length': 4.0, 'avg_sentence_words': 2.33, "
    "'top_words': [('hello', 1), ('here', 1), ('line', 1)], 'reading_minutes': 1}\n"
    "assert compare('the cat sat on the mat. a dog sat too', 'the dog ran to the mat') == "
    "{'common': ['dog', 'mat'], 'only_a': ['cat', 'sat', 'too'], 'only_b': ['ran'], 'similarity': 0.33}\n"
    "assert analyze('')['words'] == 0 and '高频词' in format_report(analyze(text))\n"
)


def check_textstats_refactor(path: Path, answer: str, base: str) -> tuple[bool, str]:
    ok, detail = _script(path, _TEXTSTATS_SAME)
    if not ok:
        return False, f"重构改变了结果: {detail}"
    source = (path / "textstats" / "analyzer.py").read_text(encoding="utf-8")
    if source.count("\ndef ") < 5:
        return False, "analyzer.py 仍未拆分为多个函数"
    if _test_count(path) < 6:
        return False, f"测试只有 {_test_count(path)} 个，补充不足"
    proc = _pytest(path)
    return proc.returncode == 0, _last_line(proc)


_TEXTSTATS_STOPWORDS = (
    "from textstats.analyzer import analyze\n"
    "text = 'The quick brown fox jumps over the lazy dog. The dog sleeps! Is the fox quick? Yes, the fox is very quick.'\n"
    "plain, custom = analyze(text), analyze(text, stopwords={'fox', 'quick'})\n"
    "assert [w for w, _ in custom['top_words']] == ['dog', 'brown', 'jumps'], custom['top_words']\n"
    "assert {k: v for k, v in custom.items() if k != 'top_words'} == {k: v for k, v in plain.items() if k != 'top_words'}\n"
)


def check_textstats_stopwords(path: Path, answer: str, base: str) -> tuple[bool, str]:
    ok, detail = _script(path, _TEXTSTATS_STOPWORDS)
    if not ok:
        return False, f"功能校验失败: {detail}"
    if "stopwords" not in "".join(f.read_text(encoding="utf-8") for f in (path / "tests").glob("test_*.py")):
        return False, "未补充 stopwords 的测试"
    proc = _pytest(path)
    return proc.returncode == 0, _last_line(proc)


# todoapp：两个入口都要实际可用
_TODOAPP_CLI = (
    "import tempfile, io, contextlib, pathlib\n"
    "from todoapp import cli\n"
    "f = str(pathlib.Path(tempfile.mkdtemp()) / 't.json')\n"
    "for args in (['add', '买牛奶'], ['add', '写周报'], ['done', '1']): cli.main(['--file', f, *args])\n"
    "out = io.StringIO()\n"
    "with contextlib.redirect_stdout(out): cli.main(['--file', f, 'list', '--status', 'done'])\n"
    "assert '买牛奶' in out.getvalue() and '写周报' not in out.getvalue(), out.getvalue()\n"
)


def check_todoapp_verify(path: Path, answer: str, base: str) -> tuple[bool, str]:
    proc = _run(path, "-m", "todoapp.web", "--selftest")
    if proc.returncode != 0:
        return False, f"网页接口自检未通过: {_last_line(proc)}"
    ok, detail = _script(path, _TODOAPP_CLI)
    if not ok:
        return False, f"命令行筛选仍不正确: {detail}"
    if len(list((path / "tests").glob("test_*.py"))) < 2:
        return False, "未给命令行或网页接口补充测试"
    proc = _pytest(path)
    return proc.returncode == 0, _last_line(proc)


TASKS = [
    EvalTask("explain", "解释项目结构", "介绍一下这个项目的结构和各模块的职责", check_explain),
    EvalTask("locate", "定位函数", "remove_stock 定义在哪个文件的哪一行？它的实现和测试期望是否一致？", check_locate),
    EvalTask("fix-tests", "修复失败测试", "运行测试，并修复所有失败的测试（不要修改测试文件）", check_fix_tests),
    EvalTask("feature", "新增功能", '实现 README 待办中的"按类别汇总库存价值"功能，并补充相应测试', check_feature),
    EvalTask("library-fix", "跨文件修复", "运行测试，找出失败的根本原因并修复（不要修改测试文件）", check_library_fix, repo="library"),
    EvalTask("library-locate", "跨文件定位", "逾期 3 天的罚金算出来是 2 元，帮我找出根本原因在哪个文件的哪个函数，先不要改代码",
             check_library_locate, repo="library"),
    EvalTask("textstats-refactor", "重构并补测试",
             "按 README 待办重构 analyzer.py 并补充测试。保持现有行为不变，即使发现不一致的地方也先不要修改，在回答中说明即可",
             check_textstats_refactor, repo="textstats"),
    EvalTask("textstats-stopwords", "新增参数", "给 analyze 增加 stopwords 参数：传入的词不计入高频词，其他统计不变，并补充测试",
             check_textstats_stopwords, repo="textstats"),
    EvalTask("todoapp-verify", "运行验证", "按 README 待办实际运行命令行和网页接口，修复发现的问题并补充测试",
             check_todoapp_verify, repo="todoapp"),
]
TASK_IDS = [t.id for t in TASKS]
EVAL_PROJECTS = list(dict.fromkeys(t.repo for t in TASKS))


# ---------------------------------------------------------------- 运行


def run_task(settings: Settings, task: EvalTask) -> dict:
    from ..session import CoderSession

    ws.reset_repo(settings, task.repo)
    session = CoderSession(settings, task.repo)  # 无审批函数：白名单外命令一律拒绝
    final = {"result": "", "status": "failed", "steps": 0}
    error = None

    async def consume():
        async for event in session.stream(task.prompt, task.paradigm):
            if event.type == StreamEventType.AGENT_FINISH and event.agent_name == MAIN_AGENT:
                final.update(result=event.data.get("result") or "", status=event.data.get("status"),
                             steps=event.data.get("total_steps") or 0)

    started = time.time()
    try:
        asyncio.run(consume())
    except Exception as exc:  # 运行失败也记录用量与判定结果
        error = f"{type(exc).__name__}: {exc}"
    outcome = session.last_outcome or {}
    if not error and outcome.get("status") == "failed":  # 会话把本轮失败归类到 last_outcome，不再抛出
        error = outcome.get("detail") or outcome.get("reason")
    elapsed = time.time() - started

    success, detail = False, error or "未创建任务"
    # 归档会清空会话的本轮用量与上下文（CoderSession._clear），先取出
    usage, context = session.usage.last, session.last_context or {}
    if session.task is not None:
        success, detail = task.check(session.task.path, final["result"], session.task.base)
        if error:
            success, detail = False, f"{error}；{detail}"
        session.archive(summarize=False)  # 评估不生成会话摘要
    return {
        "task": task.id,
        "project": task.repo,
        "success": success,
        "detail": detail,
        "status": final["status"],
        "main_steps": final["steps"],
        "model_calls": usage.model_calls if usage else 0,
        "total_tokens": usage.total_tokens if usage else 0,
        "by_agent": usage.to_dict()["by_agent"] if usage else {},
        "elapsed_s": round(elapsed, 1),
        "context_tokens": context.get("used", 0),  # 最后一次请求的上下文大小
    }


def summarize(results: list[dict]) -> dict:
    per_task = {}
    for task_id in dict.fromkeys(r["task"] for r in results):
        rows = [r for r in results if r["task"] == task_id]
        n = len(rows)
        per_task[task_id] = {
            "project": rows[0].get("project", "inventory"),
            "runs": n,
            "success_rate": sum(r["success"] for r in rows) / n,
            "avg_tokens": round(sum(r["total_tokens"] for r in rows) / n),
            "avg_model_calls": round(sum(r["model_calls"] for r in rows) / n, 1),
            "avg_elapsed_s": round(sum(r["elapsed_s"] for r in rows) / n, 1),
        }
    n = len(results) or 1
    return {
        "success_rate": sum(r["success"] for r in results) / n,
        "total_tokens": sum(r["total_tokens"] for r in results),
        "avg_tokens": round(sum(r["total_tokens"] for r in results) / n),
        "avg_model_calls": round(sum(r["model_calls"] for r in results) / n, 1),
        "avg_elapsed_s": round(sum(r["elapsed_s"] for r in results) / n, 1),
        "per_task": per_task,
    }


def _pct(new, old) -> str:
    return f"{(new - old) / old:+.0%}" if old else "-"


def print_report(report: dict, baseline: dict | None) -> None:
    s = report["summary"]
    print(f"\n评估结果 · 主模型 {report['model']} · 子 Agent 模型 {report.get('subagent_models', '-')} · {report['created_at']}")
    print(f"{'项目':<11}{'任务':<21}{'成功率':>8}{'平均 tokens':>14}{'模型调用':>10}{'耗时(s)':>10}  对比基线(tokens)")
    base_tasks = (baseline or {}).get("summary", {}).get("per_task", {})
    for task_id, t in s["per_task"].items():
        b = base_tasks.get(task_id)
        delta = _pct(t["avg_tokens"], b["avg_tokens"]) if b else "-"
        print(f"{t['project']:<11}{task_id:<21}{t['success_rate']:>8.0%}{t['avg_tokens']:>14}{t['avg_model_calls']:>10}{t['avg_elapsed_s']:>10}  {delta}")
    print(f"{'合计':<32}{s['success_rate']:>8.0%}{s['avg_tokens']:>14}{s['avg_model_calls']:>10}{s['avg_elapsed_s']:>10}")
    for r in report["results"]:
        mark = "✔" if r["success"] else "✘"
        shares = " ".join(f"{n}:{a['tokens']}" for n, a in r["by_agent"].items())
        print(f"  {mark} {r['task']:<21} {r['detail'][:60]}  [{shares}]")


def select_tasks(task_ids: list[str] | None = None, projects: list[str] | None = None) -> list[EvalTask]:
    return [t for t in TASKS if (not task_ids or t.id in task_ids) and (not projects or t.repo in projects)]


def run_eval(settings: Settings, task_ids: list[str] | None, repeat: int, save_baseline: bool,
             projects: list[str] | None = None) -> dict:
    tasks = select_tasks(task_ids, projects)
    results = []
    for task in tasks:
        for i in range(repeat):
            print(f"▶ 运行 [{task.repo}] {task.id}（{task.title}）第 {i + 1}/{repeat} 次 …", flush=True)
            results.append(run_task(settings, task))
    for repo in dict.fromkeys(t.repo for t in tasks):
        ws.reset_repo(settings, repo)  # 评估结束后还原用到的测试项目

    report = {
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "model": settings.model,
        "subagent_models": settings.role_models,
        "repeat": repeat,
        "results": results,
        "summary": summarize(results),
    }
    out_dir = settings.data_dir / "eval"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / f"{datetime.now():%Y%m%d-%H%M%S}.json"
    out_file.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    baseline_file = out_dir / "baseline.json"
    baseline = json.loads(baseline_file.read_text(encoding="utf-8")) if baseline_file.exists() else None
    print_report(report, None if save_baseline else baseline)
    if save_baseline:
        # 只替换本次运行的任务：分项目运行时不覆盖其他项目的基线
        ran = {r["task"] for r in results}
        kept = [r for r in (baseline or {}).get("results", []) if r["task"] not in ran]
        merged = {**report, "results": kept + results, "summary": summarize(kept + results)}
        baseline_file.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n已保存为基线: {baseline_file}")
    print(f"结果文件: {out_file}")
    return report

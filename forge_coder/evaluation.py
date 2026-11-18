"""评估集：固定任务 + 自动判定，记录成功与否、步数、token 与耗时，用于对比优化前后的效果。

每个任务都从还原后的测试项目开始，在新会话中运行（白名单外命令一律拒绝，无需人工交互），
运行结束后在任务 worktree 上执行判定，然后丢弃任务。结果保存在 data/eval/。

    forge-coder eval                      运行全部任务
    forge-coder eval --task fix-tests     只运行指定任务（可多次指定）
    forge-coder eval --repeat 2           每个任务重复运行，取平均
    forge-coder eval --save-baseline      同时保存为基线，后续运行自动与基线对比
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

from . import workspace as ws
from .config import Settings

REPO = "inventory"
MAIN_AGENT = "forge-coder"
Check = Callable[[Path, str, str], tuple[bool, str]]  # (worktree, 最终回答, 任务基准提交) -> (是否成功, 说明)


@dataclass(frozen=True)
class EvalTask:
    id: str
    title: str
    prompt: str
    check: Check
    mode: str = "react"


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


TASKS = [
    EvalTask("explain", "解释项目结构", "介绍一下这个项目的结构和各模块的职责", check_explain),
    EvalTask("locate", "定位函数", "remove_stock 定义在哪个文件的哪一行？它的实现和测试期望是否一致？", check_locate),
    EvalTask("fix-tests", "修复失败测试", "运行测试，并修复所有失败的测试（不要修改测试文件）", check_fix_tests),
    EvalTask("feature", "新增功能", '实现 README 待办中的"按类别汇总库存价值"功能，并补充相应测试', check_feature),
]
TASK_IDS = [t.id for t in TASKS]


# ---------------------------------------------------------------- 运行


def run_task(settings: Settings, task: EvalTask) -> dict:
    from .session import CoderSession

    ws.reset_repo(settings, REPO)
    session = CoderSession(settings, REPO)  # 无审批函数：白名单外命令一律拒绝
    final = {"result": "", "status": "failed", "steps": 0}
    error = None

    async def consume():
        async for event in session.stream(task.prompt, task.mode):
            if event.type == StreamEventType.AGENT_FINISH and event.agent_name == MAIN_AGENT:
                final.update(result=event.data.get("result") or "", status=event.data.get("status"),
                             steps=event.data.get("total_steps") or 0)

    started = time.time()
    try:
        asyncio.run(consume())
    except Exception as exc:  # 运行失败也记录用量与判定结果
        error = f"{type(exc).__name__}: {exc}"
    elapsed = time.time() - started

    success, detail = False, error or "未创建任务"
    if session.task is not None:
        success, detail = task.check(session.task.path, final["result"], session.task.base)
        if error:
            success, detail = False, f"{error}；{detail}"
        session.reject()
    usage = session.usage.last
    return {
        "task": task.id,
        "success": success,
        "detail": detail,
        "status": final["status"],
        "main_steps": final["steps"],
        "model_calls": usage.model_calls if usage else 0,
        "total_tokens": usage.total_tokens if usage else 0,
        "by_agent": usage.to_dict()["by_agent"] if usage else {},
        "elapsed_s": round(elapsed, 1),
    }


def summarize(results: list[dict]) -> dict:
    per_task = {}
    for task_id in dict.fromkeys(r["task"] for r in results):
        rows = [r for r in results if r["task"] == task_id]
        n = len(rows)
        per_task[task_id] = {
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
    print(f"\n评估结果 · 模型 {report['model']} · {report['created_at']}")
    print(f"{'任务':<10}{'成功率':>8}{'平均 tokens':>14}{'模型调用':>10}{'耗时(s)':>10}  对比基线(tokens)")
    base_tasks = (baseline or {}).get("summary", {}).get("per_task", {})
    for task_id, t in s["per_task"].items():
        b = base_tasks.get(task_id)
        delta = _pct(t["avg_tokens"], b["avg_tokens"]) if b else "-"
        print(f"{task_id:<10}{t['success_rate']:>8.0%}{t['avg_tokens']:>14}{t['avg_model_calls']:>10}{t['avg_elapsed_s']:>10}  {delta}")
    print(f"{'合计':<10}{s['success_rate']:>8.0%}{s['avg_tokens']:>14}{s['avg_model_calls']:>10}{s['avg_elapsed_s']:>10}")
    for r in report["results"]:
        mark = "✔" if r["success"] else "✘"
        shares = " ".join(f"{n}:{a['tokens']}" for n, a in r["by_agent"].items())
        print(f"  {mark} {r['task']:<10} {r['detail'][:60]}  [{shares}]")


def run_eval(settings: Settings, task_ids: list[str] | None, repeat: int, save_baseline: bool) -> dict:
    from .config import create_llm

    tasks = [t for t in TASKS if not task_ids or t.id in task_ids]
    results = []
    for task in tasks:
        for i in range(repeat):
            print(f"▶ 运行 {task.id}（{task.title}）第 {i + 1}/{repeat} 次 …", flush=True)
            results.append(run_task(settings, task))
    ws.reset_repo(settings, REPO)  # 评估结束后还原测试项目

    report = {
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "model": create_llm(settings).model,
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
        baseline_file.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n已保存为基线: {baseline_file}")
    print(f"结果文件: {out_file}")
    return report

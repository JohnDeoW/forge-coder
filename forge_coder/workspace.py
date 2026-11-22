"""测试项目管理：

- 从 samples/ 模板生成 repos/ 下的本地 git 仓库，并支持一键还原；模板说明 SAMPLE.toml（简介、侧重能力、
  预置问题与示例需求）只供面板与评估使用，不复制到仓库，Agent 看不到；
- 每个任务一个分支 + worktree，Agent 只在 worktree 中改动，用户审查后合并或丢弃。
"""

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import os
import re
import shutil
import stat
import subprocess
import tomllib

from .config import Settings

BASELINE_TAG = "baseline"
MAIN_BRANCH = "main"
SAMPLE_INFO = "SAMPLE.toml"
_IGNORE = shutil.ignore_patterns("__pycache__", ".pytest_cache", ".pytest-tmp", "*.pyc", SAMPLE_INFO)
# 框架文件工具编辑前会在同目录生成 .backups/；改动已由 git 记录，这些文件不纳入版本管理
_EXCLUDES = [".backups/", "__pycache__/", ".pytest_cache/", ".pytest-tmp/", "*.pyc"]


class WorkspaceError(RuntimeError):
    pass


def git(cwd: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, encoding="utf-8"
    )
    if proc.returncode != 0:
        raise WorkspaceError(f"git {' '.join(args)} 失败: {proc.stderr.strip()}")
    return proc.stdout


def _force_remove(path: Path) -> None:
    """删除目录；git 对象文件在 Windows 上是只读的，需要先去掉只读属性。"""

    def onexc(func, p, _exc):
        os.chmod(p, stat.S_IWRITE)
        func(p)

    if path.exists():
        shutil.rmtree(path, onexc=onexc)


def _clear_dir(path: Path) -> None:
    """清空目录内容但保留目录本身：终端等进程以该目录为当前目录时，Windows 不允许删除目录。"""
    path.mkdir(parents=True, exist_ok=True)
    for child in path.iterdir():
        if child.is_dir() and not child.is_symlink():
            _force_remove(child)
        else:
            os.chmod(child, stat.S_IWRITE)
            child.unlink()


def list_samples(settings: Settings) -> list[str]:
    return sorted(p.name for p in settings.samples_dir.iterdir() if p.is_dir())


def repo_path(settings: Settings, name: str) -> Path:
    return settings.repos_dir / name


def is_generated(settings: Settings, name: str) -> bool:
    return (repo_path(settings, name) / ".git").exists()


def sample_info(settings: Settings, name: str) -> dict:
    """模板说明：title / summary / focus / prompts / issues（预置问题：title、symptom、cause）。"""
    file = settings.samples_dir / name / SAMPLE_INFO
    info = tomllib.loads(file.read_text(encoding="utf-8")) if file.is_file() else {}
    return {"title": info.get("title") or name, "summary": info.get("summary") or "", "focus": info.get("focus") or "",
            "prompts": info.get("prompts") or [], "issues": info.get("issues") or []}


def reset_repo(settings: Settings, name: str) -> Path:
    """把测试项目还原为模板初始状态：清除 worktree、任务分支与运行数据后重新生成。"""
    template = settings.samples_dir / name
    if not template.is_dir():
        raise WorkspaceError(f"没有名为 {name} 的模板，可选: {list_samples(settings)}")

    _force_remove(settings.worktrees_dir / name)
    _force_remove(settings.data_dir / "todos" / name)
    _force_remove(settings.data_dir / "approvals" / name)
    _force_remove(settings.data_dir / "sessions" / name)
    _force_remove(settings.data_dir / "traces" / name)
    # 项目记忆描述的是被还原掉的改动，随代码一起回到初始状态（修改记录一并删除）
    (settings.data_dir / "memory" / "projects" / f"{name}.sqlite").unlink(missing_ok=True)
    from .memory.editor import drop_project_changes

    drop_project_changes(settings.data_dir, name)
    target = repo_path(settings, name)
    _clear_dir(target)

    shutil.copytree(template, target, ignore=_IGNORE, dirs_exist_ok=True)
    git(target, "init", "-q", "-b", MAIN_BRANCH)
    git(target, "config", "user.name", "ForgeCoder")
    git(target, "config", "user.email", "forge-coder@localhost")
    git(target, "config", "core.quotepath", "false")  # diff 中的中文路径不转义
    # info/exclude 位于主仓库 .git 中，对所有 worktree 生效
    (target / ".git" / "info" / "exclude").write_text("\n".join(_EXCLUDES) + "\n", encoding="utf-8")
    git(target, "add", "-A")
    git(target, "commit", "-q", "-m", "baseline: 初始测试项目")
    git(target, "tag", BASELINE_TAG)
    return target


def ensure_repo(settings: Settings, name: str) -> Path:
    """测试项目不存在时从模板生成，已存在则保持原状。"""
    target = repo_path(settings, name)
    if not (target / ".git").exists():
        return reset_repo(settings, name)
    return target


# ---------------------------------------------------------------- 任务 worktree


@dataclass
class Task:
    id: str
    repo: Path  # 主仓库 repos/<项目>
    path: Path  # worktree 目录
    base: str  # 最近一次合并（或创建任务）时 main 的提交：diff / 提交记录只显示尚未合并的改动
    rounds: int = 0
    origin: str = ""  # 创建任务时 main 的提交：用于汇总整个会话的改动

    def __post_init__(self):
        self.origin = self.origin or self.base

    @property
    def branch(self) -> str:
        return f"forge/{self.id}"


def create_task(settings: Settings, name: str) -> Task:
    """从 main 创建任务分支与 worktree。"""
    repo = ensure_repo(settings, name)
    task_id = "task-" + datetime.now().strftime("%Y%m%d-%H%M%S")
    path = settings.worktrees_dir / name / task_id
    path.parent.mkdir(parents=True, exist_ok=True)
    base = git(repo, "rev-parse", MAIN_BRANCH).strip()
    git(repo, "worktree", "add", "-q", "-b", f"forge/{task_id}", str(path), MAIN_BRANCH)
    return Task(task_id, repo, path, base)


def commit_round(task: Task, request: str) -> str | None:
    """把本轮改动提交到任务分支；没有改动时返回 None。"""
    git(task.path, "add", "-A")
    if not git(task.path, "status", "--porcelain").strip():
        return None
    task.rounds += 1
    summary = " ".join(request.split())[:60]
    git(task.path, "commit", "-q", "-m", f"第 {task.rounds} 轮: {summary}")
    return git(task.path, "rev-parse", "--short", "HEAD").strip()


def task_diff(task: Task) -> str:
    """任务相对创建时 main 的全部改动（含尚未提交的修改）。"""
    git(task.path, "add", "-A")
    return git(task.path, "diff", "--cached", task.base)


def session_diff(task: Task) -> str:
    """整个会话（从创建任务起，含已合并部分与未提交修改）的改动，供会话摘要使用。"""
    git(task.path, "add", "-A")
    return git(task.path, "diff", "--cached", task.origin)


def task_log(task: Task) -> str:
    return git(task.path, "log", "--stat", "--format=%h %s", f"{task.base}..HEAD")


def task_commits(task: Task) -> list[dict]:
    """任务分支上的逐轮提交（新到旧）：提交号、说明、时间与增删行数。"""
    out = git(task.path, "log", "--format=@@%h|%cd|%s", "--date=format:%m-%d %H:%M", "--shortstat", f"{task.base}..HEAD")
    commits = []
    for block in out.split("@@")[1:]:
        head, _, stat = block.partition("\n")
        commit, when, subject = head.split("|", 2)
        added = re.search(r"(\d+) insertion", stat)
        deleted = re.search(r"(\d+) deletion", stat)
        commits.append({"commit": commit, "subject": subject, "time": when,
                        "added": int(added.group(1)) if added else 0, "deleted": int(deleted.group(1)) if deleted else 0})
    return commits


def task_changed(task: Task) -> bool:
    return bool(task_diff(task).strip())


def _remove_task(task: Task) -> None:
    git(task.repo, "worktree", "remove", "--force", str(task.path))
    git(task.repo, "branch", "-D", task.branch)


def accept_task(task: Task) -> str:
    """合并任务分支回 main，并清理 worktree 与分支；返回合并后的提交。"""
    commit_round(task, "审查前的未提交改动")
    git(task.repo, "merge", "-q", "--no-ff", task.branch, "-m", f"合并任务 {task.id}")
    _remove_task(task)
    return git(task.repo, "rev-parse", "--short", "HEAD").strip()


def merge_task(task: Task) -> str:
    """把任务分支上尚未合并的改动合并回 main，任务继续：分支快进到合并提交，后续改动从这里开始计算。"""
    commit_round(task, "合并前的未提交改动")
    if not git(task.path, "log", "--oneline", f"{task.base}..HEAD").strip():
        raise WorkspaceError("没有尚未合并的改动")
    git(task.repo, "merge", "-q", "--no-ff", task.branch, "-m", f"合并任务 {task.id}（第 {task.rounds} 轮）")
    git(task.path, "merge", "-q", "--ff-only", MAIN_BRANCH)
    task.base = git(task.path, "rev-parse", "HEAD").strip()
    return task.base[:7]


def discard_changes(task: Task) -> None:
    """丢弃尚未合并的改动：任务分支回到最近一次合并（或创建任务）时的状态，任务继续。"""
    git(task.path, "reset", "-q", "--hard", task.base)
    git(task.path, "clean", "-q", "-fd")


def reject_task(task: Task) -> None:
    """丢弃任务：删除 worktree 与分支，main 不受影响。"""
    _remove_task(task)


def head(task: Task) -> str:
    return git(task.path, "rev-parse", "HEAD").strip()


def reset_to(task: Task, commit: str, rounds: int) -> str:
    """回溯：任务分支回到指定提交（某一轮开始前的状态），丢弃之后的提交与未提交修改，返回短提交号。"""
    git(task.path, "reset", "-q", "--hard", commit)
    git(task.path, "clean", "-q", "-fd")  # 清掉之后新建的未跟踪文件（.backups 等已在 exclude 中，不受影响）
    task.rounds = rounds
    return commit[:7]


def load_task(settings: Settings, name: str, task_id: str, base: str, rounds: int, origin: str = "") -> Task:
    """恢复会话时重新接回已有的任务 worktree；worktree 或分支不存在时报错。"""
    repo = repo_path(settings, name)
    path = settings.worktrees_dir / name / task_id
    task = Task(task_id, repo, path, base, rounds, origin)
    if not path.is_dir():
        raise WorkspaceError(f"任务 {task_id} 的 worktree 已不存在（可能已合并、丢弃或还原）")
    git(repo, "rev-parse", "--verify", "-q", task.branch)
    return task

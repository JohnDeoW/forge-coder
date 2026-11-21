"""运行配置：从 forge-coder 根目录加载 .env，给出各数据目录、各角色的可选模型与框架配置。

模型变量可写成逗号分隔的列表，表示该角色的可选模型范围，第一个为默认模型；
会话中可在范围内手动切换（面板顶栏、CLI /model），选择随会话保存。
"""

from dataclasses import dataclass, field
from pathlib import Path
import os

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SUBAGENT_ROLES = ("explorer", "tester")
# 可单独配置模型的角色：子 Agent（含 /review 使用的 reviewer）与程序级辅助 Agent（utility，如 auto 模式的审批）
MODEL_ROLES = SUBAGENT_ROLES + ("reviewer", "utility")
MAIN_ROLE = "main"  # 主 Agent 在模型选择中的角色名
ALL_MODEL_ROLES = (MAIN_ROLE,) + MODEL_ROLES

# 上下文窗口（token）：用于历史压缩阈值、上下文组装预算与用量展示
CONTEXT_WINDOW = 128_000
# 框架默认单次工具调用限时 30 秒（Config.tool_async_timeout），超时会中止整轮对话；
# 子 Agent 一次调用包含多轮 LLM 请求、RunCommand 最长 600 秒，因此统一放宽。
TOOL_TIMEOUT_S = 900


@dataclass(frozen=True)
class Settings:
    root: Path
    samples_dir: Path
    repos_dir: Path
    data_dir: Path
    model: str
    api_key: str
    base_url: str
    role_models: dict[str, str] = field(default_factory=dict)  # 子 Agent 角色 -> 默认模型
    model_options: dict[str, list[str]] = field(default_factory=dict)  # 角色（含 main）-> 可选模型，第一个为默认
    # 主 Agent 历史压缩：历史 token 超过上下文窗口的该比例时自动压缩，保留最近 compact_keep_rounds 轮
    compact_threshold: float = 0.8
    compact_keep_rounds: int = 2
    round_token_limit: int = 0  # 单轮 token 预算（框架 RunBudget），0 表示不限制

    @property
    def worktrees_dir(self) -> Path:
        return self.data_dir / "worktrees"

    def model_for(self, role: str | None) -> str:
        """默认模型：主 Agent 取 LLM_MODEL_ID 的第一个；子 Agent 与辅助 Agent 取 LLM_SUBAGENT_MODEL_ID 的第一个
        （未设置时同主 Agent）。各角色的具体选择在会话中切换。"""
        return self.role_models.get(role, self.model) if role and role != MAIN_ROLE else self.model

    def options_for(self, role: str) -> list[str]:
        return self.model_options.get(role) or [self.model_for(role)]


def _models(value: str | None) -> list[str]:
    """逗号分隔的模型列表（去空白、去重，保持顺序）。"""
    return list(dict.fromkeys(m.strip() for m in (value or "").split(",") if m.strip()))


def load_settings(root: Path = PROJECT_ROOT) -> Settings:
    # 显式指定路径，避免工作目录变化时读到其他项目的 .env
    load_dotenv(root / ".env", override=True)
    main = _models(os.getenv("LLM_MODEL_ID"))
    sub = _models(os.getenv("LLM_SUBAGENT_MODEL_ID")) or main
    options = {MAIN_ROLE: main, **{role: sub for role in MODEL_ROLES}}  # 子 Agent 与辅助 Agent 共用同一范围
    model = main[0] if main else ""
    return Settings(
        root=root,
        samples_dir=root / "samples",
        repos_dir=root / "repos",
        data_dir=root / "data",
        model=model,
        api_key=os.getenv("LLM_API_KEY", ""),
        base_url=os.getenv("LLM_BASE_URL", ""),
        role_models={role: options[role][0] if options[role] else model for role in MODEL_ROLES},
        model_options=options,
        compact_threshold=float(os.getenv("FORGE_COMPACT_THRESHOLD") or 0.8),
        compact_keep_rounds=int(os.getenv("FORGE_COMPACT_KEEP_ROUNDS") or 2),
        round_token_limit=int(os.getenv("FORGE_ROUND_TOKEN_LIMIT") or 0),
    )


def agent_config(settings: Settings, main: bool = False, trace_dir: Path | None = None):
    """子 Agent 与 PlanSolve 使用框架默认的压缩设置（独立的短历史）；
    主 Agent（main=True）启用智能摘要压缩，阈值与保留轮数可在 .env 配置。
    trace_dir 不为空时启用框架 TraceLogger，把运行轨迹（JSONL + HTML 报告）写入该目录。"""
    from forge_agents import Config

    extra = dict(
        enable_smart_compression=True,  # 摘要模型由 AgentComponents(summary_llm=辅助 Agent 模型) 注入
        compression_threshold=settings.compact_threshold,
        min_retain_rounds=settings.compact_keep_rounds,
        summary_max_tokens=1500,
    ) if main else {}
    if trace_dir is not None:
        extra.update(trace_enabled=True, trace_dir=str(trace_dir))
    return Config(
        tool_async_timeout=TOOL_TIMEOUT_S,
        context_window=CONTEXT_WINDOW,
        tool_output_dir=str(settings.data_dir / "tool-output"),  # 截断的完整输出不写到启动目录
        **extra,
    )


def create_llm(settings: Settings, model: str | None = None):
    """地址与密钥所有角色共用，只有模型名按角色区分。"""
    from forge_agents import ForgeAgentsLLM

    return ForgeAgentsLLM(
        model=model or settings.model, api_key=settings.api_key, base_url=settings.base_url
    )

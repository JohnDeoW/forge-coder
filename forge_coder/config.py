"""运行配置：从 forge-coder 根目录加载 .env，给出各数据目录、各角色模型与框架配置。"""

from dataclasses import dataclass, field
from pathlib import Path
import os

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SUBAGENT_ROLES = ("explorer", "tester")
# 可单独配置模型的角色：子 Agent（含 /review 使用的 reviewer）与程序级辅助 Agent（utility，如 auto 模式的审批）
MODEL_ROLES = SUBAGENT_ROLES + ("reviewer", "utility")

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
    role_models: dict[str, str] = field(default_factory=dict)  # 子 Agent 角色 -> 模型
    # 主 Agent 历史压缩：历史 token 超过上下文窗口的该比例时自动压缩，保留最近 compact_keep_rounds 轮
    compact_threshold: float = 0.8
    compact_keep_rounds: int = 2

    @property
    def worktrees_dir(self) -> Path:
        return self.data_dir / "worktrees"

    def model_for(self, role: str | None) -> str:
        """主 Agent 用 LLM_MODEL_ID；子 Agent 与辅助 Agent 依次取 LLM_<角色>_MODEL_ID、LLM_SUBAGENT_MODEL_ID、LLM_MODEL_ID。"""
        return self.role_models.get(role, self.model) if role else self.model


def load_settings(root: Path = PROJECT_ROOT) -> Settings:
    # 显式指定路径，避免工作目录变化时读到其他项目的 .env
    load_dotenv(root / ".env", override=True)
    model = os.getenv("LLM_MODEL_ID", "")
    sub_default = os.getenv("LLM_SUBAGENT_MODEL_ID") or model
    return Settings(
        root=root,
        samples_dir=root / "samples",
        repos_dir=root / "repos",
        data_dir=root / "data",
        model=model,
        api_key=os.getenv("LLM_API_KEY", ""),
        base_url=os.getenv("LLM_BASE_URL", ""),
        role_models={
            role: os.getenv(f"LLM_{role.upper()}_MODEL_ID") or sub_default for role in MODEL_ROLES
        },
        compact_threshold=float(os.getenv("FORGE_COMPACT_THRESHOLD") or 0.8),
        compact_keep_rounds=int(os.getenv("FORGE_COMPACT_KEEP_ROUNDS") or 2),
    )


def agent_config(settings: Settings, main: bool = False):
    """子 Agent 与 PlanSolve 使用框架默认的压缩设置（独立的短历史）；
    主 Agent（main=True）启用智能摘要压缩，阈值与保留轮数可在 .env 配置。"""
    from forge_agents import Config

    extra = dict(
        enable_smart_compression=True,  # 摘要模型由 AgentComponents(summary_llm=辅助 Agent 模型) 注入
        compression_threshold=settings.compact_threshold,
        min_retain_rounds=settings.compact_keep_rounds,
        summary_max_tokens=1500,
    ) if main else {}
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

"""运行配置：从 forge-coder 根目录加载 .env，并给出各数据目录。"""

from dataclasses import dataclass
from pathlib import Path
import os

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class Settings:
    root: Path
    samples_dir: Path
    repos_dir: Path
    data_dir: Path
    model: str
    api_key: str
    base_url: str

    @property
    def worktrees_dir(self) -> Path:
        return self.data_dir / "worktrees"


def load_settings(root: Path = PROJECT_ROOT) -> Settings:
    # 显式指定路径，避免工作目录变化时读到其他项目的 .env
    load_dotenv(root / ".env", override=True)
    return Settings(
        root=root,
        samples_dir=root / "samples",
        repos_dir=root / "repos",
        data_dir=root / "data",
        model=os.getenv("LLM_MODEL_ID", ""),
        api_key=os.getenv("LLM_API_KEY", ""),
        base_url=os.getenv("LLM_BASE_URL", ""),
    )


# 框架默认单次工具调用限时 30 秒（Config.tool_async_timeout），超时会中止整轮对话；
# 子 Agent 一次调用包含多轮 LLM 请求、RunCommand 最长 600 秒，因此统一放宽。
TOOL_TIMEOUT_S = 900


def agent_config():
    from forge_agents import Config

    return Config(tool_async_timeout=TOOL_TIMEOUT_S)


def create_llm(settings: Settings):
    from forge_agents import ForgeAgentsLLM

    return ForgeAgentsLLM(
        model=settings.model, api_key=settings.api_key, base_url=settings.base_url
    )

"""应用配置 - 使用 pydantic Settings 管理环境变量"""

from __future__ import annotations

from pathlib import Path

from pydantic import PostgresDsn
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    APP_NAME: str = "LoreFlow"
    APP_ENV: str = "dev"  # dev | staging | prod
    LOG_LEVEL: str = "INFO"
    WORKERS: int = 1

    PIPELINES_DIR: Path = Path(__file__).resolve().parent.parent / "pipelines"

    # Node plugins — 自定义插件目录
    PLUGINS_DIR: Path = Path(__file__).resolve().parent.parent.parent / "custom_plugins"
    PLUGINS_POLL_SECONDS: int = 3

    # Agent Skills — 符合 agentskills.io 规范的技能目录
    SKILLS_DIR: Path = Path(__file__).resolve().parent.parent / "registry" / "skills"

    # MCP — Model Context Protocol 服务器配置
    MCP_CONFIG: Path = Path(__file__).resolve().parent.parent.parent / "mcp.yml"

    # Agent 工具调用最大轮次
    AGENT_MAX_ROUNDS: int = 10

    # 上传文件 — 文本文件落盘目录（file 参数先上传后引用）
    UPLOADS_DIR: Path = Path(__file__).resolve().parent.parent.parent / "uploads"
    UPLOAD_MAX_MB: int = 20

    # LLM Provider 配置文件路径（默认: 项目根目录 providers.yml）
    PROVIDERS_FILE: Path = Path(__file__).resolve().parent.parent.parent.parent / "providers.yml"

    # Database
    POSTGRES_SERVER: str = "localhost"
    POSTGRES_PORT: int = 5432
    POSTGRES_USER: str = "postgres"
    POSTGRES_PASSWORD: str = "postgres"
    POSTGRES_DB: str = "loreflow"

    @property
    def DATABASE_URL(self) -> PostgresDsn:
        return PostgresDsn.build(
            scheme="postgresql+asyncpg",
            username=self.POSTGRES_USER,
            password=self.POSTGRES_PASSWORD,
            host=self.POSTGRES_SERVER,
            port=self.POSTGRES_PORT,
            path=self.POSTGRES_DB,
        )

    # Docker 镜像坐标（compose.prod.yml 使用）
    DOCKER_BACKEND_REPOSITORY: str = ""
    DOCKER_BACKEND_TAG: str = ""
    DOCKER_FRONTEND_REPOSITORY: str = ""
    DOCKER_FRONTEND_TAG: str = ""

    TAVILY_API_KEY: str = ""

    # 邮箱服务器
    SMTP_HOST: str = ""
    SMTP_PORT: int = 465
    SMTP_USER: str = ""
    SMTP_PASS: str = ""

    model_config = {
        "env_file": "../.env",
        "env_file_encoding": "utf-8",
        "extra": "ignore",  # 忽略 .env 中的未知字段
    }


settings = Settings()

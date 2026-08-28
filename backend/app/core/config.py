"""应用配置 - 使用 pydantic Settings 管理环境变量"""

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

    # 上传文件 — 文本文件落盘目录（file 参数先上传后引用）
    UPLOADS_DIR: Path = Path(__file__).resolve().parent.parent.parent / "uploads"
    UPLOAD_MAX_MB: int = 20

    OLLAMA_BASE_URL: str = "http://127.0.0.1:11434"
    OLLAMA_MODEL: str = "qwen2.5:latest"
    OLLAMA_TIMEOUT_SECONDS: float = 300.0

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

    model_config = {"env_file": "../.env", "env_file_encoding": "utf-8"}


settings = Settings()

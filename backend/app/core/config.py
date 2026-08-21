"""应用配置 - 使用 pydantic Settings 管理环境变量"""

from pathlib import Path

from pydantic import PostgresDsn
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    APP_NAME: str = "LoreFlow"
    APP_ENV: str = "dev"  # dev | staging | prod
    LOG_LEVEL: str = "INFO"
    WORKERS: int = 1  # 多 worker 各自 lifespan 会重复恢复未完成的 run，保持 1

    # Pipelines — 声明式编排定义目录与缺省文件（app/pipelines）
    PIPELINES_DIR: Path = Path(__file__).resolve().parent.parent / "pipelines"

    # Node plugins — 自定义插件目录：启动时加载，之后后台轮询自动重扫（@node 导入即注册）
    PLUGINS_DIR: Path = Path(__file__).resolve().parent.parent.parent / "custom_plugins"
    PLUGINS_POLL_SECONDS: int = 3  # 插件目录变更检测间隔

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

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

    # Agent Skills — 符合 agentskills.io 规范的技能目录
    SKILLS_DIR: Path = Path(__file__).resolve().parent.parent / "registry" / "skills"

    # MCP — Model Context Protocol 服务器配置
    MCP_CONFIG: Path = Path(__file__).resolve().parent.parent.parent / "mcp.json"

    # Agent 工具调用最大轮次
    AGENT_MAX_ROUNDS: int = 10

    # RAG 知识库 — 向量维度（需与 embedding 模型一致）
    EMBEDDING_DIMENSION: int = 1024

    # TEI — Text Embeddings Inference（BGE-M3 向量 + BGE-Reranker 精排）
    TEI_EMBED_URL: str = "http://localhost:8081"
    TEI_RERANK_URL: str = "http://localhost:8082"
    TEI_BATCH_SIZE: int = 16

    # Redis
    REDIS_URL: str = "redis://localhost:6379/0"
    REDIS_PASSWORD: str = ""

    # Arq — 分队列独立限流
    PARSE_QUEUE_NAME: str = "parse"
    PARSE_MAX_JOBS: int = 2
    PARSE_JOB_TIMEOUT: int = 1800
    DEFAULT_QUEUE_NAME: str = "default"
    DEFAULT_MAX_JOBS: int = 10

    # 检索参数
    RECALL_COUNT: int = 100
    VECTOR_THRESHOLD: float = 0.35
    RERANK_COUNT: int = 32
    RERANK_THRESHOLD: float = 0.5
    RRF_K: int = 60

    # 上传文件 — 文本文件落盘目录（file 参数先上传后引用）
    UPLOADS_DIR: Path = Path(__file__).resolve().parent.parent.parent / "uploads"
    UPLOAD_MAX_MB: int = 20

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

    # Sandbox
    SANDBOX_URL: str = "http://localhost:8194"

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

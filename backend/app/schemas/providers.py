"""Provider / Model / Settings 相关请求/响应 schema。"""

from datetime import datetime

from pydantic import BaseModel, Field, field_serializer

from app.models.provider import ModelRecord, ProviderRecord, mask_api_key


# ── Provider ──────────────────────────────────────────────────────────────────

class ProviderBody(BaseModel):
    """Provider 创建/更新请求体。"""

    name: str = Field(max_length=100)
    base_url: str = Field(max_length=500)
    api_key: str = ""


# ── Model ─────────────────────────────────────────────────────────────────────

class ModelBody(BaseModel):
    """Model 创建/更新请求体。"""

    provider_id: int
    name: str = Field(max_length=200)
    model_type: str = Field(default="chat", max_length=20)  # chat / embedding / rerank
    is_enabled: bool = True



class ModelOut(BaseModel):
    """Model 响应，嵌套 provider。"""

    id: int
    provider_id: int
    name: str
    model_key: str = ""
    model_type: str
    is_enabled: bool
    created_at: datetime
    updated_at: datetime | None = None
    provider: ProviderRecord | None = None

    model_config = {"from_attributes": True}


class ProviderOut(BaseModel):
    """Provider 响应，嵌套关联 models。"""
    id: int
    name: str
    base_url: str
    api_key: str
    created_at: datetime
    updated_at: datetime | None = None
    models: list[ModelRecord] = Field(default_factory=list)

    model_config = {"from_attributes": True}

    @field_serializer("api_key")
    @staticmethod
    def _mask_key(key: str) -> str:
        return mask_api_key(key)


# ── Settings（用途分配）───────────────────────────────────────────────────────

class ModelSettings(BaseModel):
    default_chat_model: str | None = None       # "openai/gpt-4o"
    default_embedding_model: str | None = None
    default_rerank_model: str | None = None


# ── 测试 / 同步 ───────────────────────────────────────────────────────────────

class ProviderTestResult(BaseModel):
    success: bool
    models: list[str] = Field(default_factory=list)
    error: str = ""


class AvailableModel(BaseModel):
    """provider /models 返回的模型，附带猜测类型。"""

    name: str
    model_type: str = "chat"  # chat / embedding / rerank
    imported: bool = False


class ImportModel(BaseModel):
    name: str
    model_type: str = "chat"
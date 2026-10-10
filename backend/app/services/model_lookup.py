"""默认模型解析 — 从 settings 表读 provider_name/model_name → (base_url, api_key, model_name)。"""

from __future__ import annotations

import logging

from app.core.database import AsyncSessionLocal
from app.models.provider import ProviderRecord, SettingRecord
from sqlmodel import select

logger = logging.getLogger(__name__)


async def resolve_default_model(setting_key: str) -> tuple[str, str, str]:
    """从 settings 表读 ``provider_name/model_name``，解析为 ``(base_url, api_key, model_name)``。

    参数：
        setting_key: settings 表中的 key，如 ``"default_chat_model"``
    """
    async with AsyncSessionLocal() as session:
        rec = (await session.exec(
            select(SettingRecord).where(SettingRecord.key == setting_key)
        )).one_or_none()

        if not rec or not rec.value:
            raise ValueError(f"未配置默认模型，请在「模型管理」页面设置 {setting_key}")

        raw = rec.value.strip()
        parts = raw.split("/", 1)
        if len(parts) != 2 or not parts[0] or not parts[1]:
            raise ValueError(f"默认模型格式错误（{raw!r}），应为 provider_name/model_name")

        provider_name, model_name = parts
        p = (await session.exec(
            select(ProviderRecord).where(ProviderRecord.name == provider_name)
        )).one_or_none()

        if p is None:
            raise ValueError(f"Provider {provider_name!r} 不存在，请在「模型管理」页面检查配置")

    return p.base_url, p.api_key, model_name

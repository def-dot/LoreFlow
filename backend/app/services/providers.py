"""Provider 预置与模型自动导入 — 启动时写入 mimo / ollama 并同步远端模型。"""

from __future__ import annotations

from sqlmodel import select

from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.core.logging import get_logger
from app.models.provider import ModelRecord, ProviderRecord, SettingRecord
from app.utils.http import http_client

logger = get_logger(__name__)

# 预置 Provider：(name, base_url, api_key)；密钥走 .env 的 MIMO_API_KEY
DEFAULT_PROVIDERS: list[tuple[str, str, str]] = [
    ("mimo", "https://token-plan-cn.xiaomimimo.com/v1", settings.MIMO_API_KEY),
    ("ollama", "http://localhost:11434/v1", ""),
]


async def _auto_import_models(provider: ProviderRecord) -> int:
    """自动导入 provider 的所有可用模型，返回新增数量。"""
    headers: dict[str, str] = {}
    if provider.api_key:
        headers["Authorization"] = f"Bearer {provider.api_key}"
    resp = await http_client().get(f"{provider.base_url}/models", headers=headers)
    resp.raise_for_status()
    model_ids = [m["id"] for m in resp.json().get("data", [])]
    if not model_ids:
        return 0

    async with AsyncSessionLocal() as session:
        existing = (await session.exec(
            select(ModelRecord.name).where(ModelRecord.provider_id == provider.id)
        )).all()
        new_models = [
            ModelRecord(provider_id=provider.id, name=name, model_type=_guess_model_type(name))
            for name in model_ids
            if name not in existing
        ]
        session.add_all(new_models)
        await session.commit()
    return len(new_models)


def _guess_model_type(name: str) -> str:
    n = name.lower()
    if any(k in n for k in ("embed", "bge", "e5", "gte", "text2vec", "m3e", "contriever")):
        return "embedding"
    if "rerank" in n or "rank" in n:
        return "rerank"
    return "chat"


async def ensure_default_providers() -> None:
    """启动时预置 mimo / ollama Provider 并自动导入模型。异常兜底，不阻塞启动。

    幂等语义：Provider 按 name 只补缺不覆盖（用户在 UI 的改动不会被冲掉）；
    settings 仅在为空时写入默认 chat 模型。
    """
    try:
        from sqlalchemy import text

        async with AsyncSessionLocal() as session:
            # 多 worker 只让一个进程执行
            got = (await session.execute(
                text("SELECT pg_try_advisory_xact_lock(hashtext('default_providers_init'))")
            )).scalar()
            if not got:
                return

            created: list[ProviderRecord] = []
            for name, base_url, api_key in DEFAULT_PROVIDERS:
                existing = (await session.exec(
                    select(ProviderRecord).where(ProviderRecord.name == name)
                )).first()
                if existing:
                    continue
                provider = ProviderRecord(name=name, base_url=base_url, api_key=api_key)
                session.add(provider)
                created.append(provider)
            await session.commit()
            for p in created:
                await session.refresh(p)

        # 提交后再导入：导入走独立 session，需能看到 provider 行
        for p in created:
            try:
                n = await _auto_import_models(p)
                logger.info("预置 Provider %s：导入 %d 个模型", p.name, n)
            except Exception as exc:
                logger.warning("预置 Provider %s 模型导入失败（可在「模型管理」页手动同步）: %s", p.name, exc)

        # 默认 chat 模型：仅在未配置时写入 mimo 的第一个 chat 模型
        async with AsyncSessionLocal() as session:
            rec = (await session.exec(
                select(SettingRecord).where(SettingRecord.key == "default_chat_model")
            )).first()
            if rec and rec.value:
                return
            m = (await session.exec(
                select(ModelRecord)
                .join(ProviderRecord)
                .where(
                    ProviderRecord.name == "mimo",
                    ModelRecord.model_type == "chat",
                    ModelRecord.is_enabled == True,  # noqa: E712
                )
                .order_by(ModelRecord.name)
                .limit(1)
            )).first()
            if not m:
                return
            if rec is None:
                rec = SettingRecord(key="default_chat_model")
                session.add(rec)
            rec.value = f"mimo/{m.name}"
            await session.commit()
            logger.info("默认 chat 模型已设置为 mimo/%s", m.name)
    except Exception:
        logger.exception("预置 Provider 初始化失败")

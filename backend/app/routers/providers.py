"""Provider / Model / Settings CRUD API — 挂载在 /api/v1 下。"""

from datetime import datetime

from fastapi import APIRouter, HTTPException
from sqlmodel import delete, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.database import AsyncSessionLocal
from app.core.response import UnifiedResponseRoute
from app.models.provider import ModelRecord, ProviderRecord, SettingRecord
from app.schemas.providers import (
    AvailableModel,
    ImportModel,
    ModelBody,
    ModelOut,
    ModelSettings,
    ProviderBody,
    ProviderOut,
    ProviderTestResult,
)
from app.utils.http import http_client

router = APIRouter(route_class=UnifiedResponseRoute, tags=["providers"])


# ── Provider CRUD ─────────────────────────────────────────────────────────────

@router.post("/providers", response_model=ProviderOut, status_code=201)
async def create_provider(body: ProviderBody) -> ProviderOut:
    async with AsyncSessionLocal() as session:
        exists = (await session.exec(select(ProviderRecord).where(ProviderRecord.name == body.name))).one_or_none()
        if exists:
            raise HTTPException(status_code=409, detail=f"Provider 名称 {body.name!r} 已存在")
        provider = ProviderRecord(**body.model_dump())
        session.add(provider)
        await session.commit()
        await session.refresh(provider)
        return provider


@router.get("/providers", response_model=list[ProviderOut])
async def list_providers() -> list[ProviderOut]:
    async with AsyncSessionLocal() as session:
        stmt = (
            select(ProviderRecord)
            .order_by(ProviderRecord.id)
        )
        rows = (await session.exec(stmt)).all()
    return rows


@router.get("/providers/{provider_id}", response_model=ProviderOut)
async def get_provider(provider_id: int) -> ProviderOut:
    async with AsyncSessionLocal() as session:
        stmt = (
            select(ProviderRecord)
            .where(ProviderRecord.id == provider_id)
        )
        p = (await session.exec(stmt)).one_or_none()
    if p is None:
        raise HTTPException(status_code=404, detail=f"Provider {provider_id!r} 不存在")
    return p


@router.put("/providers/{provider_id}", response_model=ProviderOut)
async def update_provider(provider_id: int, body: ProviderBody) -> ProviderOut:
    async with AsyncSessionLocal() as session:
        stmt = (
            select(ProviderRecord)
            .where(ProviderRecord.id == provider_id)
        )
        p = (await session.exec(stmt)).one_or_none()
        if p is None:
            raise HTTPException(status_code=404, detail=f"Provider {provider_id!r} 不存在")
        exists = (await session.exec(
            select(ProviderRecord).where(ProviderRecord.name == body.name, ProviderRecord.id != provider_id)
        )).one_or_none()
        if exists:
            raise HTTPException(status_code=409, detail=f"Provider 名称 {body.name!r} 已存在")
        body_dict = body.model_dump()
        if "***" in body_dict.get("api_key", ""):
            body_dict["api_key"] = p.api_key
        p.sqlmodel_update(body_dict)
        p.updated_at = datetime.now()
        await session.commit()
        return p


@router.delete("/providers/{provider_id}")
async def delete_provider(provider_id: int) -> dict[str, int]:
    async with AsyncSessionLocal() as session:
        p = (await session.exec(select(ProviderRecord).where(ProviderRecord.id == provider_id))).one_or_none()
        if p is None:
            raise HTTPException(status_code=404, detail=f"Provider {provider_id!r} 不存在")
        # 删除关联模型
        models = (await session.exec(select(ModelRecord).where(ModelRecord.provider_id == provider_id))).all()
        model_refs = {m.model_key for m in models}
        # 清除 settings 中 value 匹配的引用
        await session.exec(
            delete(SettingRecord).where(
                SettingRecord.key.in_(ModelSettings.model_fields),
                SettingRecord.value.in_(model_refs),
            )
        )
        for m in models:
            await session.delete(m)
        await session.delete(p)
        await session.commit()
    return {"deleted": provider_id}


@router.post("/providers/{provider_id}/test", response_model=ProviderTestResult)
async def test_provider(provider_id: int) -> ProviderTestResult:
    async with AsyncSessionLocal() as session:
        p = (await session.exec(select(ProviderRecord).where(ProviderRecord.id == provider_id))).one_or_none()
    if p is None:
        raise HTTPException(status_code=404, detail=f"Provider {provider_id!r} 不存在")
    try:
        headers: dict[str, str] = {}
        if p.api_key:
            headers["Authorization"] = f"Bearer {p.api_key}"
        resp = await http_client().get(f"{p.base_url}/models", headers=headers)
        resp.raise_for_status()
        data = resp.json()
        model_list = [m["id"] for m in data.get("data", [])] if "data" in data else []
        return ProviderTestResult(success=True, models=model_list)
    except Exception as e:
        return ProviderTestResult(success=False, error=str(e))


@router.get("/providers/{provider_id}/available-models", response_model=list[AvailableModel])
async def get_available_models(provider_id: int) -> list[AvailableModel]:
    async with AsyncSessionLocal() as session:
        p = (await session.exec(select(ProviderRecord).where(ProviderRecord.id == provider_id))).one_or_none()
    if p is None:
        raise HTTPException(status_code=404, detail=f"Provider {provider_id!r} 不存在")
    try:
        headers: dict[str, str] = {}
        if p.api_key:
            headers["Authorization"] = f"Bearer {p.api_key}"
        resp = await http_client().get(f"{p.base_url}/models", headers=headers)
        resp.raise_for_status()
        data = resp.json()
        model_ids = [m["id"] for m in data.get("data", [])] if "data" in data else []
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"获取模型列表失败: {e}")
    async with AsyncSessionLocal() as session:
        existing = {m.name for m in (await session.exec(
            select(ModelRecord).where(ModelRecord.provider_id == provider_id)
        )).all()}
    return [
        AvailableModel(name=m, model_type=_guess_model_type(m), imported=m in existing)
        for m in model_ids
    ]


@router.post("/providers/{provider_id}/import-models")
async def import_models(provider_id: int, body: list[ImportModel]) -> dict[str, int]:
    async with AsyncSessionLocal() as session:
        p = (await session.exec(select(ProviderRecord).where(ProviderRecord.id == provider_id))).one_or_none()
        if p is None:
            raise HTTPException(status_code=404, detail=f"Provider {provider_id!r} 不存在")
        existing = {m.name for m in (await session.exec(
            select(ModelRecord).where(ModelRecord.provider_id == provider_id)
        )).all()}
        created = 0
        for item in body:
            if item.name in existing or item.model_type not in ("chat", "embedding", "rerank"):
                continue
            session.add(ModelRecord(provider_id=provider_id, name=item.name, model_type=item.model_type))
            created += 1
        await session.commit()
    return {"imported": created}


# ── Model CRUD ────────────────────────────────────────────────────────────────

def _guess_model_type(name: str) -> str:
    n = name.lower()
    if any(k in n for k in ("embed", "bge", "e5", "gte", "text2vec", "m3e", "contriever")):
        return "embedding"
    if "rerank" in n or "rank" in n:
        return "rerank"
    return "chat"

@router.get("/models", response_model=list[ModelOut])
async def list_models(provider_id: int | None = None, model_type: str | None = None) -> list[ModelOut]:
    async with AsyncSessionLocal() as session:
        stmt = select(ModelRecord)
        if provider_id is not None:
            stmt = stmt.where(ModelRecord.provider_id == provider_id)
        if model_type:
            stmt = stmt.where(ModelRecord.model_type == model_type)
        stmt = stmt.order_by(ModelRecord.provider_id, ModelRecord.model_type, ModelRecord.name)
        rows = (await session.exec(stmt)).all()
    return list(rows)


@router.post("/models", response_model=ModelOut, status_code=201)
async def create_model(body: ModelBody) -> ModelOut:
    async with AsyncSessionLocal() as session:
        p = (await session.exec(select(ProviderRecord).where(ProviderRecord.id == body.provider_id))).one_or_none()
        if p is None:
            raise HTTPException(status_code=404, detail=f"Provider {body.provider_id!r} 不存在")
        if body.model_type not in ("chat", "embedding", "rerank"):
            raise HTTPException(status_code=400, detail="model_type 必须是 chat / embedding / rerank")
        model = ModelRecord(**body.model_dump())
        session.add(model)
        await session.commit()
        await session.refresh(model, attribute_names=["provider"])
        return model


@router.get("/models/{model_id}", response_model=ModelOut)
async def get_model(model_id: int) -> ModelOut:
    async with AsyncSessionLocal() as session:
        stmt = (
            select(ModelRecord)
            .where(ModelRecord.id == model_id)
        )
        m = (await session.exec(stmt)).one_or_none()
    if m is None:
        raise HTTPException(status_code=404, detail=f"Model {model_id!r} 不存在")
    return m


@router.put("/models/{model_id}", response_model=ModelOut)
async def update_model(model_id: int, body: ModelBody) -> ModelOut:
    async with AsyncSessionLocal() as session:
        stmt = (
            select(ModelRecord)
            .where(ModelRecord.id == model_id)
        )
        m = (await session.exec(stmt)).one_or_none()
        if m is None:
            raise HTTPException(status_code=404, detail=f"Model {model_id!r} 不存在")
        if body.model_type not in ("chat", "embedding", "rerank"):
            raise HTTPException(status_code=400, detail="model_type 必须是 chat / embedding / rerank")
        p = (await session.exec(select(ProviderRecord).where(ProviderRecord.id == body.provider_id))).one_or_none()
        if p is None:
            raise HTTPException(status_code=404, detail=f"Provider {body.provider_id!r} 不存在")
        m.sqlmodel_update(body.model_dump())
        m.updated_at = datetime.now()
        await session.commit()
        return m


@router.delete("/models/{model_id}")
async def delete_model(model_id: int) -> dict[str, int]:
    async with AsyncSessionLocal() as session:
        m = (await session.exec(select(ModelRecord).where(ModelRecord.id == model_id))).one_or_none()
        if m is None:
            raise HTTPException(status_code=404, detail=f"Model {model_id!r} 不存在")
       
        # 清除 settings 中 value 匹配的引用
        await session.exec(
            delete(SettingRecord).where(
                SettingRecord.key.in_(ModelSettings.model_fields),
                SettingRecord.value == m.model_key,
            )
        )
        await session.delete(m)
        await session.commit()
    return {"deleted": model_id}


# ── Settings（用途分配）───────────────────────────────────────────────────────

@router.get("/settings/models", response_model=ModelSettings)
async def get_model_settings() -> ModelSettings:
    async with AsyncSessionLocal() as session:
        rows = (await session.exec(
            select(SettingRecord).where(SettingRecord.key.in_(ModelSettings.model_fields))
        )).all()
        return ModelSettings(**{rec.key: rec.value or None for rec in rows})


@router.put("/settings/models", response_model=ModelSettings)
async def update_model_settings(body: ModelSettings) -> ModelSettings:
    async with AsyncSessionLocal() as session:
        existing = {r.key: r for r in (await session.exec(
            select(SettingRecord).where(SettingRecord.key.in_(ModelSettings.model_fields))
        )).all()}
        for key, val in body.model_dump().items():
            rec = existing.get(key) or SettingRecord(key=key)
            rec.value = val or ""
            session.add(rec)
        await session.commit()
        return body

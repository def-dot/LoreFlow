"""初始化脚本 — 将 pipelines/*.yaml 文件同步到数据库。

用法:
    cd backend && python init_pipelines.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import yaml
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.config import settings
from app.models.pipeline import PipelineRecord


async def init_pipelines() -> None:
    """读取 pipelines 目录下的 YAML 文件并写入数据库。"""

    # ── 1. 扫描 YAML 文件 ──────────────────────────────────────────────
    pipelines_dir = settings.PIPELINES_DIR
    if not pipelines_dir.is_dir():
        print(f"错误: pipelines 目录不存在: {pipelines_dir}")
        sys.exit(1)

    yaml_files = sorted(pipelines_dir.glob("*.yaml"))
    if not yaml_files:
        print(f"警告: 在 {pipelines_dir} 中未找到 .yaml 文件")
        return

    print(f"找到 {len(yaml_files)} 个 YAML 文件，准备写入数据库...")

    # ── 2. 解析所有 YAML ─────────────────────────────────────────────────
    records: list[dict] = []
    for f in yaml_files:
        try:
            raw = f.read_text(encoding="utf-8")
            data = yaml.safe_load(raw)
            name = data.get("name", f.stem)
            description = data.get("description", "")
            records.append({"name": name, "description": description, "definition": raw})
        except Exception as exc:
            print(f"  跳过 {f.name}: {exc}")

    # ── 3. 写入数据库 ─────────────────────────────────────────────────────
    engine = create_async_engine(str(settings.DATABASE_URL))
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with session_factory() as session:
        inserted = 0
        updated = 0

        for rec in records:
            # 检查是否已存在
            stmt = select(PipelineRecord).where(PipelineRecord.name == rec["name"])
            result = await session.execute(stmt)
            existing = result.scalars().first()

            if existing:
                # 更新
                existing.description = rec["description"]
                existing.definition = rec["definition"]
                session.add(existing)
                updated += 1
                print(f"  更新: {rec['name']}")
            else:
                # 插入
                new_rec = PipelineRecord(**rec)
                session.add(new_rec)
                inserted += 1
                print(f"  插入: {rec['name']}")

        await session.commit()

    print(f"\n完成! 插入: {inserted}, 更新: {updated}")


if __name__ == "__main__":
    asyncio.run(init_pipelines())
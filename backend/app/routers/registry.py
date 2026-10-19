"""工具 / 节点类型目录 — 向前端枚举已注册的 tools 和 node types。"""

from fastapi import APIRouter

from app.core.response import UnifiedResponseRoute
from app.registry import REGISTRY
from app.registry.types import TOOL_REGISTRY
from app.schemas.registry import FuncOut

router = APIRouter(prefix="", route_class=UnifiedResponseRoute, tags=["registry"])


@router.get("/tools", response_model=list[FuncOut])
async def list_tools() -> list[FuncOut]:
    return [
        FuncOut(name=key, label=t.label, description=t.description, metadata=t.metadata,
                input_schema=t.input_schema, output_schema=t.output_schema)
        for key, t in sorted(TOOL_REGISTRY.items())
    ]


@router.get("/node-types", response_model=list[FuncOut])
async def list_node_types() -> list[FuncOut]:
    return [
        FuncOut(name=t.name, label=t.label or t.name, description=t.description, metadata=t.metadata,
                input_schema=t.input_schema, output_schema=t.output_schema)
        for t in sorted(REGISTRY.values(), key=lambda t: (t.metadata.get("order", 999), t.name))
    ]
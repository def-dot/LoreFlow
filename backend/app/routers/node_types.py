"""节点类型目录 — 向页面枚举系统支持的节点/条件类型（数据源：app.registry）"""

from fastapi import APIRouter

from app.core.response import UnifiedResponseRoute
from app.registry import REGISTRY
from app.registry.plugins import plugin_owner_index
from app.registry.types import roles_of, source_of
from app.schemas.common import SourceInfo
from app.schemas.node_types import NodeTypeOut

router = APIRouter(prefix="/node-types", route_class=UnifiedResponseRoute, tags=["node-types"])


@router.get("", response_model=list[NodeTypeOut])
async def list_node_types() -> list[NodeTypeOut]:
    owner = plugin_owner_index()
    sorted_types = sorted(
        REGISTRY.values(),
        key=lambda t: (t.metadata.get("order", 999), t.name),
    )
    out: list[NodeTypeOut] = []
    for t in sorted_types:
        kind, src_name = source_of(t, owner.get(t.name, ""))
        out.append(
            NodeTypeOut(
                name=t.name,
                label=t.label,
                description=t.description,
                metadata=t.metadata,
                input_schema=t.json_input_schema(),
                output_schema=t.json_output_schema(),
                roles=roles_of(t.name),
                source=SourceInfo(kind=kind, name=src_name),
            )
        )
    return out

"""节点类型目录 — 向页面枚举系统支持的节点/条件类型（数据源：app.registry）"""

from fastapi import APIRouter

from app.core.response import UnifiedResponseRoute
from app.registry import REGISTRY
from app.schemas.node_types import NodeTypeOut

router = APIRouter(prefix="/node-types", route_class=UnifiedResponseRoute, tags=["node-types"])


@router.get("", response_model=list[NodeTypeOut])
async def list_node_types() -> list[NodeTypeOut]:
    sorted_types = sorted(
        REGISTRY.values(),
        key=lambda t: (t.metadata.get("order", 999)),
    )
    return [
        NodeTypeOut(
            name=t.name,
            label=t.label,
            description=t.description,
            metadata=t.metadata,
            input_schema=t.input_schema.model_json_schema() if t.input_schema else None,
            output_schema=t.output_schema.model_json_schema() if t.output_schema else None,
        )
        for t in sorted_types
    ]

"""节点类型目录 — 向页面枚举系统支持的节点/条件类型（数据源：app.registry）"""

from fastapi import APIRouter

from app.core.response import UnifiedResponseRoute
from app.registry import REGISTRY
from app.registry.core import NodeGroup
from app.schemas.node_types import NodeTypeListResponse, NodeTypeOut

router = APIRouter(prefix="/node-types", route_class=UnifiedResponseRoute, tags=["node-types"])


@router.get("", response_model=NodeTypeListResponse)
async def list_node_types() -> NodeTypeListResponse:
    order = list(NodeGroup)
    sorted_types = sorted(REGISTRY.values(), key=lambda t: order.index(t.group) if t.group in order else len(order))
    return NodeTypeListResponse(
        node_types=[
            NodeTypeOut(
                name=t.name,
                label=t.label,
                description=t.description,
                group=t.group,
                input_schema=t.input_schema,
                output_schema=t.output_schema,
            )
            for t in sorted_types
        ]
    )

"""节点类型目录 — 向页面枚举系统支持的节点/条件类型（数据源：app.registry）"""

from fastapi import APIRouter

from app.core.response import UnifiedResponseRoute
from app.registry import all_types
from app.schemas.node_types import NodeTypeListResponse, NodeTypeOut

router = APIRouter(prefix="/node-types", route_class=UnifiedResponseRoute, tags=["node-types"])


@router.get("", response_model=NodeTypeListResponse)
async def list_node_types() -> NodeTypeListResponse:
    return NodeTypeListResponse(
        node_types=[
            NodeTypeOut(name=t.name, kind=t.kind, label=t.label, description=t.description)
            for t in all_types()
        ]
    )

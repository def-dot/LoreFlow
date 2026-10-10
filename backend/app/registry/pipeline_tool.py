"""
Pipeline 注册 — 将 Pipeline 注册为节点（REGISTRY）和/或工具（TOOL_REGISTRY）。

- 所有 Pipeline 注册到 REGISTRY（供子工作流引用）
- agent_tool=True 的 Pipeline 同时注册到 TOOL_REGISTRY（供 Agent 调用）

每个 Pipeline 的 params（JSON Schema properties）直接作为参数 schema。
"""

import logging
from typing import Any

from pydantic import BaseModel, Field

from app.engine.pipeline import Pipeline
from app.registry.types import REGISTRY, TOOL_REGISTRY, FuncDef

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pipeline → FuncDef 注册
# ---------------------------------------------------------------------------

class PipelineOutput(BaseModel):
    """Pipeline 输出（统一格式，方便 execute_tool_call 序列化）。"""
    status: str = Field(description="执行状态：completed / failed")
    output: dict[str, Any] = Field(default_factory=dict, description="工作流输出结果")
    error: str | None = Field(default=None, description="错误信息（失败时）")


def _make_pipeline_runner(pipeline: Pipeline):
    """创建 Pipeline 执行闭包。"""

    async def _run(**kwargs: Any) -> PipelineOutput:
        pipeline.validate_inputs(kwargs)

        try:
            _, output = await pipeline.run(inputs=kwargs)
        except Exception as exc:
            logger.exception("[pipeline] 执行失败: %s", pipeline.name)
            return PipelineOutput(status="failed", error=str(exc))

        return PipelineOutput(
            status="completed",
            output=output or {},
        )

    return _run


def register_pipeline(pipeline: Pipeline) -> None:
    """注册 Pipeline：始终注册到 REGISTRY（节点），agent_tool=True 时同时注册到 TOOL_REGISTRY（工具）。"""
    try:
        input_props = {k: v.model_dump() for k, v in pipeline.params.items()} if pipeline.params else {}
        input_schema: dict[str, Any] = {"type": "object", "properties": input_props}
        if pipeline.required:
            input_schema["required"] = pipeline.required

        runner = _make_pipeline_runner(pipeline)

        output_schema = PipelineOutput.model_json_schema()
        output_schema["properties"]["output"] = (
            pipeline.get_node_schema(pipeline.end_node).output_schema or {"type": "object"}
        )

        fd = FuncDef(
            name=pipeline.name,
            func=runner,
            label=pipeline.name,
            description=pipeline.description or "",
            metadata={"group": "工作流", "source": "pipeline"},
            input_schema=input_schema,
            output_schema=output_schema,
        )

        REGISTRY[pipeline.name] = fd
        logger.debug("[pipeline] 注册节点: %s", pipeline.name)

        if pipeline.metadata.agent_tool:
            TOOL_REGISTRY[pipeline.name] = fd
            logger.debug("[pipeline] 注册工具: %s", pipeline.name)
    except Exception:
        logger.warning("[pipeline] 注册失败: %s", pipeline.name, exc_info=True)


def unregister_pipeline(name: str) -> None:
    """从两张注册表移除指定 Pipeline。"""
    REGISTRY.pop(name, None)
    TOOL_REGISTRY.pop(name, None)

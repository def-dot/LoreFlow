"""
Pipeline 工具 — 将 Pipeline 自动注册为 Agent 可调用的工具。

每个 Pipeline 的 params（JSON Schema properties）直接作为工具参数 schema。
工具名直接使用 Pipeline name
"""

import logging
from typing import Any

from pydantic import BaseModel, Field

from app.engine.pipeline import Pipeline
from app.registry.types import TOOL_REGISTRY, FuncDef

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pipeline → FuncDef 注册
# ---------------------------------------------------------------------------

class PipelineOutput(BaseModel):
    """Pipeline 工具输出（统一格式，方便 execute_tool_call 序列化）。"""
    status: str = Field(description="执行状态：completed / failed")
    output: dict[str, Any] = Field(default_factory=dict, description="工作流输出结果")
    error: str | None = Field(default=None, description="错误信息（失败时）")


def _make_pipeline_runner(pipeline: Pipeline):
    """创建 Pipeline 执行闭包。"""

    async def _run(inputs: dict[str, Any] | None = None) -> PipelineOutput:
        from app.engine.pipeline import validate_inputs

        if pipeline.params:
            validate_inputs(pipeline.params, pipeline.required, inputs)

        try:
            _, output = await pipeline.run(inputs=inputs or {})
        except Exception as exc:
            logger.exception("[pipeline_tool] 执行失败: %s", pipeline.name)
            return PipelineOutput(status="failed", error=str(exc))

        return PipelineOutput(
            status="completed",
            output=output or {},
        )

    return _run


def register_pipeline_tool(pipeline: Pipeline) -> None:
    """将一个 Pipeline 注册为 TOOL_REGISTRY 中的工具。metadata.agent_tool = false 时跳过。"""
    if not pipeline.metadata.agent_tool:
        return
    try:
        props = {k: v.model_dump() for k, v in pipeline.params.items()} if pipeline.params else {}
        schema: dict[str, Any] = {"type": "object", "properties": props}
        if pipeline.required:
            schema["required"] = pipeline.required

        runner = _make_pipeline_runner(pipeline)

        fd = FuncDef(
            name=pipeline.name,
            func=runner,
            label=pipeline.name,
            description=pipeline.description or "",
            metadata={"group": "工作流"},
            input_schema=schema,
            output_schema=PipelineOutput,
        )

        TOOL_REGISTRY[pipeline.name] = fd
        logger.debug("[pipeline_tool] 注册工具: %s", pipeline.name)
    except Exception:
        logger.warning("[pipeline_tool] 注册失败: %s", pipeline.name, exc_info=True)


def unregister_pipeline_tool(name: str) -> None:
    """从 TOOL_REGISTRY 移除指定 Pipeline 工具。"""
    TOOL_REGISTRY.pop(name, None)

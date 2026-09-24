"""测试 start 节点输出是否正确传递给下游。"""
import asyncio
from app.engine import Pipeline


async def main():
    # 定义一个简单的工作流：start -> llm
    data = {
        "name": "test",
        "nodes": [
            {
                "name": "__start__",
                "type": "start",
                "inputs": {
                    "query": {"type": "text", "required": True}
                }
            },
            {
                "name": "llm",
                "type": "llm",
                "depends_on": ["__start__"],
                "inputs": {
                    "prompt": "$__start__.query"
                }
            }
        ]
    }

    pipeline = Pipeline.model_validate(data)

    # 临时 patch executor 来检查 ctx
    from app.engine.executor import PipeLineExecutor
    original_execute = PipeLineExecutor.execute

    async def patched_execute(self, resume=None):
        print(f"\n[DEBUG] ctx before execute: {self.ctx}")
        print(f"[DEBUG] start node output in ctx: {self.ctx.get('__start__')}")
        return await original_execute(self, resume=resume)

    PipeLineExecutor.execute = patched_execute

    try:
        results, output = await pipeline.run(inputs={"query": "hello world"})
    finally:
        PipeLineExecutor.execute = original_execute

    print("\nResults:")
    for name, result in results.items():
        print(f"  {name}: {result.status.value} -> {result.output}")


if __name__ == "__main__":
    asyncio.run(main())

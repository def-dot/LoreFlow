import asyncio

async def finalize():
    print("开始写数据库...")
    await asyncio.sleep(1) # 模拟 DB 异步 I/O 切换
    print("数据库写入成功！") # 永远无法执行到这里

async def run():
    try:
        await asyncio.sleep(2) # 模拟被取消的操作
    except asyncio.CancelledError:
        print("捕获到了 CancelledError")
    finally:
        # 错误示例：直接 await
        await finalize()

async def main():
    task = asyncio.create_task(run())
    await asyncio.sleep(0.1)
    task.cancel() # 取消任务
    try:
        await task
    except asyncio.CancelledError:
        pass

asyncio.run(main())
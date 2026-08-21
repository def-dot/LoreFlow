import asyncio

class SuspendExecution(Exception):
    """自定义挂起异常"""
    pass

async def task_b():
    print("Task B 开始运行...")
    await asyncio.sleep(0.5)
    # 模拟 Task B 抛出异常
    raise SuspendExecution("Task B 触发了挂起异常！")

async def task_a(b_task: asyncio.Task):
    print("Task A 开始等待 Task B 完成...")
    # 等待 Task B 执行结束
    await asyncio.sleep(1)
    
    print("Task A 尝试获取 Task B 的结果...")
    try:
        # 调用 result() 时，Task B 内部的异常会在 Task A 中抛出
        res = b_task.result()
        print(f"Task B 结果: {res}")
    except SuspendExecution as e:
        print(f"Task A 捕获到了 Task B 的异常: {e}")

async def main():
    # 启动 Task B
    b_task = asyncio.create_task(task_b())
    # 将 Task B 的句柄传给 Task A
    a_task = asyncio.create_task(task_a(b_task))
    
    # 等待 Task A 执行完毕
    await a_task

if __name__ == "__main__":
    asyncio.run(main())
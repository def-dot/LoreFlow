"""临时调试：打印 reviewing 时刻 GET /runs/{id} 的完整响应，验证 payload 是否到位。"""

import asyncio
import json

from httpx import AsyncClient


async def test_print_reviewing_payload(client: AsyncClient) -> None:
    resp = await client.post(
        "/api/v1/runs",
        json={"config_file": "05_human_review.yaml", "inputs": {"title": "调试标题", "content": "调试正文"}},
    )
    assert resp.status_code == 201
    run_id = resp.json()["data"]["run_id"]

    while True:
        data = (await client.get(f"/api/v1/runs/{run_id}")).json()["data"]
        if data["status"] == "reviewing":
            break
        if data["status"] != "running":
            raise AssertionError(f"run 在审批前已结束: {data}")
        await asyncio.sleep(0.05)

    print("\n===== REVIEWING RESPONSE =====")
    print("run status:", data["status"])
    print("nodes keys:", list(data["nodes"]))
    print("review entry:", json.dumps(data["nodes"].get("review"), ensure_ascii=False, indent=1))
    print("==============================\n")

    # 收尾：审批掉，避免残留 running 记录干扰其他测试
    await client.post(f"/api/v1/runs/{run_id}/approve/review", json={"approve": True})

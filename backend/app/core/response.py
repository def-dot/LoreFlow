"""统一响应封装 — 自定义 APIRoute，把成功 JSON 响应自动包成 {code, msg, data} 信封"""

import json
from collections.abc import Callable

from fastapi import Request, Response
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute


class UnifiedResponseRoute(APIRoute):
    """自定义路由类：成功（2xx）JSON 响应封装成统一格式 {code, msg, data}。

    错误响应由 app.core.exceptions 的全局异常处理器统一返回
    """

    def get_route_handler(self) -> Callable:
        original_route_handler = super().get_route_handler()

        async def custom_route_handler(request: Request) -> Response:
            response: Response = await original_route_handler(request)
            if response.media_type != "application/json":
                return response
            try:
                data = json.loads(response.body)
            except (ValueError, UnicodeDecodeError):
                return response

            return JSONResponse(
                status_code=response.status_code,
                content={"code": response.status_code, "msg": "ok", "data": data},
                background=response.background,
            )

        return custom_route_handler

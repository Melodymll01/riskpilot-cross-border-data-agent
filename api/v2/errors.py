"""Domain / app 层异常 → HTTP 响应的统一映射。

每个 ``build_*_routes(container)`` 在最后调用 ``register_error_handlers(router)``
把自定义异常翻译成 ``ErrorResponse`` JSON。
"""

from __future__ import annotations

from fastapi import HTTPException, Request, status
from fastapi.responses import JSONResponse

from api.v2.schemas import ErrorResponse
from domain.errors import (
    AuthError,
    DomainError,
    InvalidToken,
    OAuthFlowError,
    OwnerMismatch,
    TaskNotFound,
    ToolExecutionError,
    ToolNotFound,
    UserNotFound,
)

# DomainError 类型 → (http_status, error_code)
_DOMAIN_MAP: dict[type[DomainError], tuple[int, str]] = {
    InvalidToken: (status.HTTP_401_UNAUTHORIZED, "INVALID_TOKEN"),
    OAuthFlowError: (status.HTTP_400_BAD_REQUEST, "OAUTH_ERROR"),
    AuthError: (status.HTTP_401_UNAUTHORIZED, "AUTH_ERROR"),
    UserNotFound: (status.HTTP_404_NOT_FOUND, "USER_NOT_FOUND"),
    TaskNotFound: (status.HTTP_404_NOT_FOUND, "TASK_NOT_FOUND"),
    OwnerMismatch: (status.HTTP_403_FORBIDDEN, "FORBIDDEN"),
    ToolNotFound: (status.HTTP_400_BAD_REQUEST, "TOOL_NOT_FOUND"),
    ToolExecutionError: (status.HTTP_502_BAD_GATEWAY, "TOOL_FAILED"),
}


def _map_domain_error(exc: DomainError) -> tuple[int, str]:
    """按 MRO 顺序找最具体的映射。"""
    for cls in type(exc).__mro__:
        if cls in _DOMAIN_MAP:
            return _DOMAIN_MAP[cls]
    return status.HTTP_500_INTERNAL_SERVER_ERROR, "DOMAIN_ERROR"


def install_exception_handlers(app: object, *, path_prefix: str = "/api/v2") -> None:
    """把异常处理注册到 FastAPI app 上（不是 router，因为 exception handler 只在 app 层有效）。

    用 ``object`` 注解避免顶层依赖 FastAPI 名字（运行时仍是 FastAPI 实例）。

    ``path_prefix`` 用来限定作用域：只有命中该前缀的请求才走 v2 的统一 error 协议；
    其他路径（老 ``/api/*``、根路径、静态文件等）继续走 FastAPI/Starlette 的默认行为。
    这样 Strangler Fig 不破——老路由的 ``HTTPException(detail="...")`` 仍按原契约返回。
    """
    from fastapi import FastAPI

    if not isinstance(app, FastAPI):
        msg = "install_exception_handlers expects a FastAPI instance"
        raise TypeError(msg)

    def _in_scope(request: Request) -> bool:
        return request.url.path.startswith(path_prefix)

    def _default_http_response(exc: HTTPException) -> JSONResponse:
        """老路由的回落响应：保持 FastAPI 默认契约 ``{"detail": <whatever>}``。"""
        headers = getattr(exc, "headers", None) or None
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": exc.detail},
            headers=headers,
        )

    @app.exception_handler(DomainError)
    async def _domain_handler(request: Request, exc: DomainError) -> JSONResponse:
        # DomainError 是 v2 / app / domain 内部异常；理论上不会从老路由抛出。
        # 出现在老路由 = bug，给个 500。
        if not _in_scope(request):
            return JSONResponse(
                status_code=500, content={"detail": "Internal Server Error"}
            )
        status_code, code = _map_domain_error(exc)
        body = ErrorResponse(error_code=code, message=str(exc))
        return JSONResponse(status_code=status_code, content=body.model_dump())

    @app.exception_handler(PermissionError)
    async def _perm_handler(request: Request, exc: PermissionError) -> JSONResponse:
        if not _in_scope(request):
            return JSONResponse(status_code=500, content={"detail": str(exc)})
        body = ErrorResponse(error_code="FORBIDDEN", message=str(exc))
        return JSONResponse(
            status_code=status.HTTP_403_FORBIDDEN, content=body.model_dump()
        )

    @app.exception_handler(ValueError)
    async def _value_handler(request: Request, exc: ValueError) -> JSONResponse:
        if not _in_scope(request):
            return JSONResponse(status_code=500, content={"detail": str(exc)})
        body = ErrorResponse(error_code="BAD_REQUEST", message=str(exc))
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST, content=body.model_dump()
        )

    @app.exception_handler(HTTPException)
    async def _http_handler(request: Request, exc: HTTPException) -> JSONResponse:
        # 老路由的 HTTPException(detail=str) 不应被重写（破坏既有 API 契约）。
        if not _in_scope(request):
            return _default_http_response(exc)
        # 如果 detail 已经是结构化字典（来自 deps.require_owner），保留；否则包成 ErrorResponse。
        # exc.detail 在 fastapi/starlette 里实际是 Any，但类型注解为 str，需要拓宽。
        detail: object = exc.detail
        if isinstance(detail, dict) and "error_code" in detail:
            return JSONResponse(status_code=exc.status_code, content=detail)
        body = ErrorResponse(error_code="HTTP_ERROR", message=str(exc.detail))
        return JSONResponse(status_code=exc.status_code, content=body.model_dump())

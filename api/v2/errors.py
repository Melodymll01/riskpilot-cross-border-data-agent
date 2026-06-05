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


def install_exception_handlers(app: object) -> None:
    """把异常处理注册到 FastAPI app 上（不是 router，因为 exception handler 只在 app 层有效）。

    用 ``object`` 注解避免顶层依赖 FastAPI 名字（运行时仍是 FastAPI 实例）。
    """
    from fastapi import FastAPI

    if not isinstance(app, FastAPI):
        msg = "install_exception_handlers expects a FastAPI instance"
        raise TypeError(msg)

    @app.exception_handler(DomainError)
    async def _domain_handler(request: Request, exc: DomainError) -> JSONResponse:
        status_code, code = _map_domain_error(exc)
        body = ErrorResponse(error_code=code, message=str(exc))
        return JSONResponse(status_code=status_code, content=body.model_dump())

    @app.exception_handler(PermissionError)
    async def _perm_handler(request: Request, exc: PermissionError) -> JSONResponse:
        body = ErrorResponse(error_code="FORBIDDEN", message=str(exc))
        return JSONResponse(
            status_code=status.HTTP_403_FORBIDDEN, content=body.model_dump()
        )

    @app.exception_handler(ValueError)
    async def _value_handler(request: Request, exc: ValueError) -> JSONResponse:
        body = ErrorResponse(error_code="BAD_REQUEST", message=str(exc))
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST, content=body.model_dump()
        )

    @app.exception_handler(HTTPException)
    async def _http_handler(request: Request, exc: HTTPException) -> JSONResponse:
        # 如果 detail 已经是结构化字典（来自 deps.require_owner），保留；否则包成 ErrorResponse。
        # exc.detail 在 fastapi/starlette 里实际是 Any，但类型注解为 str，需要拓宽。
        detail: object = exc.detail
        if isinstance(detail, dict) and "error_code" in detail:
            return JSONResponse(status_code=exc.status_code, content=detail)
        body = ErrorResponse(error_code="HTTP_ERROR", message=str(exc.detail))
        return JSONResponse(status_code=exc.status_code, content=body.model_dump())

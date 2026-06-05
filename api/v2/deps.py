"""FastAPI 依赖：owner_id 解析 + 容器获取。

设计思路：
- 路由通过闭包持有 ``container``（``build_*_routes(container)`` 模式），
  不需要 ``Depends`` 拿容器。
- 但 owner_id 解析每次请求都要做，且要能 401，最适合做成 Depends。
- 提供两个层级：
  - ``identify_owner`` —— 可选；找不到 owner 返回 None，用于 /auth/me 这类
  - ``require_owner`` —— 必选；找不到 owner 抛 401

Cookie 名/属性见 ``Settings.cookie_*``；签发由 ``set_session_cookie`` 统一。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from fastapi import HTTPException, Request, Response, status

if TYPE_CHECKING:
    from app.container import AppContainer
    from config import Settings


def make_identify_owner(container: AppContainer) -> Callable[[Request], str | None]:
    """构造 ``identify_owner`` Depends：返回 owner_id 或 None。"""
    cookie_name = container.settings.cookie_name

    def _identify(request: Request) -> str | None:
        token = request.cookies.get(cookie_name)
        return container.auth_login.identify(token)

    return _identify


def make_require_owner(container: AppContainer) -> Callable[[Request], str]:
    """构造 ``require_owner`` Depends：找不到 owner 抛 401。"""
    cookie_name = container.settings.cookie_name

    def _require(request: Request) -> str:
        token = request.cookies.get(cookie_name)
        uid = container.auth_login.identify(token)
        if uid is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={
                    "error_code": "AUTH_REQUIRED",
                    "message": "需要登录或先调用 /api/v2/auth/anonymous 获得身份",
                },
            )
        return uid

    return _require


def make_require_admin(container: AppContainer) -> Callable[[Request], str]:
    """构造 ``require_admin`` Depends：要求 owner 命中 ``settings.admin_user_ids``。

    返回 owner_id（已通过管理员校验）。失败规则：
    - 未登录 → 401（语义同 ``require_owner``）
    - 已登录但非管理员 → 403（``ADMIN_REQUIRED``）

    使用：
        admin_dep = make_require_admin(container)
        @router.delete("/documents/{id}")
        def remove(id: str, admin_id: str = Depends(admin_dep)): ...
    """
    cookie_name = container.settings.cookie_name
    admin_set = set(container.settings.admin_user_ids)

    def _require_admin(request: Request) -> str:
        token = request.cookies.get(cookie_name)
        uid = container.auth_login.identify(token)
        if uid is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={
                    "error_code": "AUTH_REQUIRED",
                    "message": "需要登录或先调用 /api/v2/auth/anonymous 获得身份",
                },
            )
        if uid not in admin_set:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "error_code": "ADMIN_REQUIRED",
                    "message": "该操作仅限管理员",
                },
            )
        return uid

    return _require_admin


def set_session_cookie(
    response: Response, token: str, settings: Settings
) -> None:
    """统一签发 session cookie。"""
    response.set_cookie(
        key=settings.cookie_name,
        value=token,
        httponly=True,
        secure=settings.cookie_secure,
        samesite=settings.cookie_samesite,
        max_age=settings.jwt_ttl_seconds,
        path="/",
    )


def clear_session_cookie(response: Response, settings: Settings) -> None:
    response.delete_cookie(
        key=settings.cookie_name,
        path="/",
        samesite=settings.cookie_samesite,
    )

"""``/api/v2/auth/*`` 路由：anonymous / github login+callback / me / logout。

所有路由通过闭包持有 ``container``；不引入全局变量。
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import RedirectResponse

from api.v2.deps import clear_session_cookie, make_identify_owner, set_session_cookie
from api.v2.schemas import (
    AnonymousLoginResponse,
    GithubLoginResponse,
    OkResponse,
    UserOut,
    WhoAmIResponse,
)
from domain.errors import AuthError, OAuthFlowError

if TYPE_CHECKING:
    from app.container import AppContainer
    from domain.models import User


def _to_user_out(user: User, admin_ids: Iterable[str]) -> UserOut:
    return UserOut(
        user_id=user.user_id,
        provider=user.provider,
        display_name=user.display_name,
        email=user.email,
        avatar_url=user.avatar_url,
        is_admin=user.user_id in set(admin_ids),
    )


def build_auth_routes(container: AppContainer) -> APIRouter:
    router = APIRouter(prefix="/auth", tags=["auth"])
    identify = make_identify_owner(container)
    admin_ids = container.settings.admin_user_ids

    # ── 匿名登录 ──────────────────────────────────────────────────────
    @router.post(
        "/anonymous",
        response_model=AnonymousLoginResponse,
        status_code=status.HTTP_201_CREATED,
        summary="创建匿名身份，发 session cookie",
    )
    def login_anonymous(response: Response) -> AnonymousLoginResponse:
        user, token = container.auth_login.login_anonymous()
        set_session_cookie(response, token, container.settings)
        return AnonymousLoginResponse(user=_to_user_out(user, admin_ids))

    # ── GitHub OAuth：begin ───────────────────────────────────────────
    @router.get(
        "/github/login",
        response_model=GithubLoginResponse,
        summary="返回 GitHub authorize URL；前端跳转到该 URL",
    )
    def github_login() -> GithubLoginResponse:
        try:
            url, state = container.auth_login.begin("github")
        except OAuthFlowError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "error_code": "OAUTH_UNAVAILABLE",
                    "message": str(exc),
                },
            ) from exc
        return GithubLoginResponse(authorize_url=url, state=state)

    # ── GitHub OAuth：callback ────────────────────────────────────────
    @router.get(
        "/github/callback",
        summary="GitHub 回调；换 token + 颁发 session cookie，并 303 重定向回首页",
    )
    def github_callback(
        code: str = Query(..., min_length=1),
        state: str = Query(..., min_length=1),
    ) -> RedirectResponse:
        try:
            user, token = container.auth_login.complete("github", code, state)
        except AuthError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "error_code": "OAUTH_EXCHANGE_FAILED",
                    "message": str(exc),
                },
            ) from exc
        # 303 See Other：浏览器以 GET 跳回首页，cookie 随 Set-Cookie 一并下发
        redirect = RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)
        set_session_cookie(redirect, token, container.settings)
        _ = user  # 仅为 set cookie；user 详情前端刷新后通过 /auth/me 重新拉取
        return redirect

    # ── 当前身份 ──────────────────────────────────────────────────────
    @router.get(
        "/me",
        response_model=WhoAmIResponse,
        summary="返回当前 owner 的用户信息；未登录时 user=null",
    )
    def whoami(
        request: Request,
        owner_id: str | None = Depends(identify),
    ) -> WhoAmIResponse:
        if owner_id is None:
            return WhoAmIResponse(authenticated=False, user=None)
        user = container.user_repo.get(owner_id)
        if user is None:
            # cookie 有效但用户已被删 —— 视作未登录
            return WhoAmIResponse(authenticated=False, user=None)
        return WhoAmIResponse(authenticated=True, user=_to_user_out(user, admin_ids))

    # ── 登出 ──────────────────────────────────────────────────────────
    @router.post(
        "/logout",
        response_model=OkResponse,
        summary="清除 session cookie；客户端可立即调 /auth/anonymous 拿新匿名身份",
    )
    def logout(response: Response) -> OkResponse:
        clear_session_cookie(response, container.settings)
        return OkResponse()

    return router

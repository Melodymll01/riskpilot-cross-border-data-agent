"""AuthLoginUseCase：把 ``AuthPort`` 的 OAuth/anonymous + JWT 流程串成单一接口。

API 层只看到这三个动作：
- ``begin(provider)`` → 给前端 (auth_url, state)
- ``complete(provider, code, state)`` → (User, JWT)
- ``login_anonymous()`` → (User, JWT)
- ``identify(token)`` → user_id | None

不在本 use case 里做 cookie / session 处理；那些属于 api 层。

Step 025c：写动作（complete / login_anonymous）落审计。
- ``complete`` 成功 → ``AUTH_LOGIN_SUCCESS``；OAuth 异常 → ``AUTH_LOGIN_FAILURE``
- ``login_anonymous`` 成功 → ``AUTH_ANONYMOUS_CREATE``
- ``audit_log=None`` 静默跳过（保留构造向后兼容）；写失败仅 warning 不影响主流程
- ``begin`` 不落审计（仅返回 authorize_url，未产生身份）；``identify``/``require`` 是只读
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from app.request_context import get_request_id
from domain.errors import InvalidToken, OAuthFlowError
from domain.models import AuditAction, AuditEntry

if TYPE_CHECKING:
    from domain.models import User
    from domain.ports import AuditLogPort, AuthPort


logger = logging.getLogger(__name__)


class AuthLoginUseCase:
    def __init__(
        self,
        auth: AuthPort,
        *,
        audit_log: AuditLogPort | None = None,
    ) -> None:
        self._auth = auth
        self._audit = audit_log

    def begin(self, provider: str) -> tuple[str, str]:
        """委托 AuthPort.begin_oauth；任何 provider 错误透传 OAuthFlowError。"""
        return self._auth.begin_oauth(provider)

    def complete(
        self,
        provider: str,
        code: str,
        state: str,
        *,
        request_id: str | None = None,
    ) -> tuple[User, str]:
        """完成 OAuth 回调，颁发 JWT。成功 / 失败均落审计。"""
        try:
            user = self._auth.complete_oauth(provider, code, state)
        except OAuthFlowError as exc:
            self._record_audit(
                action=AuditAction.AUTH_LOGIN_FAILURE,
                resource=f"oauth:{provider}",
                actor_id=None,
                request_id=request_id,
                success=False,
                error=str(exc),
                extra={"provider": provider, "reason": type(exc).__name__},
            )
            raise
        token = self._auth.issue_jwt(user.user_id)
        self._record_audit(
            action=AuditAction.AUTH_LOGIN_SUCCESS,
            resource=f"oauth:{provider}",
            actor_id=user.user_id,
            request_id=request_id,
            success=True,
            error=None,
            extra={"provider": provider},
        )
        return user, token

    def login_anonymous(
        self,
        *,
        request_id: str | None = None,
    ) -> tuple[User, str]:
        """创建匿名用户并颁发 JWT。落审计。"""
        user = self._auth.create_anonymous()
        token = self._auth.issue_jwt(user.user_id)
        self._record_audit(
            action=AuditAction.AUTH_ANONYMOUS_CREATE,
            resource="anonymous",
            actor_id=user.user_id,
            request_id=request_id,
            success=True,
            error=None,
            extra={"provider": "anonymous"},
        )
        return user, token

    def identify(self, token: str | None) -> str | None:
        """解码 token → user_id；空 / 无效一律返回 None。"""
        if not token:
            return None
        return self._auth.verify_jwt(token)

    def require(self, token: str | None) -> str:
        """同 ``identify`` 但失败抛 InvalidToken；供受保护 API 强校验。"""
        uid = self.identify(token)
        if uid is None:
            raise InvalidToken("missing or invalid token")
        return uid

    # ── 私有 ──────────────────────────────────────────────────────────

    def _record_audit(
        self,
        *,
        action: str,
        resource: str,
        actor_id: str | None,
        request_id: str | None,
        success: bool,
        error: str | None,
        extra: dict[str, object] | None = None,
    ) -> None:
        """统一审计写入：audit_log=None 跳过；写失败仅 warning。

        Step 025d：request_id 语义为「显式形参优先 > contextvar > None」。
        """
        if self._audit is None:
            return
        effective_request_id = request_id if request_id is not None else get_request_id()
        try:
            self._audit.record(
                AuditEntry(
                    actor_id=actor_id or "system:unknown",
                    action=action,
                    resource=resource,
                    request_id=effective_request_id,
                    success=success,
                    error=error,
                    extra_json=dict(extra or {}),
                )
            )
        except Exception:  # pragma: no cover - defense in depth
            logger.warning(
                "audit log write failed action=%s resource=%s",
                action,
                resource,
                exc_info=True,
            )

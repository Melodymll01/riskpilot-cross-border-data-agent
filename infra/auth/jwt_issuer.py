"""JWT 颁发与校验：HS256 + 短 TTL。

设计：
- 单一密钥（HS256），无 KMS / 多租户密钥轮换的复杂度
- TTL 默认 24 小时（spec §2 写的是 30 天 cookie，但 token 本身仍短 TTL；前端续签）
- `verify` 任何失败（签名错 / 过期 / 篡改 / 格式错）均返回 None，不抛异常
- payload 仅含 `sub` (user_id) + `iat` + `exp`，不放权限或个人信息
"""

from __future__ import annotations

import time
from collections.abc import Callable

import jwt

DEFAULT_TTL_SECONDS = 24 * 3600
_ALG = "HS256"


class JwtIssuer:
    """HS256 JWT 颁发器 + 校验器。"""

    def __init__(
        self,
        secret: str,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
        *,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if not secret or len(secret) < 16:
            msg = "JWT secret must be at least 16 chars"
            raise ValueError(msg)
        if ttl_seconds <= 0:
            msg = f"ttl_seconds must be positive, got {ttl_seconds}"
            raise ValueError(msg)
        self._secret = secret
        self._ttl = ttl_seconds
        self._clock = clock

    def issue(self, user_id: str) -> str:
        if not user_id:
            msg = "user_id must be non-empty"
            raise ValueError(msg)
        now = int(self._clock())
        payload = {"sub": user_id, "iat": now, "exp": now + self._ttl}
        return jwt.encode(payload, self._secret, algorithm=_ALG)

    def verify(self, token: str) -> str | None:
        """校验通过返回 user_id，任何失败均返回 None。

        说明：使用注入的 ``self._clock`` 自行检查 ``exp``，绕开 PyJWT 的实时时钟，
        以便测试可用受控时间断言过期行为。
        """
        if not token:
            return None
        try:
            payload = jwt.decode(
                token,
                self._secret,
                algorithms=[_ALG],
                options={"verify_exp": False},
            )
        except jwt.PyJWTError:
            return None
        sub = payload.get("sub")
        exp = payload.get("exp")
        if not isinstance(sub, str) or not sub:
            return None
        if not isinstance(exp, (int, float)):
            return None
        if int(self._clock()) >= int(exp):
            return None
        return sub

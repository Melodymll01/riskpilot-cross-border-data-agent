"""匿名用户 Provider：发放 `anon:{uuid}` 形式的 owner_id。"""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable

from domain.models import User


class AnonymousProvider:
    """生成 `User`，`provider="anonymous"`，`user_id="anon:{uuid4().hex[:16]}"`。"""

    def __init__(self, *, clock: Callable[[], float] = time.time) -> None:
        self._clock = clock

    def create(self) -> User:
        anon_id = uuid.uuid4().hex[:16]
        now = self._clock()
        return User(
            user_id=f"anon:{anon_id}",
            provider="anonymous",
            provider_id=anon_id,
            email=None,
            display_name=f"匿名用户-{anon_id[:6]}",
            avatar_url=None,
            created_at=now,
            last_active_at=now,
        )

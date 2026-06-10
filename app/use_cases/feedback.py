"""FeedbackUseCase：消息点赞/点踩反馈的提交、撤销与回显（消息反馈统计）。

把 ``FeedbackRepoPort`` 包成单一入口：
- ``submit``：rating 取 ``"up"`` / ``"down"`` 写入；``"none"`` 视为撤销（删除该条反馈）。
- ``ratings_for_task``：返回 ``{msg_id: rating}``，供前端加载历史时回显按钮高亮。

所有操作都带 ``owner_id`` 做权属隔离：用户只能给自己的会话消息打分、只能看到自己的反馈。
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Literal

from domain.models import MessageFeedback

if TYPE_CHECKING:
    from domain.ports import FeedbackRepoPort

Rating = Literal["up", "down", "none"]


class FeedbackUseCase:
    def __init__(self, repo: FeedbackRepoPort) -> None:
        self._repo = repo

    def submit(
        self,
        *,
        owner_id: str,
        task_id: str,
        msg_id: str,
        rating: Rating,
    ) -> str | None:
        """提交一条反馈；``rating="none"`` 表示撤销。返回生效后的 rating（撤销后为 None）。"""
        if rating == "none":
            self._repo.clear(msg_id, owner_id)
            return None
        now = time.time()
        self._repo.set(
            MessageFeedback(
                msg_id=msg_id,
                task_id=task_id,
                owner_id=owner_id,
                rating=rating,
                created_at=now,
                updated_at=now,
            )
        )
        return rating

    def ratings_for_task(self, task_id: str, owner_id: str) -> dict[str, str]:
        """返回该 task 下当前 owner 的 ``{msg_id: rating}`` 映射。"""
        return self._repo.get_for_task(task_id, owner_id)

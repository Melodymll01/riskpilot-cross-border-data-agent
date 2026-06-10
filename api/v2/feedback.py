"""``/api/v2/feedback/*`` 路由：消息点赞/点踩反馈（供后台统计）。

owner_id 来自 session cookie（``require_owner`` 依赖），所有读写按 owner 隔离：
用户只能给自己的会话消息打分、只能读到自己的反馈。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends

from api.v2.deps import make_require_owner
from api.v2.schemas import (
    FeedbackMapResponse,
    FeedbackRequest,
    FeedbackResponse,
)

if TYPE_CHECKING:
    from app.container import AppContainer


def build_feedback_routes(container: AppContainer) -> APIRouter:
    router = APIRouter(prefix="/feedback", tags=["feedback"])
    require_owner = make_require_owner(container)

    @router.post(
        "",
        response_model=FeedbackResponse,
        summary="对某条回答提交点赞/点踩（rating=none 撤销）",
    )
    def submit_feedback(
        body: FeedbackRequest,
        owner_id: str = Depends(require_owner),
    ) -> FeedbackResponse:
        rating = container.feedback.submit(
            owner_id=owner_id,
            task_id=body.task_id,
            msg_id=body.msg_id,
            rating=body.rating,
        )
        return FeedbackResponse(msg_id=body.msg_id, rating=rating)

    @router.get(
        "",
        response_model=FeedbackMapResponse,
        summary="读取某个 task 下当前用户的全部反馈，用于回显按钮状态",
    )
    def get_feedback(
        task_id: str,
        owner_id: str = Depends(require_owner),
    ) -> FeedbackMapResponse:
        ratings = container.feedback.ratings_for_task(task_id, owner_id)
        return FeedbackMapResponse(ratings=ratings)

    return router

"""``/api/v2/memory/*`` 路由：L3 用户画像查询 + 主动遗忘（被遗忘权，Step 030d）。

设计要点：
- 全部端点过 ``require_owner``，只能读/删**自己**的记忆（owner 隔离，合规底线）。
- ``GET /memory/profile``：返回当前 owner 的稳定偏好画像；记忆禁用时返回空画像。
- ``POST /memory/forget``：级联清除记忆（``scope`` 控制是否连带 L1 原始 task），
  返回各层删除计数；业务层 ``ForgetMemoryUseCase`` 同步落审计。
- 记忆系统禁用（``container.memory is None``）时：profile 返回空、forget 返回零计数，
  均 200——保持优雅降级，不对外暴露内部装配状态。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends

from api.v2.deps import make_require_owner
from api.v2.schemas import ForgetRequest, ForgetResponse, ProfileResponse

if TYPE_CHECKING:
    from app.container import AppContainer


def build_memory_routes(container: AppContainer) -> APIRouter:
    router = APIRouter(prefix="/memory", tags=["memory"])
    require_owner = make_require_owner(container)

    @router.get(
        "/profile",
        response_model=ProfileResponse,
        summary="当前 owner 的 L3 用户画像（稳定偏好）",
    )
    def get_profile(owner_id: str = Depends(require_owner)) -> ProfileResponse:
        memory = container.memory
        if memory is None:
            return ProfileResponse(owner_id=owner_id, facts={}, updated_at=0.0)
        profile = memory.get_profile(owner_id)
        return ProfileResponse(
            owner_id=profile.owner_id,
            facts=dict(profile.facts),
            updated_at=profile.updated_at,
        )

    @router.post(
        "/forget",
        response_model=ForgetResponse,
        summary="主动遗忘（被遗忘权）：级联清除记忆，返回删除计数",
    )
    def forget(
        body: ForgetRequest,
        owner_id: str = Depends(require_owner),
    ) -> ForgetResponse:
        result = container.forget_memory.forget(owner_id, scope=body.scope)
        return ForgetResponse(
            owner_id=result.owner_id,
            scope=result.scope,
            summaries_deleted=result.summaries_deleted,
            profile_deleted=result.profile_deleted,
            facts_deleted=result.facts_deleted,
            states_deleted=result.states_deleted,
            tasks_deleted=result.tasks_deleted,
            total_deleted=result.total_deleted,
        )

    return router

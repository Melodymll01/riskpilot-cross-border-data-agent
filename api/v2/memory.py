"""``/api/v2/memory/*`` 路由：L3 用户画像查询 + 主动遗忘（被遗忘权，Step 030d）
+ 每用户记忆开关读写 + 长期事实清单（Step 031a）。

设计要点：
- 全部端点过 ``require_owner``，只能读/改**自己**的记忆（owner 隔离，合规底线）。
- ``GET /memory/profile``：返回当前 owner 的稳定偏好画像；记忆禁用时返回空画像。
- ``POST /memory/forget``：级联清除记忆（``scope`` 控制是否连带 L1 原始 task），
  返回各层删除计数；业务层 ``ForgetMemoryUseCase`` 同步落审计。
- ``GET/PUT /memory/settings``：读/改两个开关（参考保存的记忆 / 参考会话上下文），
  ``PUT`` 部分更新并落同意变更审计（``MemorySettingsUseCase``）。
- ``GET /memory/facts``：列当前生效的长期事实 + 容量上限，供管理面板渲染。
- ``DELETE /memory/facts/{id}``：删当前 owner 的单条事实（被遗忘权细粒度，Step 034），
  成功 204；事实不存在 / 不属于该 owner → 404；删除落 ``MEMORY_FACT_DELETE`` 审计。
- 记忆系统禁用（``container.memory is None``）时读端点优雅降级（空/默认值，200）；
  单条删除因资源不存在返回 404。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, HTTPException, Response, status

from api.v2.deps import make_require_owner
from api.v2.schemas import (
    ForgetRequest,
    ForgetResponse,
    MemoryFactItem,
    MemoryFactsResponse,
    MemorySettingsResponse,
    ProfileResponse,
    UpdateMemorySettingsRequest,
)

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

    @router.get(
        "/settings",
        response_model=MemorySettingsResponse,
        summary="当前 owner 的记忆开关（参考保存的记忆 / 参考会话上下文）",
    )
    def get_settings(owner_id: str = Depends(require_owner)) -> MemorySettingsResponse:
        settings = container.memory_settings.get(owner_id)
        return MemorySettingsResponse(
            use_saved_memory=settings.use_saved_memory,
            reference_history=settings.reference_history,
            updated_at=settings.updated_at,
        )

    @router.put(
        "/settings",
        response_model=MemorySettingsResponse,
        summary="更新记忆开关（部分更新，落同意变更审计）",
    )
    def update_settings(
        body: UpdateMemorySettingsRequest,
        owner_id: str = Depends(require_owner),
    ) -> MemorySettingsResponse:
        updated = container.memory_settings.update(
            owner_id,
            use_saved_memory=body.use_saved_memory,
            reference_history=body.reference_history,
        )
        return MemorySettingsResponse(
            use_saved_memory=updated.use_saved_memory,
            reference_history=updated.reference_history,
            updated_at=updated.updated_at,
        )

    @router.get(
        "/facts",
        response_model=MemoryFactsResponse,
        summary="当前 owner 生效的长期事实清单（管理面板）",
    )
    def list_facts(owner_id: str = Depends(require_owner)) -> MemoryFactsResponse:
        cap = container.settings.memory_fact_cap_per_owner
        memory = container.memory
        if memory is None:
            return MemoryFactsResponse(facts=[], count=0, cap=cap)
        facts = memory.list_facts(owner_id)
        items = [
            MemoryFactItem(
                fact_id=f.fact_id,
                text=f.text,
                tags=list(f.tags),
                created_at=f.created_at,
            )
            for f in facts
        ]
        return MemoryFactsResponse(facts=items, count=len(items), cap=cap)

    @router.delete(
        "/facts/{fact_id}",
        status_code=status.HTTP_204_NO_CONTENT,
        summary="删除当前 owner 的单条长期事实（被遗忘权细粒度）",
    )
    def delete_fact(
        fact_id: str,
        owner_id: str = Depends(require_owner),
    ) -> Response:
        deleted = container.forget_memory.delete_fact(owner_id, fact_id)
        if not deleted:
            raise HTTPException(status_code=404, detail="fact not found")
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    return router

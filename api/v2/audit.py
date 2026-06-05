"""``/api/v2/audit/*`` 路由：管理员审计日志只读查询（Step 021）。

设计要点：
- **admin-only**：所有端点都过 ``make_require_admin``；非管理员看不到他人审计
- **只读**：审计记录不可篡改；不暴露 update / delete
- 业务层走 ``container.audit_log``（``AuditLogPort`` 默认 ``SqliteAuditLogRepo``）
- 过滤参数：``action`` / ``actor_id`` / ``limit``（防大量回拉）

未来扩展（暂不做）：
- 时间范围 ``since`` / ``until`` 过滤
- cursor 风格分页（当前用 offset，足够 admin 翻看场景）
- 导出 CSV
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, Query

from api.v2.deps import make_require_admin
from api.v2.schemas import AuditEntryOut, AuditLogListResponse

if TYPE_CHECKING:
    from app.container import AppContainer
    from domain.models import AuditEntry


def _to_entry_out(e: AuditEntry) -> AuditEntryOut:
    return AuditEntryOut(
        actor_id=e.actor_id,
        action=e.action,
        resource=e.resource,
        timestamp=e.timestamp,
        request_id=e.request_id,
        success=e.success,
        error=e.error,
        extra_json=e.extra_json,
    )


def build_audit_routes(container: AppContainer) -> APIRouter:
    """构造 ``/audit`` 子 router；全部端点 admin-only。"""

    router = APIRouter(prefix="/audit", tags=["audit"])
    require_admin = make_require_admin(container)

    @router.get(
        "/logs",
        response_model=AuditLogListResponse,
        summary="列出审计日志（admin-only · 按时间倒序）",
    )
    def list_audit_logs(
        limit: int = Query(50, ge=1, le=500, description="返回上限，默认 50"),
        offset: int = Query(0, ge=0, description="分页偏移，从 0 开始"),
        action: str | None = Query(
            None, description="按 action 精确过滤，如 'kb.delete'"
        ),
        actor_id: str | None = Query(
            None, description="按 actor_id 精确过滤，如 'github:Melodymll01'"
        ),
        _admin_id: str = Depends(require_admin),
    ) -> AuditLogListResponse:
        entries = container.audit_log.list_recent(
            limit=limit,
            offset=offset,
            action=action,
            actor_id=actor_id,
        )
        return AuditLogListResponse(
            entries=[_to_entry_out(e) for e in entries],
            count=len(entries),
        )

    return router

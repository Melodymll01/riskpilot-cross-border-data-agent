"""MemorySettingsUseCase：每用户记忆开关读写 + 审计（Step 031a）。

把 ``MemorySettingsStorePort`` 的读写包成单一入口：``get`` 缺省双开，
``update`` 做部分更新（只改传入的开关），并把"谁、把哪个开关改成什么"
落 ``AuditLogPort``——记忆开关本质是**用户同意状态变更**，按 PIPL §14/§55
应可追溯（同意撤回 / 重新授予）。

设计要点：
- ``store=None``（未启用持久化）时：``get`` 返回默认双开；``update`` 返回内存态结果，
  不抛——保持优雅降级。
- 审计只记最终开关值与变更范围，不回存其他内容（数据最小化）。
- ``audit_log=None`` 静默跳过审计写入；审计写失败仅 warning，不影响主流程。
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING

from app.request_context import get_request_id
from domain.models import AuditAction, AuditEntry, MemorySettings

if TYPE_CHECKING:
    from domain.ports import AuditLogPort, MemorySettingsStorePort

logger = logging.getLogger(__name__)


class MemorySettingsUseCase:
    def __init__(
        self,
        store: MemorySettingsStorePort | None,
        *,
        audit_log: AuditLogPort | None = None,
    ) -> None:
        self._store = store
        self._audit = audit_log

    def get(self, owner_id: str) -> MemorySettings:
        """返回该 owner 的开关；未配置 store / 未设置过 → 默认双开。"""
        if self._store is None:
            return MemorySettings(owner_id=owner_id)
        settings = self._store.get(owner_id)
        if settings is None:
            return MemorySettings(owner_id=owner_id)
        return settings

    def update(
        self,
        owner_id: str,
        *,
        use_saved_memory: bool | None = None,
        request_id: str | None = None,
    ) -> MemorySettings:
        """部分更新开关并落审计；只覆盖显式传入的字段（None=保持原值）。

        未配置 store 时直接返回基于当前值合成的结果（不持久化、不抛）。
        """
        current = self.get(owner_id)
        new_use_saved = current.use_saved_memory if use_saved_memory is None else use_saved_memory
        updated = MemorySettings(
            owner_id=owner_id,
            use_saved_memory=new_use_saved,
            updated_at=time.time(),
        )
        if self._store is not None:
            self._store.upsert(updated)
        self._record_audit(
            actor_id=owner_id,
            resource=owner_id,
            request_id=request_id,
            success=True,
            error=None,
            extra={
                "use_saved_memory": updated.use_saved_memory,
                "persisted": self._store is not None,
            },
        )
        return updated

    def _record_audit(
        self,
        *,
        actor_id: str,
        resource: str,
        request_id: str | None,
        success: bool,
        error: str | None,
        extra: dict[str, object] | None = None,
    ) -> None:
        if self._audit is None:
            return
        effective_request_id = request_id if request_id is not None else get_request_id()
        try:
            self._audit.record(
                AuditEntry(
                    actor_id=actor_id or "system:unknown",
                    action=AuditAction.MEMORY_SETTINGS_UPDATE,
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
                AuditAction.MEMORY_SETTINGS_UPDATE,
                resource,
                exc_info=True,
            )

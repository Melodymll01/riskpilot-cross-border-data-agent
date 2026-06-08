"""ForgetMemoryUseCase：主动遗忘（被遗忘权）编排 + 审计（Step 030d）。

把 ``MemoryPort.forget`` 包成单一入口：执行级联删除，并把"谁、删了哪个范围、
删了多少条"落 ``AuditLogPort``（PIPL §47 被遗忘权 + §55 日志留存）。

设计要点：
- 审计只记删除计数与范围，**不回存被删内容**（审计自身守数据最小化）。
- ``memory=None``（记忆禁用）时返回空结果，不抛——保持优雅降级。
- 删除失败：记一条 ``success=False`` 审计后向上抛，由 API 层映射错误。
- ``audit_log=None`` 静默跳过审计写入（构造向后兼容）；审计写失败仅 warning。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from app.request_context import get_request_id
from domain.models import AuditAction, AuditEntry, ForgetResult

if TYPE_CHECKING:
    from domain.ports import AuditLogPort, MemoryPort

logger = logging.getLogger(__name__)


class ForgetMemoryUseCase:
    def __init__(
        self,
        memory: MemoryPort | None,
        *,
        audit_log: AuditLogPort | None = None,
    ) -> None:
        self._memory = memory
        self._audit = audit_log

    def forget(
        self,
        owner_id: str,
        *,
        scope: str = "memory",
        request_id: str | None = None,
    ) -> ForgetResult:
        """清除该 owner 的记忆并落审计。

        ``scope="memory"`` 只清派生记忆（L2/L3/L4）；``"all"`` 额外删 L1 原始 task。
        记忆禁用（``memory=None``）时返回零结果且不落审计。
        """
        if self._memory is None:
            return ForgetResult(owner_id=owner_id, scope=scope)
        try:
            result = self._memory.forget(owner_id, scope=scope)
        except Exception as exc:  # noqa: BLE001 — 失败也要留痕，再抛给上层
            self._record_audit(
            action=AuditAction.MEMORY_FORGET,
            actor_id=owner_id,
            resource=owner_id,
            request_id=request_id,
            success=False,
            error=str(exc),
            extra={"scope": scope},
            )
            raise
        self._record_audit(
            action=AuditAction.MEMORY_FORGET,
            actor_id=owner_id,
            resource=owner_id,
            request_id=request_id,
            success=True,
            error=None,
            extra={
                "scope": result.scope,
                "summaries_deleted": result.summaries_deleted,
                "profile_deleted": result.profile_deleted,
                "facts_deleted": result.facts_deleted,
                "states_deleted": result.states_deleted,
                "tasks_deleted": result.tasks_deleted,
                "total_deleted": result.total_deleted,
            },
        )
        return result

    def delete_fact(
        self,
        owner_id: str,
        fact_id: str,
        *,
        request_id: str | None = None,
    ) -> bool:
        """删除该 owner 的单条长期事实并落审计（被遗忘权细粒度，Step 034）。

        返回是否真的删了（``False`` = 事实不存在 / 不属于该 owner / 记忆禁用）。
        记忆禁用（``memory=None``）时返回 ``False`` 且不落审计；只有真发生删除
        才记一条 ``success=True`` 审计（只记 fact_id，不回存被删文本，数据最小化）。
        删除异常：记一条 ``success=False`` 审计后向上抛。
        """
        if self._memory is None:
            return False
        try:
            deleted = self._memory.delete_fact(owner_id, fact_id)
        except Exception as exc:  # noqa: BLE001 — 失败也要留痕，再抛给上层
            self._record_audit(
                action=AuditAction.MEMORY_FACT_DELETE,
                actor_id=owner_id,
                resource=owner_id,
                request_id=request_id,
                success=False,
                error=str(exc),
                extra={"fact_id": fact_id},
            )
            raise
        if deleted:
            self._record_audit(
                action=AuditAction.MEMORY_FACT_DELETE,
                actor_id=owner_id,
                resource=owner_id,
                request_id=request_id,
                success=True,
                error=None,
                extra={"fact_id": fact_id},
            )
        return deleted
    def _record_audit(
        self,
        *,
        action: str,
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

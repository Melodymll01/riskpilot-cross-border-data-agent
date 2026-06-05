"""``KbManagementUseCase``：知识库管理面业务编排（Step 016b）。

承担 admin 视角的 KB 全套操作：
- 读：``list_documents`` / ``get_document`` / ``count_chunks``
- 写：``delete_document`` / ``ingest_file`` / ``ingest_web``

编排 3 个端口（不直接依赖 ingestion / processing / chroma）：
- ``DocumentLoaderPort``：外部资源 → ``list[KbChunk]``
- ``EmbedPort``：``texts → embeddings``
- ``KbDocumentRepoPort``：CRUD + "先删后插"幂等写入

可选第 4 个端口（Step 021 增量）：
- ``AuditLogPort``：admin 写操作落审计流水；为 ``None`` 时跳过（向后兼容）

设计取舍：
- **不**承载授权（``api`` 层用 ``make_require_admin`` 守门，use case 内部不再
  二次判断 admin；保留对应 v1 行为）
- **不**承载文件系统操作（``ingest_file`` 只收 ``file_path`` 字符串；前端先
  POST 到 ``api/v2/documents`` 路由把文件落到 ``data/uploads/``，再调 use case）
- ``add_chunks`` 的"先删后插"语义在 ``KbDocumentRepoPort`` 层保证，use case
  不再重复 delete
- 返回 ``KbIngestResult`` 而非裸 ``int`` / ``bool``，便于 API 层 schema 直接 dump
- 审计是**副作用**：成功 / 失败均记录；audit 写失败仅打 warning 不影响主业务
- ``actor_id`` / ``request_id`` 由 API 层 ``Depends`` 注入；use case 不直接读取
  cookie / header，保持纯业务编排（领域无 web 概念）
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from domain.models import AuditAction, AuditEntry, KbChunk

if TYPE_CHECKING:
    from domain.models import KbDocument
    from domain.ports import (
        AuditLogPort,
        DocumentLoaderPort,
        EmbedPort,
        KbDocumentRepoPort,
    )

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class KbIngestResult:
    """``ingest_file`` / ``ingest_web`` 的返回值。"""

    success: bool
    source_name: str
    chunk_count: int
    message: str


class KbManagementUseCase:
    """KB 管理面 use case：读 3 个方法 + 写 3 个方法（写均落审计）。"""

    def __init__(
        self,
        *,
        kb_repo: KbDocumentRepoPort,
        loader: DocumentLoaderPort,
        embedder: EmbedPort,
        audit_log: AuditLogPort | None = None,
    ) -> None:
        self._repo = kb_repo
        self._loader = loader
        self._embedder = embedder
        self._audit = audit_log

    # ─── 读 ──────────────────────────────────────────────────────────

    def list_documents(self) -> list[KbDocument]:
        return self._repo.list_documents()

    def get_document(self, source_name: str) -> KbDocument | None:
        if not source_name:
            msg = "source_name 不能为空"
            raise ValueError(msg)
        return self._repo.get_document(source_name)

    def count_chunks(self) -> int:
        return self._repo.count_chunks()

    # ─── 写 ──────────────────────────────────────────────────────────

    def delete_document(
        self,
        source_name: str,
        *,
        actor_id: str | None = None,
        request_id: str | None = None,
    ) -> int:
        if not source_name:
            msg = "source_name 不能为空"
            raise ValueError(msg)
        try:
            deleted = self._repo.delete_document(source_name)
        except Exception as exc:
            self._record_audit(
                action=AuditAction.KB_DELETE,
                resource=source_name,
                actor_id=actor_id,
                request_id=request_id,
                success=False,
                error=str(exc),
                extra={"deleted_count": 0},
            )
            raise
        self._record_audit(
            action=AuditAction.KB_DELETE,
            resource=source_name,
            actor_id=actor_id,
            request_id=request_id,
            success=True,
            error=None,
            extra={"deleted_count": deleted},
        )
        return deleted

    def ingest_file(
        self,
        file_path: str,
        *,
        original_filename: str | None = None,
        category: str | None = None,
        actor_id: str | None = None,
        request_id: str | None = None,
    ) -> KbIngestResult:
        if not file_path:
            msg = "file_path 不能为空"
            raise ValueError(msg)
        try:
            chunks = self._loader.load_file(
                file_path,
                original_filename=original_filename,
                category=category,
            )
        except Exception as exc:
            self._record_audit(
                action=AuditAction.KB_INGEST_FILE,
                resource=original_filename or file_path,
                actor_id=actor_id,
                request_id=request_id,
                success=False,
                error=str(exc),
                extra={"chunk_count": 0},
            )
            raise
        if not chunks:
            fallback_name = original_filename or file_path
            result = KbIngestResult(
                success=False,
                source_name=fallback_name,
                chunk_count=0,
                message="文件内容为空或无法提取文本",
            )
            self._record_audit(
                action=AuditAction.KB_INGEST_FILE,
                resource=fallback_name,
                actor_id=actor_id,
                request_id=request_id,
                success=False,
                error=result.message,
                extra={"chunk_count": 0, "category": category},
            )
            return result
        result = self._ingest_chunks(
            chunks,
            success_msg=f"文件 [{chunks[0].source_name}] 导入成功",
            action=AuditAction.KB_INGEST_FILE,
            actor_id=actor_id,
            request_id=request_id,
            extra={"category": category},
        )
        return result

    def ingest_web(
        self,
        url: str,
        *,
        category: str | None = None,
        actor_id: str | None = None,
        request_id: str | None = None,
    ) -> KbIngestResult:
        if not url:
            msg = "url 不能为空"
            raise ValueError(msg)
        try:
            chunks = self._loader.load_web(url, category=category)
        except Exception as exc:
            self._record_audit(
                action=AuditAction.KB_INGEST_WEB,
                resource=url,
                actor_id=actor_id,
                request_id=request_id,
                success=False,
                error=str(exc),
                extra={"chunk_count": 0},
            )
            raise
        if not chunks:
            result = KbIngestResult(
                success=False,
                source_name=url,
                chunk_count=0,
                message="网页内容为空",
            )
            self._record_audit(
                action=AuditAction.KB_INGEST_WEB,
                resource=url,
                actor_id=actor_id,
                request_id=request_id,
                success=False,
                error=result.message,
                extra={"chunk_count": 0, "category": category},
            )
            return result
        return self._ingest_chunks(
            chunks,
            success_msg=f"网页 [{chunks[0].title or chunks[0].source_name}] 采集成功",
            action=AuditAction.KB_INGEST_WEB,
            actor_id=actor_id,
            request_id=request_id,
            extra={"category": category},
        )

    # ─── 私有 ────────────────────────────────────────────────────────

    def _ingest_chunks(
        self,
        chunks: list[KbChunk],
        *,
        success_msg: str,
        action: str,
        actor_id: str | None,
        request_id: str | None,
        extra: dict[str, object] | None = None,
    ) -> KbIngestResult:
        """共用写流程：embed → repo.add_chunks（端口内部先删后插）+ 审计。"""
        texts = [c.text for c in chunks]
        try:
            embeddings = self._embedder.embed(texts)
            self._repo.add_chunks(chunks, embeddings)
        except Exception as exc:
            self._record_audit(
                action=action,
                resource=chunks[0].source_name,
                actor_id=actor_id,
                request_id=request_id,
                success=False,
                error=str(exc),
                extra={"chunk_count": len(chunks), **(extra or {})},
            )
            raise
        result = KbIngestResult(
            success=True,
            source_name=chunks[0].source_name,
            chunk_count=len(chunks),
            message=success_msg,
        )
        self._record_audit(
            action=action,
            resource=chunks[0].source_name,
            actor_id=actor_id,
            request_id=request_id,
            success=True,
            error=None,
            extra={"chunk_count": len(chunks), **(extra or {})},
        )
        return result

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
        """统一审计写入：audit_log=None 跳过；写失败仅 warning。"""
        if self._audit is None:
            return
        try:
            self._audit.record(
                AuditEntry(
                    actor_id=actor_id or "system:unknown",
                    action=action,
                    resource=resource,
                    request_id=request_id,
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

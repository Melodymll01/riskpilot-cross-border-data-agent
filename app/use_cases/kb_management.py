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
- **不**承载授权细节（``api`` 层用 ``make_require_admin`` / ``make_require_owner``
  守门，本 use case 接收 ``viewer_id`` / ``viewer_is_admin`` 上下文做行级权限
  判断（Step 025a 起：普通用户只能写/删自己的私人文档，admin 可任意操作）
- **不**承载文件系统操作（``ingest_file`` 只收 ``file_path`` 字符串；前端先
  POST 到 ``api/v2/documents`` 路由把文件落到 ``data/uploads/``，再调 use case）
- ``add_chunks`` 的"先删后插"语义在 ``KbDocumentRepoPort`` 层保证，use case
  不再重复 delete
- 返回 ``KbIngestResult`` 而非裸 ``int`` / ``bool``，便于 API 层 schema 直接 dump
- 审计是**副作用**：成功 / 失败均记录；audit 写失败仅打 warning 不影响主业务
- ``actor_id`` / ``request_id`` 由 API 层 ``Depends`` 注入；use case 不直接读取
  cookie / header，保持纯业务编排（领域无 web 概念）

Step 025a 多租户：
- ``Scope`` 三态：``public`` / ``mine`` / ``all``
- ``list_documents`` / ``get_document`` 接收 ``viewer_id`` + ``scope``，转 owners 给 repo
- ``ingest_file`` / ``ingest_web`` 接收 ``owner_id``，写入到该 owner 的 chunk
- ``delete_document`` 接收 ``actor_is_admin``：True 不限，False 仅删 actor 自己的
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from app.request_context import get_request_id
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


Scope = Literal["public", "mine", "all"]
"""KB 可见范围三态：

- ``public``：仅 admin 入库的公共文档（owner_id is None）
- ``mine``：仅当前 viewer 上传的私人文档（owner_id == viewer_id）
- ``all``：``public`` ∪ ``mine``（admin 视角等价"全库"——见 ``_resolve_owners``）
"""


@dataclass(frozen=True)
class KbIngestResult:
    """``ingest_file`` / ``ingest_web`` 的返回值。"""

    success: bool
    source_name: str
    chunk_count: int
    message: str


def _resolve_owners(
    *,
    viewer_id: str | None,
    viewer_is_admin: bool,
    scope: Scope,
) -> list[str | None] | None:
    """根据 viewer + scope 计算传给 repo 的 ``owners`` 过滤集合。

    返回值语义：
    - ``None`` → 不过滤（admin scope=all：全库可见）
    - ``[None]`` → 仅公共
    - ``[viewer_id]`` → 仅自己
    - ``[None, viewer_id]`` → 公共 ∪ 自己（默认普通用户视角）

    异常：``scope=mine`` 但 ``viewer_id`` 为空 → 返回空列表 ``[]``，让 repo 返空集。
    """
    if scope == "public":
        return [None]
    if scope == "mine":
        return [viewer_id] if viewer_id else []
    # scope == "all"
    if viewer_is_admin:
        return None  # admin 全库视角
    if viewer_id is None:
        return [None]  # 匿名/未识别：等价 public
    return [None, viewer_id]


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

    def list_documents(
        self,
        *,
        viewer_id: str | None = None,
        viewer_is_admin: bool = False,
        scope: Scope = "all",
    ) -> list[KbDocument]:
        owners = _resolve_owners(
            viewer_id=viewer_id,
            viewer_is_admin=viewer_is_admin,
            scope=scope,
        )
        return self._repo.list_documents(owners=owners)

    def get_document(
        self,
        source_name: str,
        *,
        viewer_id: str | None = None,
        viewer_is_admin: bool = False,
        scope: Scope = "all",
    ) -> KbDocument | None:
        if not source_name:
            msg = "source_name 不能为空"
            raise ValueError(msg)
        owners = _resolve_owners(
            viewer_id=viewer_id,
            viewer_is_admin=viewer_is_admin,
            scope=scope,
        )
        return self._repo.get_document(source_name, owners=owners)

    def count_chunks(self) -> int:
        return self._repo.count_chunks()

    # ─── 写 ──────────────────────────────────────────────────────────

    def delete_document(
        self,
        source_name: str,
        *,
        actor_id: str | None = None,
        actor_is_admin: bool = False,
        request_id: str | None = None,
    ) -> int:
        """删除文档。

        Step 025a 权限规则：
        - admin (``actor_is_admin=True``)：可删任何 owner 的文档（含公共）
        - 非 admin：仅可删 ``owner_id == actor_id`` 的文档；不会误删公共 / 他人
        """
        if not source_name:
            msg = "source_name 不能为空"
            raise ValueError(msg)
        try:
            if actor_is_admin:
                deleted = self._repo.delete_document(source_name)
            else:
                # 非 admin 必须显式指定 owner_id=自己；actor_id 为空视为非法
                if not actor_id:
                    msg = "非管理员必须提供 actor_id"
                    raise ValueError(msg)
                deleted = self._repo.delete_document(source_name, owner_id=actor_id)
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
            extra={"deleted_count": deleted, "actor_is_admin": actor_is_admin},
        )
        return deleted

    def ingest_file(
        self,
        file_path: str,
        *,
        original_filename: str | None = None,
        category: str | None = None,
        owner_id: str | None = None,
        actor_id: str | None = None,
        request_id: str | None = None,
    ) -> KbIngestResult:
        """文件入库。

        Step 025a 权限规则（由 API 层 ``deps.py`` 把策略翻译为 ``owner_id`` 参数）：
        - admin 不传 ``owner_id`` 或显式传 ``None`` → 入到公共库
        - admin 显式传 ``owner_id=<某 user>`` → 入到某人私人库（罕见，运维场景）
        - 普通用户由 API 层强制把 ``owner_id`` 设为自己的 user_id
        """
        if not file_path:
            msg = "file_path 不能为空"
            raise ValueError(msg)
        try:
            chunks = self._loader.load_file(
                file_path,
                original_filename=original_filename,
                category=category,
                owner_id=owner_id,
            )
        except Exception as exc:
            self._record_audit(
                action=AuditAction.KB_INGEST_FILE,
                resource=original_filename or file_path,
                actor_id=actor_id,
                request_id=request_id,
                success=False,
                error=str(exc),
                extra={"chunk_count": 0, "owner_id": owner_id},
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
                extra={"chunk_count": 0, "category": category, "owner_id": owner_id},
            )
            return result
        result = self._ingest_chunks(
            chunks,
            success_msg=f"文件 [{chunks[0].source_name}] 导入成功",
            action=AuditAction.KB_INGEST_FILE,
            actor_id=actor_id,
            request_id=request_id,
            extra={"category": category, "owner_id": owner_id},
        )
        return result

    def ingest_web(
        self,
        url: str,
        *,
        category: str | None = None,
        owner_id: str | None = None,
        actor_id: str | None = None,
        request_id: str | None = None,
    ) -> KbIngestResult:
        if not url:
            msg = "url 不能为空"
            raise ValueError(msg)
        try:
            chunks = self._loader.load_web(url, category=category, owner_id=owner_id)
        except Exception as exc:
            self._record_audit(
                action=AuditAction.KB_INGEST_WEB,
                resource=url,
                actor_id=actor_id,
                request_id=request_id,
                success=False,
                error=str(exc),
                extra={"chunk_count": 0, "owner_id": owner_id},
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
                extra={"chunk_count": 0, "category": category, "owner_id": owner_id},
            )
            return result
        return self._ingest_chunks(
            chunks,
            success_msg=f"网页 [{chunks[0].title or chunks[0].source_name}] 采集成功",
            action=AuditAction.KB_INGEST_WEB,
            actor_id=actor_id,
            request_id=request_id,
            extra={"category": category, "owner_id": owner_id},
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
        """统一审计写入：audit_log=None 跳过；写失败仅 warning。

        Step 025d：request_id 语义为「显式形参优先 > contextvar > None」。
        HTTP 调用路径上 middleware 会填 contextvar；命令行/后台任务
        可显式给 request_id。
        """
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

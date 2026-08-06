"""SQLite DocumentRepoPort 实现。"""

from __future__ import annotations

import json
from typing import Any

from domain.document_content import DocumentParseSnapshot
from domain.documents import (
    CaseDocument,
    Document,
    DocumentStatus,
    DocumentVersion,
    ProcessingJob,
    ProcessingJobStatus,
    ProcessingStage,
)
from infra.storage._db import SqliteConnectionPool


class SqliteDocumentRepo:
    def __init__(self, pool: SqliteConnectionPool) -> None:
        self._pool = pool

    def create_upload(
        self,
        document: Document,
        version: DocumentVersion,
        binding: CaseDocument,
        job: ProcessingJob,
    ) -> None:
        self._validate_upload_graph(document, version, binding, job)
        conn = self._pool.get()
        with conn:
            conn.execute(
                """
                INSERT INTO documents
                    (document_id, workspace_id, logical_name, document_type,
                     status, created_by, current_version_id, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                _document_values(document),
            )
            conn.execute(
                """
                INSERT INTO document_versions
                    (version_id, document_id, version_number, object_key, sha256,
                     mime_type, size_bytes, parser_version, page_count, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                _version_values(version),
            )
            conn.execute(
                """
                INSERT INTO case_documents
                    (case_id, document_id, purpose, added_by, added_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    binding.case_id,
                    binding.document_id,
                    binding.purpose,
                    binding.added_by,
                    binding.added_at,
                ),
            )
            conn.execute(
                """
                INSERT INTO processing_jobs
                    (job_id, document_version_id, status, current_stage, progress,
                     error_code, error_message, retry_count, created_at, updated_at,
                     started_at, completed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                _job_values(job),
            )

    def get(self, document_id: str) -> Document | None:
        row = (
            self._pool.get()
            .execute(
                "SELECT * FROM documents WHERE document_id = ?",
                (document_id,),
            )
            .fetchone()
        )
        return None if row is None else _row_to_document(row)

    def get_version(self, version_id: str) -> DocumentVersion | None:
        row = (
            self._pool.get()
            .execute(
                "SELECT * FROM document_versions WHERE version_id = ?",
                (version_id,),
            )
            .fetchone()
        )
        return None if row is None else _row_to_version(row)

    def list_versions(self, document_id: str) -> list[DocumentVersion]:
        rows = (
            self._pool.get()
            .execute(
                """
                SELECT * FROM document_versions
                WHERE document_id = ?
                ORDER BY version_number DESC
                """,
                (document_id,),
            )
            .fetchall()
        )
        return [_row_to_version(row) for row in rows]

    def get_binding(self, case_id: str, document_id: str) -> CaseDocument | None:
        row = (
            self._pool.get()
            .execute(
                """
                SELECT * FROM case_documents
                WHERE case_id = ? AND document_id = ?
                """,
                (case_id, document_id),
            )
            .fetchone()
        )
        return None if row is None else _row_to_binding(row)

    def list_bindings_for_document(self, document_id: str) -> list[CaseDocument]:
        rows = self._pool.get().execute(
            """
            SELECT * FROM case_documents
            WHERE document_id = ?
            ORDER BY added_at, case_id
            """,
            (document_id,),
        ).fetchall()
        return [_row_to_binding(row) for row in rows]

    def list_for_case(
        self,
        case_id: str,
        *,
        include_deleted: bool = False,
    ) -> list[Document]:
        conn = self._pool.get()
        if include_deleted:
            rows = conn.execute(
                """
                SELECT d.*
                FROM documents AS d
                JOIN case_documents AS cd ON cd.document_id = d.document_id
                WHERE cd.case_id = ?
                ORDER BY d.updated_at DESC
                """,
                (case_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT d.*
                FROM documents AS d
                JOIN case_documents AS cd ON cd.document_id = d.document_id
                WHERE cd.case_id = ? AND d.status != 'deleted'
                ORDER BY d.updated_at DESC
                """,
                (case_id,),
            ).fetchall()
        return [_row_to_document(row) for row in rows]

    def get_job(self, job_id: str) -> ProcessingJob | None:
        row = (
            self._pool.get()
            .execute(
                "SELECT * FROM processing_jobs WHERE job_id = ?",
                (job_id,),
            )
            .fetchone()
        )
        return None if row is None else _row_to_job(row)

    def update_document(self, document: Document) -> None:
        conn = self._pool.get()
        conn.execute(
            """
            UPDATE documents SET
                workspace_id = ?,
                logical_name = ?,
                document_type = ?,
                status = ?,
                created_by = ?,
                current_version_id = ?,
                created_at = ?,
                updated_at = ?
            WHERE document_id = ?
            """,
            (
                document.workspace_id,
                document.logical_name,
                document.document_type,
                document.status,
                document.created_by,
                document.current_version_id,
                document.created_at,
                document.updated_at,
                document.document_id,
            ),
        )
        conn.commit()

    def update_job(self, job: ProcessingJob) -> None:
        conn = self._pool.get()
        conn.execute(
            """
            UPDATE processing_jobs SET
                document_version_id = ?,
                status = ?,
                current_stage = ?,
                progress = ?,
                error_code = ?,
                error_message = ?,
                retry_count = ?,
                created_at = ?,
                updated_at = ?,
                started_at = ?,
                completed_at = ?
            WHERE job_id = ?
            """,
            (
                job.document_version_id,
                job.status,
                job.current_stage,
                job.progress,
                job.error_code,
                job.error_message,
                job.retry_count,
                job.created_at,
                job.updated_at,
                job.started_at,
                job.completed_at,
                job.job_id,
            ),
        )
        conn.commit()

    def update_processing_state(
        self,
        document: Document,
        job: ProcessingJob,
    ) -> None:
        conn = self._pool.get()
        with conn:
            _update_document(conn, document)
            _update_job(conn, job)

    def save_parse_result(
        self,
        version: DocumentVersion,
        snapshot: DocumentParseSnapshot,
        document: Document,
        job: ProcessingJob,
    ) -> None:
        if snapshot.document_version_id != version.version_id:
            raise ValueError("解析快照必须属于当前 DocumentVersion")
        if version.document_id != document.document_id:
            raise ValueError("DocumentVersion 必须属于当前 Document")
        if job.document_version_id != version.version_id:
            raise ValueError("ProcessingJob 必须处理当前 DocumentVersion")
        conn = self._pool.get()
        with conn:
            conn.execute(
                """
                UPDATE document_versions SET
                    parser_version = ?,
                    page_count = ?
                WHERE version_id = ?
                """,
                (version.parser_version, version.page_count, version.version_id),
            )
            conn.execute(
                """
                INSERT INTO document_parse_snapshots
                    (snapshot_id, document_version_id, payload_json, parsed_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(document_version_id) DO UPDATE SET
                    snapshot_id = excluded.snapshot_id,
                    payload_json = excluded.payload_json,
                    parsed_at = excluded.parsed_at
                """,
                (
                    snapshot.snapshot_id,
                    snapshot.document_version_id,
                    snapshot.model_dump_json(),
                    snapshot.parsed_at,
                ),
            )
            _update_document(conn, document)
            _update_job(conn, job)

    def get_parse_snapshot(
        self, document_version_id: str
    ) -> DocumentParseSnapshot | None:
        row = self._pool.get().execute(
            """
            SELECT payload_json FROM document_parse_snapshots
            WHERE document_version_id = ?
            """,
            (document_version_id,),
        ).fetchone()
        if row is None:
            return None
        return DocumentParseSnapshot.model_validate(json.loads(row["payload_json"]))

    @staticmethod
    def _validate_upload_graph(
        document: Document,
        version: DocumentVersion,
        binding: CaseDocument,
        job: ProcessingJob,
    ) -> None:
        if version.document_id != document.document_id:
            raise ValueError("DocumentVersion 必须属于 Document")
        if binding.document_id != document.document_id:
            raise ValueError("CaseDocument 必须绑定当前 Document")
        if job.document_version_id != version.version_id:
            raise ValueError("ProcessingJob 必须处理当前 DocumentVersion")
        if document.current_version_id != version.version_id:
            raise ValueError("Document.current_version_id 必须指向当前版本")


def _document_values(document: Document) -> tuple[object, ...]:
    return (
        document.document_id,
        document.workspace_id,
        document.logical_name,
        document.document_type,
        document.status,
        document.created_by,
        document.current_version_id,
        document.created_at,
        document.updated_at,
    )


def _version_values(version: DocumentVersion) -> tuple[object, ...]:
    return (
        version.version_id,
        version.document_id,
        version.version_number,
        version.object_key,
        version.sha256,
        version.mime_type,
        version.size_bytes,
        version.parser_version,
        version.page_count,
        version.created_at,
    )


def _job_values(job: ProcessingJob) -> tuple[object, ...]:
    return (
        job.job_id,
        job.document_version_id,
        job.status,
        job.current_stage,
        job.progress,
        job.error_code,
        job.error_message,
        job.retry_count,
        job.created_at,
        job.updated_at,
        job.started_at,
        job.completed_at,
    )


def _update_document(conn: Any, document: Document) -> None:
    conn.execute(
        """
        UPDATE documents SET
            workspace_id = ?,
            logical_name = ?,
            document_type = ?,
            status = ?,
            created_by = ?,
            current_version_id = ?,
            created_at = ?,
            updated_at = ?
        WHERE document_id = ?
        """,
        (
            document.workspace_id,
            document.logical_name,
            document.document_type,
            document.status,
            document.created_by,
            document.current_version_id,
            document.created_at,
            document.updated_at,
            document.document_id,
        ),
    )


def _update_job(conn: Any, job: ProcessingJob) -> None:
    conn.execute(
        """
        UPDATE processing_jobs SET
            document_version_id = ?,
            status = ?,
            current_stage = ?,
            progress = ?,
            error_code = ?,
            error_message = ?,
            retry_count = ?,
            created_at = ?,
            updated_at = ?,
            started_at = ?,
            completed_at = ?
        WHERE job_id = ?
        """,
        (
            job.document_version_id,
            job.status,
            job.current_stage,
            job.progress,
            job.error_code,
            job.error_message,
            job.retry_count,
            job.created_at,
            job.updated_at,
            job.started_at,
            job.completed_at,
            job.job_id,
        ),
    )


def _row_to_document(row: Any) -> Document:
    return Document(
        document_id=row["document_id"],
        workspace_id=row["workspace_id"],
        logical_name=row["logical_name"],
        document_type=row["document_type"],
        status=_validate_document_status(row["status"]),
        created_by=row["created_by"],
        current_version_id=row["current_version_id"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _row_to_version(row: Any) -> DocumentVersion:
    return DocumentVersion(
        version_id=row["version_id"],
        document_id=row["document_id"],
        version_number=row["version_number"],
        object_key=row["object_key"],
        sha256=row["sha256"],
        mime_type=row["mime_type"],
        size_bytes=row["size_bytes"],
        parser_version=row["parser_version"],
        page_count=row["page_count"],
        created_at=row["created_at"],
    )


def _row_to_binding(row: Any) -> CaseDocument:
    return CaseDocument(
        case_id=row["case_id"],
        document_id=row["document_id"],
        purpose=row["purpose"],
        added_by=row["added_by"],
        added_at=row["added_at"],
    )


def _row_to_job(row: Any) -> ProcessingJob:
    return ProcessingJob(
        job_id=row["job_id"],
        document_version_id=row["document_version_id"],
        status=_validate_job_status(row["status"]),
        current_stage=_validate_processing_stage(row["current_stage"]),
        progress=row["progress"],
        error_code=row["error_code"],
        error_message=row["error_message"],
        retry_count=row["retry_count"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        started_at=row["started_at"],
        completed_at=row["completed_at"],
    )


def _validate_document_status(value: str) -> DocumentStatus:
    valid = {
        "uploaded",
        "queued",
        "parsing",
        "ocr",
        "chunking",
        "indexing",
        "ready",
        "failed",
        "deleted",
    }
    if value not in valid:
        raise ValueError(f"invalid document status in DB: {value!r}")
    return value


def _validate_job_status(value: str) -> ProcessingJobStatus:
    if value not in {"queued", "running", "completed", "failed", "cancelled"}:
        raise ValueError(f"invalid processing job status in DB: {value!r}")
    return value


def _validate_processing_stage(value: str) -> ProcessingStage:
    valid = {
        "validate",
        "persist",
        "extract_structure",
        "extract_text",
        "ocr",
        "extract_tables",
        "normalize",
        "chunk",
        "index_vector",
        "index_bm25",
        "quality_check",
        "ready",
    }
    if value not in valid:
        raise ValueError(f"invalid processing stage in DB: {value!r}")
    return value

"""SQLAlchemy DocumentRepoPort 实现。"""

from __future__ import annotations

from sqlalchemy import select

from domain.document_content import DocumentParseSnapshot
from domain.documents import CaseDocument, Document, DocumentVersion, ProcessingJob
from infra.storage.sqlalchemy.database import SqlAlchemyDatabase
from infra.storage.sqlalchemy.mapping import (
    require_datetime,
    require_timestamp,
    to_datetime,
    to_timestamp,
)
from infra.storage.sqlalchemy.models import (
    CaseDocumentRow,
    DocumentParseSnapshotRow,
    DocumentRow,
    DocumentVersionRow,
    ProcessingJobRow,
)


class SqlAlchemyDocumentRepo:
    def __init__(self, database: SqlAlchemyDatabase) -> None:
        self._database = database

    def create_upload(
        self,
        document: Document,
        version: DocumentVersion,
        binding: CaseDocument,
        job: ProcessingJob,
    ) -> None:
        _validate_upload_graph(document, version, binding, job)
        with self._database.session() as session:
            session.add(_document_row(document))
            session.add(_version_row(version))
            session.add(_binding_row(binding))
            session.add(_job_row(job))

    def get(self, document_id: str) -> Document | None:
        with self._database.read_session() as session:
            row = session.get(DocumentRow, document_id)
            return None if row is None else _document(row)

    def get_version(self, version_id: str) -> DocumentVersion | None:
        with self._database.read_session() as session:
            row = session.get(DocumentVersionRow, version_id)
            return None if row is None else _version(row)

    def list_versions(self, document_id: str) -> list[DocumentVersion]:
        statement = (
            select(DocumentVersionRow)
            .where(DocumentVersionRow.document_id == document_id)
            .order_by(DocumentVersionRow.version_number.desc())
        )
        with self._database.read_session() as session:
            return [_version(row) for row in session.scalars(statement)]

    def get_binding(self, case_id: str, document_id: str) -> CaseDocument | None:
        with self._database.read_session() as session:
            row = session.get(CaseDocumentRow, (case_id, document_id))
            return None if row is None else _binding(row)

    def list_bindings_for_document(self, document_id: str) -> list[CaseDocument]:
        statement = (
            select(CaseDocumentRow)
            .where(CaseDocumentRow.document_id == document_id)
            .order_by(CaseDocumentRow.added_at, CaseDocumentRow.case_id)
        )
        with self._database.read_session() as session:
            return [_binding(row) for row in session.scalars(statement)]

    def list_for_case(
        self,
        case_id: str,
        *,
        include_deleted: bool = False,
    ) -> list[Document]:
        statement = (
            select(DocumentRow)
            .join(
                CaseDocumentRow,
                CaseDocumentRow.document_id == DocumentRow.document_id,
            )
            .where(CaseDocumentRow.case_id == case_id)
        )
        if not include_deleted:
            statement = statement.where(DocumentRow.status != "deleted")
        statement = statement.order_by(DocumentRow.updated_at.desc())
        with self._database.read_session() as session:
            return [_document(row) for row in session.scalars(statement)]

    def get_job(self, job_id: str) -> ProcessingJob | None:
        with self._database.read_session() as session:
            row = session.get(ProcessingJobRow, job_id)
            return None if row is None else _job(row)

    def get_latest_job_for_version(
        self,
        document_version_id: str,
    ) -> ProcessingJob | None:
        statement = (
            select(ProcessingJobRow)
            .where(ProcessingJobRow.document_version_id == document_version_id)
            .order_by(
                ProcessingJobRow.created_at.desc(),
                ProcessingJobRow.job_id.desc(),
            )
            .limit(1)
        )
        with self._database.read_session() as session:
            row = session.scalar(statement)
            return None if row is None else _job(row)

    def update_document(self, document: Document) -> None:
        with self._database.session() as session:
            row = session.get(DocumentRow, document.document_id)
            if row is None:
                raise ValueError("待更新 Document 不存在")
            _apply_document(row, document)

    def update_job(self, job: ProcessingJob) -> None:
        with self._database.session() as session:
            row = session.get(ProcessingJobRow, job.job_id)
            if row is None:
                raise ValueError("待更新 ProcessingJob 不存在")
            _apply_job(row, job)

    def update_processing_state(
        self,
        document: Document,
        job: ProcessingJob,
    ) -> None:
        with self._database.session() as session:
            document_row = session.get(DocumentRow, document.document_id)
            job_row = session.get(ProcessingJobRow, job.job_id)
            if document_row is None or job_row is None:
                raise ValueError("Document 或 ProcessingJob 不存在")
            _apply_document(document_row, document)
            _apply_job(job_row, job)

    def save_parse_result(
        self,
        version: DocumentVersion,
        snapshot: DocumentParseSnapshot,
        document: Document,
        job: ProcessingJob,
    ) -> None:
        with self._database.session() as session:
            version_row = session.get(DocumentVersionRow, version.version_id)
            document_row = session.get(DocumentRow, document.document_id)
            job_row = session.get(ProcessingJobRow, job.job_id)
            if version_row is None or document_row is None or job_row is None:
                raise ValueError("DocumentVersion、Document 或 ProcessingJob 不存在")
            _apply_version(version_row, version)
            _apply_document(document_row, document)
            _apply_job(job_row, job)
            existing = session.scalar(
                select(DocumentParseSnapshotRow).where(
                    DocumentParseSnapshotRow.document_version_id == version.version_id
                )
            )
            if existing is None:
                session.add(_snapshot_row(snapshot))
            else:
                existing.snapshot_id = snapshot.snapshot_id
                existing.payload = snapshot.model_dump(mode="json")
                existing.parsed_at = require_datetime(snapshot.parsed_at)

    def get_parse_snapshot(
        self,
        document_version_id: str,
    ) -> DocumentParseSnapshot | None:
        statement = select(DocumentParseSnapshotRow).where(
            DocumentParseSnapshotRow.document_version_id == document_version_id
        )
        with self._database.read_session() as session:
            row = session.scalar(statement)
            if row is None:
                return None
            return DocumentParseSnapshot.model_validate(row.payload)


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
        raise ValueError("ProcessingJob 必须属于当前 DocumentVersion")
    if document.current_version_id != version.version_id:
        raise ValueError("Document.current_version_id 必须指向当前版本")


def _document_row(document: Document) -> DocumentRow:
    row = DocumentRow(document_id=document.document_id)
    _apply_document(row, document)
    return row


def _apply_document(row: DocumentRow, document: Document) -> None:
    row.workspace_id = document.workspace_id
    row.logical_name = document.logical_name
    row.document_type = document.document_type
    row.status = document.status
    row.created_by = document.created_by
    row.current_version_id = document.current_version_id
    row.created_at = require_datetime(document.created_at)
    row.updated_at = require_datetime(document.updated_at)


def _version_row(version: DocumentVersion) -> DocumentVersionRow:
    row = DocumentVersionRow(version_id=version.version_id)
    _apply_version(row, version)
    return row


def _apply_version(row: DocumentVersionRow, version: DocumentVersion) -> None:
    row.document_id = version.document_id
    row.version_number = version.version_number
    row.object_key = version.object_key
    row.sha256 = version.sha256
    row.mime_type = version.mime_type
    row.size_bytes = version.size_bytes
    row.parser_version = version.parser_version
    row.page_count = version.page_count
    row.created_at = require_datetime(version.created_at)


def _binding_row(binding: CaseDocument) -> CaseDocumentRow:
    return CaseDocumentRow(
        case_id=binding.case_id,
        document_id=binding.document_id,
        purpose=binding.purpose,
        added_by=binding.added_by,
        added_at=require_datetime(binding.added_at),
    )


def _job_row(job: ProcessingJob) -> ProcessingJobRow:
    row = ProcessingJobRow(job_id=job.job_id)
    _apply_job(row, job)
    return row


def _apply_job(row: ProcessingJobRow, job: ProcessingJob) -> None:
    row.document_version_id = job.document_version_id
    row.status = job.status
    row.current_stage = job.current_stage
    row.progress = job.progress
    row.error_code = job.error_code
    row.error_message = job.error_message
    row.retry_count = job.retry_count
    row.created_at = require_datetime(job.created_at)
    row.updated_at = require_datetime(job.updated_at)
    row.started_at = to_datetime(job.started_at)
    row.completed_at = to_datetime(job.completed_at)


def _snapshot_row(
    snapshot: DocumentParseSnapshot,
) -> DocumentParseSnapshotRow:
    return DocumentParseSnapshotRow(
        snapshot_id=snapshot.snapshot_id,
        document_version_id=snapshot.document_version_id,
        payload=snapshot.model_dump(mode="json"),
        parsed_at=require_datetime(snapshot.parsed_at),
    )


def _document(row: DocumentRow) -> Document:
    return Document(
        document_id=row.document_id,
        workspace_id=row.workspace_id,
        logical_name=row.logical_name,
        document_type=row.document_type,
        status=row.status,
        created_by=row.created_by,
        current_version_id=row.current_version_id,
        created_at=require_timestamp(row.created_at),
        updated_at=require_timestamp(row.updated_at),
    )


def _version(row: DocumentVersionRow) -> DocumentVersion:
    return DocumentVersion(
        version_id=row.version_id,
        document_id=row.document_id,
        version_number=row.version_number,
        object_key=row.object_key,
        sha256=row.sha256,
        mime_type=row.mime_type,
        size_bytes=row.size_bytes,
        parser_version=row.parser_version,
        page_count=row.page_count,
        created_at=require_timestamp(row.created_at),
    )


def _binding(row: CaseDocumentRow) -> CaseDocument:
    return CaseDocument(
        case_id=row.case_id,
        document_id=row.document_id,
        purpose=row.purpose,
        added_by=row.added_by,
        added_at=require_timestamp(row.added_at),
    )


def _job(row: ProcessingJobRow) -> ProcessingJob:
    return ProcessingJob(
        job_id=row.job_id,
        document_version_id=row.document_version_id,
        status=row.status,
        current_stage=row.current_stage,
        progress=row.progress,
        error_code=row.error_code,
        error_message=row.error_message,
        retry_count=row.retry_count,
        created_at=require_timestamp(row.created_at),
        updated_at=require_timestamp(row.updated_at),
        started_at=to_timestamp(row.started_at),
        completed_at=to_timestamp(row.completed_at),
    )

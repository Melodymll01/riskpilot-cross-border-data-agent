"""V2 DocumentManagementUseCase 测试。"""

from __future__ import annotations

import io
import time
import zipfile

import pytest

from app.use_cases import (
    CaseManagementUseCase,
    DocumentManagementUseCase,
    WorkspaceManagementUseCase,
)
from domain import (
    DocumentTooLarge,
    InvalidDocumentContent,
    ProcessingJobNotFound,
    UnsupportedDocumentType,
    WorkspaceAccessDenied,
)
from infra.tasks import ManualJobDispatcher
from tests.fakes import (
    FakeDocumentParser,
    FakeJobDispatcher,
    FakeObjectStore,
    InMemoryCaseRepo,
    InMemoryDocumentRepo,
    InMemoryWorkspaceRepo,
)


def _docx_bytes() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("word/document.xml", "<document/>")
    return buffer.getvalue()


def _compressed_docx_bytes(*, text_size: int) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("word/document.xml", "A" * text_size)
    return buffer.getvalue()


def _setup(
    *,
    max_upload_bytes: int = 1024 * 1024,
    document_repo: InMemoryDocumentRepo | None = None,
    bind_worker: bool = False,
    dispatcher: FakeJobDispatcher | None = None,
) -> tuple[
    WorkspaceManagementUseCase,
    CaseManagementUseCase,
    DocumentManagementUseCase,
    InMemoryDocumentRepo,
    FakeObjectStore,
    str,
]:
    workspace_repo = InMemoryWorkspaceRepo()
    case_repo = InMemoryCaseRepo()
    document_repo = document_repo or InMemoryDocumentRepo()
    object_store = FakeObjectStore()
    workspace_uc = WorkspaceManagementUseCase(workspace_repo)
    case_uc = CaseManagementUseCase(
        case_repo=case_repo,
        workspace_repo=workspace_repo,
    )
    document_uc = DocumentManagementUseCase(
        document_repo=document_repo,
        object_store=object_store,
        case_management=case_uc,
        workspace_management=workspace_uc,
        max_upload_bytes=max_upload_bytes,
        job_dispatcher=dispatcher or ManualJobDispatcher(),
    )
    if bind_worker:
        from app.workers import DocumentProcessingWorker

        document_uc.bind_processing_worker(
            DocumentProcessingWorker(
                document_repo=document_repo,
                object_store=object_store,
                parser=FakeDocumentParser(),
                clock=time.time,
            )
        )
    workspace = workspace_uc.create_workspace("github:alice", name="跨境合规组")
    case = case_uc.create_case(
        "github:alice",
        workspace_id=workspace.workspace_id,
        title="海外客服项目",
    )
    return (
        workspace_uc,
        case_uc,
        document_uc,
        document_repo,
        object_store,
        case.case_id,
    )


class TestUpload:
    def test_workspace_knowledge_requires_admin(self) -> None:
        workspace_uc, case_uc, uc, _, _, case_id = _setup()
        case = case_uc.get_case(case_id, "github:alice")
        workspace_uc.add_or_update_member(
            case.workspace_id,
            "github:alice",
            user_id="github:editor",
            role="editor",
        )
        with pytest.raises(WorkspaceAccessDenied):
            uc.upload(
                "github:editor",
                case_id=case_id,
                filename="policy.txt",
                content=b"workspace policy",
                document_type="workspace_knowledge",
            )
        uploaded = uc.upload(
            "github:alice",
            case_id=case_id,
            filename="policy.txt",
            content=b"workspace policy",
            document_type="workspace_knowledge",
        )
        assert uploaded.document.document_type == "workspace_knowledge"

    @pytest.mark.parametrize(
        ("filename", "content", "expected_mime"),
        [
            ("policy.txt", "制度文本".encode(), "text/plain"),
            ("policy.md", "# 制度".encode(), "text/markdown"),
            ("report.pdf", b"%PDF-1.7\nbody", "application/pdf"),
            (
                "contract.docx",
                _docx_bytes(),
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            ),
        ],
    )
    def test_supported_file_upload(
        self,
        filename: str,
        content: bytes,
        expected_mime: str,
    ) -> None:
        _, _, uc, repo, objects, case_id = _setup()
        result = uc.upload(
            "github:alice",
            case_id=case_id,
            filename=filename,
            content=content,
            purpose="案件证据",
        )

        assert result.document.status == "queued"
        assert result.version.mime_type == expected_mime
        assert result.version.size_bytes == len(content)
        assert result.version.sha256
        assert result.job.status == "queued"
        assert result.job.revision == 0
        assert result.job.current_stage == "extract_structure"
        assert repo.get(result.document.document_id) == result.document
        assert objects.read(result.version.object_key) == content

    def test_upload_enqueues_revision_scoped_background_job(self) -> None:
        dispatcher = FakeJobDispatcher()
        _, _, uc, _, _, case_id = _setup(dispatcher=dispatcher)

        result = uc.upload(
            "github:alice",
            case_id=case_id,
            filename="policy.txt",
            content=b"text",
        )

        assert dispatcher.enqueued == [(result.job.job_id, 0)]

    def test_dispatch_failure_is_persisted_and_returned_as_failed(self) -> None:
        dispatcher = FakeJobDispatcher(raise_on_enqueue=ConnectionError("redis unavailable"))
        _, _, uc, repo, _, case_id = _setup(dispatcher=dispatcher)

        result = uc.upload(
            "github:alice",
            case_id=case_id,
            filename="policy.txt",
            content=b"text",
        )

        assert result.job.status == "failed"
        assert result.job.error_code == "TASK_DISPATCH_FAILED"
        assert result.document.status == "failed"
        assert repo.get_job(result.job.job_id) == result.job

    def test_path_filename_is_reduced_to_basename(self) -> None:
        _, _, uc, _, _, case_id = _setup()
        result = uc.upload(
            "github:alice",
            case_id=case_id,
            filename="../../contract.pdf",
            content=b"%PDF-1.7\nbody",
        )
        assert result.document.logical_name == "contract.pdf"
        assert ".." not in result.version.object_key

    def test_unsupported_extension_rejected(self) -> None:
        _, _, uc, _, objects, case_id = _setup()
        with pytest.raises(UnsupportedDocumentType):
            uc.upload(
                "github:alice",
                case_id=case_id,
                filename="script.exe",
                content=b"MZ",
            )
        assert objects.objects == {}

    def test_fake_pdf_rejected(self) -> None:
        _, _, uc, _, _, case_id = _setup()
        with pytest.raises(InvalidDocumentContent, match="PDF"):
            uc.upload(
                "github:alice",
                case_id=case_id,
                filename="fake.pdf",
                content=b"not-pdf",
            )

    def test_invalid_docx_rejected(self) -> None:
        _, _, uc, _, _, case_id = _setup()
        with pytest.raises(InvalidDocumentContent, match="DOCX"):
            uc.upload(
                "github:alice",
                case_id=case_id,
                filename="broken.docx",
                content=b"not-a-zip",
            )

    def test_docx_zip_bomb_compression_ratio_rejected(self) -> None:
        _, _, uc, repo, objects, case_id = _setup(max_upload_bytes=1024 * 1024)

        with pytest.raises(InvalidDocumentContent, match="压缩比"):
            uc.upload(
                "github:alice",
                case_id=case_id,
                filename="bomb.docx",
                content=_compressed_docx_bytes(text_size=200_000),
            )

        assert repo.list_for_case(case_id) == []
        assert objects.objects == {}

    def test_docx_excessive_entry_count_rejected(self) -> None:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("[Content_Types].xml", "<Types/>")
            archive.writestr("word/document.xml", "<document/>")
            for index in range(2_001):
                archive.writestr(f"word/media/item_{index}.txt", "x")
        _, _, uc, repo, objects, case_id = _setup(max_upload_bytes=1024 * 1024)

        with pytest.raises(InvalidDocumentContent, match="条目数量"):
            uc.upload(
                "github:alice",
                case_id=case_id,
                filename="many.docx",
                content=buffer.getvalue(),
            )

        assert repo.list_for_case(case_id) == []
        assert objects.objects == {}

    def test_binary_text_rejected(self) -> None:
        _, _, uc, _, _, case_id = _setup()
        with pytest.raises(InvalidDocumentContent, match="空字节"):
            uc.upload(
                "github:alice",
                case_id=case_id,
                filename="binary.txt",
                content=b"a\x00b",
            )

    def test_oversize_rejected(self) -> None:
        _, _, uc, _, _, case_id = _setup(max_upload_bytes=4)
        with pytest.raises(DocumentTooLarge):
            uc.upload(
                "github:alice",
                case_id=case_id,
                filename="large.txt",
                content=b"12345",
            )

    def test_viewer_cannot_upload(self) -> None:
        workspace_uc, case_uc, uc, _, _, case_id = _setup()
        case = case_uc.get_case(case_id, "github:alice")
        workspace_uc.add_or_update_member(
            case.workspace_id,
            "github:alice",
            user_id="github:viewer",
            role="viewer",
        )
        with pytest.raises(WorkspaceAccessDenied, match="无权"):
            uc.upload(
                "github:viewer",
                case_id=case_id,
                filename="policy.txt",
                content=b"text",
            )


class TestCompensation:
    def test_repo_failure_removes_object(self) -> None:
        class FailingRepo(InMemoryDocumentRepo):
            def create_upload(self, *args: object, **kwargs: object) -> None:
                raise RuntimeError("database failed")

        _, _, uc, _, objects, case_id = _setup(document_repo=FailingRepo())
        with pytest.raises(RuntimeError, match="database failed"):
            uc.upload(
                "github:alice",
                case_id=case_id,
                filename="policy.txt",
                content=b"text",
            )
        assert objects.objects == {}


class TestQueries:
    def test_list_detail_download_and_job(self) -> None:
        _, _, uc, _, _, case_id = _setup()
        result = uc.upload(
            "github:alice",
            case_id=case_id,
            filename="policy.txt",
            content="制度文本".encode(),
            purpose="内部制度",
        )
        summaries = uc.list_case_documents(case_id, "github:alice")
        assert len(summaries) == 1
        assert summaries[0].document == result.document
        assert summaries[0].latest_job == result.job
        detail = uc.get_detail(
            case_id,
            result.document.document_id,
            "github:alice",
        )
        assert detail.version == result.version
        assert detail.latest_job == result.job
        download = uc.download(
            case_id,
            result.document.document_id,
            "github:alice",
        )
        assert download.content == "制度文本".encode()
        assert uc.get_job(result.job.job_id, "github:alice") == result.job

    def test_outsider_cannot_discover_job(self) -> None:
        _, _, uc, _, _, case_id = _setup()
        result = uc.upload(
            "github:alice",
            case_id=case_id,
            filename="policy.txt",
            content=b"text",
        )
        with pytest.raises(ProcessingJobNotFound):
            uc.get_job(result.job.job_id, "github:outsider")


class TestProcessingActions:
    def test_run_parse_stage_requires_bound_worker(self) -> None:
        _, _, uc, _, _, case_id = _setup()
        result = uc.upload(
            "github:alice",
            case_id=case_id,
            filename="policy.txt",
            content=b"text",
        )
        with pytest.raises(RuntimeError, match="Worker"):
            uc.run_parse_stage(result.job.job_id, "github:alice")

    def test_run_parse_stage_and_retry(self) -> None:
        _, _, uc, repo, objects, case_id = _setup(bind_worker=True)
        result = uc.upload(
            "github:alice",
            case_id=case_id,
            filename="policy.txt",
            content=b"text",
        )
        parsed = uc.run_parse_stage(result.job.job_id, "github:alice")
        assert parsed.job.current_stage == "chunk"
        assert parsed.document.status == "chunking"

        failed_at = max(parsed.job.updated_at, parsed.document.updated_at) + 1.0
        failed_job = parsed.job.fail(
            error_code="CHUNK_FAILED",
            at=failed_at,
        )
        failed_document = parsed.document.transition_to("failed", at=failed_at)
        repo.update_processing_state(
            failed_document,
            failed_job,
            expected_revision=parsed.job.revision,
        )
        retried = uc.retry_job(result.job.job_id, "github:alice")
        assert retried.status == "queued"
        assert retried.retry_count == 1
        assert repo.get(result.document.document_id).status == "queued"  # type: ignore[union-attr]
        assert objects.exists(result.version.object_key)

    def test_retry_enqueues_new_revision_and_cancel_revokes_previous_attempt(self) -> None:
        dispatcher = FakeJobDispatcher()
        _, _, uc, repo, _, case_id = _setup(bind_worker=True, dispatcher=dispatcher)
        result = uc.upload(
            "github:alice",
            case_id=case_id,
            filename="policy.txt",
            content=b"text",
        )
        parsed = uc.run_parse_stage(result.job.job_id, "github:alice")
        failed_at = max(parsed.job.updated_at, parsed.document.updated_at) + 1.0
        failed_job = parsed.job.fail(error_code="INDEX_FAILED", at=failed_at)
        failed_document = parsed.document.transition_to("failed", at=failed_at)
        repo.update_processing_state(
            failed_document,
            failed_job,
            expected_revision=parsed.job.revision,
        )

        retried = uc.retry_job(result.job.job_id, "github:alice")
        cancelled = uc.cancel_job(result.job.job_id, "github:alice")

        assert dispatcher.enqueued[-1] == (retried.job_id, retried.retry_count)
        assert dispatcher.cancelled == [(retried.job_id, retried.retry_count)]
        assert cancelled.status == "cancelled"
        assert cancelled.revision == retried.revision + 1

    def test_list_jobs_filters_by_status(self) -> None:
        _, _, uc, _, _, case_id = _setup()
        uploaded = uc.upload(
            "github:alice",
            case_id=case_id,
            filename="policy.txt",
            content=b"text",
        )

        assert uc.list_jobs(case_id, "github:alice") == [uploaded.job]
        assert uc.list_jobs(case_id, "github:alice", statuses={"failed"}) == []

    def test_viewer_cannot_run_parse_stage(self) -> None:
        workspace_uc, case_uc, uc, _, _, case_id = _setup(bind_worker=True)
        result = uc.upload(
            "github:alice",
            case_id=case_id,
            filename="policy.txt",
            content=b"text",
        )
        case = case_uc.get_case(case_id, "github:alice")
        workspace_uc.add_or_update_member(
            case.workspace_id,
            "github:alice",
            user_id="github:viewer",
            role="viewer",
        )
        with pytest.raises(WorkspaceAccessDenied):
            uc.run_parse_stage(result.job.job_id, "github:viewer")

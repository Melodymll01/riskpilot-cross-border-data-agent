"""EvidenceSearchUseCase 权限与作用域测试。"""

from __future__ import annotations

import pytest

from app.use_cases import (
    CaseManagementUseCase,
    EvidenceSearchUseCase,
    WorkspaceManagementUseCase,
)
from domain import CaseNotFound, EvidenceChunk
from tests.fakes import (
    FakeEmbed,
    FakeEvidenceIndex,
    InMemoryCaseRepo,
    InMemoryWorkspaceRepo,
)


def _setup():
    workspace_repo = InMemoryWorkspaceRepo()
    case_repo = InMemoryCaseRepo()
    workspace_uc = WorkspaceManagementUseCase(workspace_repo)
    case_uc = CaseManagementUseCase(
        case_repo=case_repo,
        workspace_repo=workspace_repo,
    )
    workspace = workspace_uc.create_workspace("github:alice", name="跨境合规组")
    case = case_uc.create_case(
        "github:alice",
        workspace_id=workspace.workspace_id,
        title="案件",
    )
    index = FakeEvidenceIndex()
    chunk = EvidenceChunk(
        chunk_id="evc_001",
        workspace_id=workspace.workspace_id,
        case_id=case.case_id,
        document_id="doc_001",
        document_version_id="ver_001",
        page_number=1,
        chunk_index=0,
        text="境外接收方责任",
        source_sha256="a" * 64,
        created_at=100.0,
    )
    index.chunks[chunk.chunk_id] = (chunk, [1.0])
    use_case = EvidenceSearchUseCase(
        evidence_index=index,
        embedder=FakeEmbed(dim=1),
        case_management=case_uc,
    )
    return use_case, index, case.case_id


class TestEvidenceSearch:
    def test_member_searches_only_case_scope(self) -> None:
        use_case, index, case_id = _setup()
        hits = use_case.search(
            "github:alice",
            case_id=case_id,
            query="责任",
        )
        assert [hit.chunk.chunk_id for hit in hits] == ["evc_001"]
        assert index.search_calls[0]["case_id"] == case_id

    def test_outsider_gets_case_not_found(self) -> None:
        use_case, _, case_id = _setup()
        with pytest.raises(CaseNotFound):
            use_case.search("github:outsider", case_id=case_id, query="责任")

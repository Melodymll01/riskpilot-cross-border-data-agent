"""SqliteCaseFactRepo 版本历史与证据测试。"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from domain import (
    Case,
    CaseDocument,
    CaseFact,
    CaseFactEvidence,
    CaseFactRepoPort,
    Document,
    DocumentVersion,
    ProcessingJob,
    Workspace,
    WorkspaceMembership,
)
from infra.storage import (
    SqliteCaseFactRepo,
    SqliteCaseRepo,
    SqliteDocumentRepo,
    SqliteWorkspaceRepo,
)
from infra.storage._db import SqliteConnectionPool


@pytest.fixture
def pool(tmp_path: Path) -> SqliteConnectionPool:
    return SqliteConnectionPool(str(tmp_path / "facts.db"))


@pytest.fixture
def fact_repo(pool: SqliteConnectionPool) -> SqliteCaseFactRepo:
    return SqliteCaseFactRepo(pool)


def _seed_case_and_document(pool: SqliteConnectionPool) -> None:
    workspace_repo = SqliteWorkspaceRepo(pool)
    case_repo = SqliteCaseRepo(pool)
    document_repo = SqliteDocumentRepo(pool)
    workspace_repo.create(
        Workspace(
            workspace_id="ws_001",
            name="跨境合规组",
            created_by="github:alice",
            created_at=100.0,
            updated_at=100.0,
        ),
        WorkspaceMembership(
            workspace_id="ws_001",
            user_id="github:alice",
            role="admin",
            joined_at=100.0,
        ),
    )
    case_repo.create(
        Case(
            case_id="case_001",
            workspace_id="ws_001",
            title="案件",
            owner_id="github:alice",
            created_at=100.0,
            updated_at=100.0,
        )
    )
    version = DocumentVersion(
        version_id="ver_001",
        document_id="doc_001",
        version_number=1,
        object_key="ws/doc/ver/source.txt",
        sha256="a" * 64,
        mime_type="text/plain",
        size_bytes=10,
        created_at=100.0,
    )
    document_repo.create_upload(
        Document(
            document_id="doc_001",
            workspace_id="ws_001",
            logical_name="source.txt",
            document_type="case_material",
            status="ready",
            created_by="github:alice",
            current_version_id=version.version_id,
            created_at=100.0,
            updated_at=100.0,
        ),
        version,
        CaseDocument(
            case_id="case_001",
            document_id="doc_001",
            added_by="github:alice",
            added_at=100.0,
        ),
        ProcessingJob(
            job_id="job_001",
            document_version_id=version.version_id,
            created_at=100.0,
            updated_at=100.0,
        ),
    )


def _fact(**overrides: object) -> CaseFact:
    values: dict[str, object] = {
        "fact_id": "fact_001",
        "case_id": "case_001",
        "field_name": "important_data_involved",
        "value": True,
        "source_type": "document",
        "confidence": 0.9,
        "criticality": "critical",
        "created_by": "github:alice",
        "created_at": 100.0,
        "updated_at": 100.0,
    }
    values.update(overrides)
    return CaseFact(**values)  # type: ignore[arg-type]


def _evidence(fact: CaseFact, evidence_id: str = "evidence_001") -> CaseFactEvidence:
    return CaseFactEvidence(
        evidence_id=evidence_id,
        case_id=fact.case_id,
        fact_id=fact.fact_id,
        fact_version=fact.version,
        document_id="doc_001",
        document_version_id="ver_001",
        page_number=1,
        quote="涉及重要数据",
        confidence=0.9,
        created_at=fact.updated_at,
    )


class TestCaseFactRepo:
    def test_satisfies_port(self, fact_repo: SqliteCaseFactRepo) -> None:
        assert isinstance(fact_repo, CaseFactRepoPort)

    def test_create_round_trip(
        self,
        pool: SqliteConnectionPool,
        fact_repo: SqliteCaseFactRepo,
    ) -> None:
        _seed_case_and_document(pool)
        fact = _fact()
        evidence = _evidence(fact)
        fact_repo.create(fact, [evidence])

        assert fact_repo.get(fact.fact_id) == fact
        assert fact_repo.get_version(fact.fact_id, 1) == fact
        assert fact_repo.list_evidence(fact.fact_id, fact_version=1) == [evidence]
        assert fact_repo.list_for_case("case_001") == [fact]

    def test_revision_preserves_version_history(
        self,
        pool: SqliteConnectionPool,
        fact_repo: SqliteCaseFactRepo,
    ) -> None:
        _seed_case_and_document(pool)
        original = _fact()
        fact_repo.create(original, [_evidence(original)])
        revised = original.propose_revision(
            value=False,
            source_type="document",
            confidence=0.8,
            actor_id="github:editor",
            at=101.0,
        )
        version_two_evidence = _evidence(revised, "evidence_002")
        fact_repo.save_revision(revised, [version_two_evidence])

        assert fact_repo.get(original.fact_id) == revised
        assert fact_repo.get_version(original.fact_id, 1) == original
        assert fact_repo.get_version(original.fact_id, 2) == revised
        assert fact_repo.list_evidence(original.fact_id, fact_version=1)[0].fact_version == 1
        assert fact_repo.list_evidence(original.fact_id, fact_version=2) == [version_two_evidence]

    def test_status_update_does_not_create_new_version(
        self,
        pool: SqliteConnectionPool,
        fact_repo: SqliteCaseFactRepo,
    ) -> None:
        _seed_case_and_document(pool)
        fact = _fact()
        fact_repo.create(fact, [_evidence(fact)])
        confirmed = fact.transition_to(
            "confirmed",
            actor_id="github:reviewer",
            at=101.0,
        )
        fact_repo.update_status(confirmed)
        assert fact_repo.get(fact.fact_id) == confirmed
        assert fact_repo.get_version(fact.fact_id, 1) == confirmed
        assert fact_repo.get_version(fact.fact_id, 2) is None

    def test_update_statuses_updates_peers_atomically(
        self,
        pool: SqliteConnectionPool,
        fact_repo: SqliteCaseFactRepo,
    ) -> None:
        _seed_case_and_document(pool)
        original = _fact(fact_id="fact_original")
        candidate = _fact(
            fact_id="fact_candidate",
            value=False,
            status="conflicting",
        )
        fact_repo.create_many(
            [
                (original, [_evidence(original, "evidence_original")]),
                (candidate, [_evidence(candidate, "evidence_candidate")]),
            ]
        )
        confirmed = candidate.transition_to(
            "confirmed",
            actor_id="github:reviewer",
            at=101.0,
        )
        rejected = original.transition_to(
            "rejected",
            actor_id="github:reviewer",
            at=101.0,
        )

        fact_repo.update_statuses([confirmed, rejected])

        assert fact_repo.get(candidate.fact_id) == confirmed
        assert fact_repo.get(original.fact_id) == rejected
        assert fact_repo.get_version(candidate.fact_id, 1) == confirmed
        assert fact_repo.get_version(original.fact_id, 1) == rejected

    def test_evidence_version_mismatch_rejected(
        self,
        pool: SqliteConnectionPool,
        fact_repo: SqliteCaseFactRepo,
    ) -> None:
        _seed_case_and_document(pool)
        fact = _fact()
        wrong = _evidence(fact).model_copy(update={"fact_version": 2})
        with pytest.raises(ValueError, match="版本"):
            fact_repo.create(fact, [wrong])

    def test_cross_case_document_evidence_rejected(
        self,
        pool: SqliteConnectionPool,
        fact_repo: SqliteCaseFactRepo,
    ) -> None:
        _seed_case_and_document(pool)
        fact = _fact()
        pool.get().execute(
            "DELETE FROM case_documents WHERE case_id = ? AND document_id = ?",
            ("case_001", "doc_001"),
        )
        pool.get().commit()
        with pytest.raises(ValueError, match="未绑定"):
            fact_repo.create(fact, [_evidence(fact)])

    def test_create_many_is_atomic(
        self,
        pool: SqliteConnectionPool,
        fact_repo: SqliteCaseFactRepo,
    ) -> None:
        _seed_case_and_document(pool)
        first = _fact(fact_id="fact_first")
        second = _fact(fact_id="fact_second", field_name="destination_country", value="DE")
        invalid_evidence = _evidence(second, "evidence_second").model_copy(
            update={"fact_version": 2}
        )

        with pytest.raises(ValueError, match="版本"):
            fact_repo.create_many(
                [
                    (first, [_evidence(first, "evidence_first")]),
                    (second, [invalid_evidence]),
                ]
            )

        assert fact_repo.list_for_case("case_001") == []

    def test_create_many_rolls_back_after_database_error(
        self,
        pool: SqliteConnectionPool,
        fact_repo: SqliteCaseFactRepo,
    ) -> None:
        _seed_case_and_document(pool)
        first = _fact(fact_id="fact_first")
        second = _fact(fact_id="fact_first", field_name="destination_country", value="DE")

        with pytest.raises(sqlite3.IntegrityError):
            fact_repo.create_many(
                [
                    (first, [_evidence(first, "evidence_first")]),
                    (second, [_evidence(second, "evidence_second")]),
                ]
            )

        assert fact_repo.list_for_case("case_001") == []

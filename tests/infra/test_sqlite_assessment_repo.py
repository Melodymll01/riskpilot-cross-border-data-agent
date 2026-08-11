"""SqliteAssessmentRepo 版本切换与 Bundle 测试。"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from domain import (
    ActionItem,
    Assessment,
    AssessmentBundle,
    AssessmentEvidenceCitation,
    AssessmentRepoPort,
    Case,
    Finding,
    PolicyEvaluation,
    Workspace,
    WorkspaceMembership,
)
from infra.storage import SqliteAssessmentRepo, SqliteCaseRepo, SqliteWorkspaceRepo
from infra.storage._db import SqliteConnectionPool


@pytest.fixture
def pool(tmp_path: Path) -> SqliteConnectionPool:
    return SqliteConnectionPool(str(tmp_path / "assessment.db"))


@pytest.fixture
def repo(pool: SqliteConnectionPool) -> SqliteAssessmentRepo:
    return SqliteAssessmentRepo(pool)


def _seed_case(pool: SqliteConnectionPool) -> Case:
    workspace_repo = SqliteWorkspaceRepo(pool)
    case_repo = SqliteCaseRepo(pool)
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
    case = Case(
        case_id="case_001",
        workspace_id="ws_001",
        title="案件",
        assessment_date=date(2026, 8, 6),
        owner_id="github:alice",
        created_at=100.0,
        updated_at=100.0,
    )
    case_repo.create(case)
    return case


def _bundle(
    assessment_id: str,
    version: int,
    *,
    status: str = "review_required",
    created_at: float = 100.0,
) -> AssessmentBundle:
    evaluation = PolicyEvaluation(
        rule_id="rule_001",
        ruleset_version="synthetic-v1",
        status="triggered",
        consumed_fact_versions={"flag": 1},
        result={"risk_level": "high", "candidate_path": "synthetic"},
        source_clause_ids=["clause_001"],
    )
    assessment = Assessment(
        assessment_id=assessment_id,
        case_id="case_001",
        version=version,
        status=status,  # type: ignore[arg-type]
        assessment_date=date(2026, 8, 6),
        jurisdiction="CN",
        ruleset_version="synthetic-v1",
        fact_versions={"flag": 1},
        policy_evaluations=[evaluation],
        risk_level="high",
        candidate_paths=["synthetic"],
        created_at=created_at,
        updated_at=created_at,
    )
    finding = Finding(
        finding_id=f"finding_{version}",
        assessment_id=assessment_id,
        finding_type="rule_trigger",
        severity="high",
        title="规则触发",
        fact_ids=["fact_001"],
        evidence_ids=[f"assessment_evidence_{version}"],
        rule_ids=["rule_001"],
        clause_ids=["clause_001"],
    )
    citation = AssessmentEvidenceCitation(
        citation_id=f"assessment_evidence_{version}",
        assessment_id=assessment_id,
        source_evidence_id="evidence_001",
        fact_id="fact_001",
        fact_version=1,
        document_id="doc_001",
        document_version_id="ver_001",
        page_number=1,
        quote="涉及重要数据",
        source_sha256="a" * 64,
        created_at=created_at,
    )
    action = ActionItem(
        action_id=f"action_{version}",
        assessment_id=assessment_id,
        title="补充材料",
        priority="high",
        related_finding_ids=[finding.finding_id],
    )
    return AssessmentBundle(
        assessment=assessment,
        findings=[finding],
        action_items=[action],
        evidence_citations=[citation],
    )


class TestSqliteAssessmentRepo:
    def test_satisfies_port(self, repo: SqliteAssessmentRepo) -> None:
        assert isinstance(repo, AssessmentRepoPort)

    def test_create_and_round_trip(
        self,
        pool: SqliteConnectionPool,
        repo: SqliteAssessmentRepo,
    ) -> None:
        case = _seed_case(pool)
        bundle = _bundle("assessment_001", 1)
        updated_case = case.model_copy(
            update={
                "status": "review_required",
                "active_assessment_id": bundle.assessment.assessment_id,
                "updated_at": 101.0,
            }
        )
        repo.create_version(bundle, None, updated_case)
        assert repo.get(bundle.assessment.assessment_id) == bundle
        assert repo.get_active(case.case_id) == bundle
        assert repo.list_for_case(case.case_id) == [bundle.assessment]
        assert repo.next_version(case.case_id) == 2
        loaded_case = SqliteCaseRepo(pool).get(case.case_id)
        assert loaded_case is not None
        assert loaded_case.active_assessment_id == bundle.assessment.assessment_id
        assert loaded_case.status == "review_required"

    def test_new_version_supersedes_previous_atomically(
        self,
        pool: SqliteConnectionPool,
        repo: SqliteAssessmentRepo,
    ) -> None:
        case = _seed_case(pool)
        first = _bundle("assessment_001", 1)
        repo.create_version(
            first,
            None,
            case.model_copy(
                update={
                    "status": "review_required",
                    "active_assessment_id": first.assessment.assessment_id,
                    "updated_at": 101.0,
                }
            ),
        )
        superseded = first.assessment.transition_to(
            "superseded",
            actor_id="github:alice",
            at=102.0,
        )
        second = _bundle("assessment_002", 2, created_at=102.0)
        repo.create_version(
            second,
            superseded,
            case.model_copy(
                update={
                    "status": "review_required",
                    "active_assessment_id": second.assessment.assessment_id,
                    "updated_at": 102.0,
                }
            ),
        )
        versions = repo.list_for_case(case.case_id)
        assert [assessment.version for assessment in versions] == [2, 1]
        assert versions[1].status == "superseded"
        assert repo.get_active(case.case_id) == second

    def test_invalid_version_does_not_mutate_previous(
        self,
        pool: SqliteConnectionPool,
        repo: SqliteAssessmentRepo,
    ) -> None:
        case = _seed_case(pool)
        first = _bundle("assessment_001", 1)
        repo.create_version(
            first,
            None,
            case.model_copy(
                update={
                    "status": "review_required",
                    "active_assessment_id": first.assessment.assessment_id,
                    "updated_at": 101.0,
                }
            ),
        )
        superseded = first.assessment.transition_to(
            "superseded",
            actor_id="github:alice",
            at=102.0,
        )
        invalid = _bundle("assessment_003", 3, created_at=102.0)
        with pytest.raises(ValueError, match="递增"):
            repo.create_version(
                invalid,
                superseded,
                case.model_copy(
                    update={
                        "status": "review_required",
                        "active_assessment_id": invalid.assessment.assessment_id,
                        "updated_at": 102.0,
                    }
                ),
            )
        assert repo.get(first.assessment.assessment_id) == first

    def test_review_updates_assessment_and_case_atomically(
        self,
        pool: SqliteConnectionPool,
        repo: SqliteAssessmentRepo,
    ) -> None:
        case = _seed_case(pool)
        bundle = _bundle("assessment_001", 1)
        review_case = case.model_copy(
            update={
                "status": "review_required",
                "active_assessment_id": bundle.assessment.assessment_id,
                "updated_at": 101.0,
            }
        )
        repo.create_version(bundle, None, review_case)
        approved = bundle.assessment.transition_to(
            "approved",
            actor_id="github:reviewer",
            comment="审核通过",
            at=102.0,
        )
        completed_case = review_case.transition_to("completed", at=102.0)

        repo.save_review(approved, completed_case)

        loaded = repo.get(bundle.assessment.assessment_id)
        assert loaded is not None
        assert loaded.assessment == approved
        loaded_case = SqliteCaseRepo(pool).get(case.case_id)
        assert loaded_case is not None
        assert loaded_case.status == "completed"

    def test_review_rejects_non_active_assessment_without_partial_write(
        self,
        pool: SqliteConnectionPool,
        repo: SqliteAssessmentRepo,
    ) -> None:
        case = _seed_case(pool)
        bundle = _bundle("assessment_001", 1)
        review_case = case.model_copy(
            update={
                "status": "review_required",
                "active_assessment_id": bundle.assessment.assessment_id,
                "updated_at": 101.0,
            }
        )
        repo.create_version(bundle, None, review_case)
        approved = bundle.assessment.transition_to(
            "approved",
            actor_id="github:reviewer",
            at=102.0,
        )
        invalid_case = review_case.model_copy(
            update={
                "active_assessment_id": "assessment_other",
                "status": "completed",
                "updated_at": 102.0,
            }
        )

        with pytest.raises(ValueError, match="活动"):
            repo.save_review(approved, invalid_case)

        assert repo.get(bundle.assessment.assessment_id) == bundle

    def test_review_rolls_back_assessment_when_case_state_is_stale(
        self,
        pool: SqliteConnectionPool,
        repo: SqliteAssessmentRepo,
    ) -> None:
        case = _seed_case(pool)
        bundle = _bundle("assessment_001", 1)
        review_case = case.model_copy(
            update={
                "status": "review_required",
                "active_assessment_id": bundle.assessment.assessment_id,
                "updated_at": 101.0,
            }
        )
        repo.create_version(bundle, None, review_case)
        SqliteCaseRepo(pool).update(
            review_case.model_copy(update={"status": "assessing", "updated_at": 101.5})
        )
        approved = bundle.assessment.transition_to(
            "approved",
            actor_id="github:reviewer",
            at=102.0,
        )
        completed_case = review_case.transition_to("completed", at=102.0)

        with pytest.raises(ValueError, match="活动"):
            repo.save_review(approved, completed_case)

        assert repo.get(bundle.assessment.assessment_id) == bundle

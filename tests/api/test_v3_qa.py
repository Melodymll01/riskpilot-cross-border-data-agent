"""V3 Evidence QA API 端到端测试。"""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from app.container import AppContainer
from domain import (
    ActionItem,
    Assessment,
    AssessmentBundle,
    ClaimSupportJudgement,
    ClaimSupportResult,
    EvidenceQAClaim,
    EvidenceQADraft,
    Finding,
)
from domain.models import Chunk
from tests.fakes import (
    FakeClaimSupportVerifier,
    FakeEvidenceQAGenerator,
    FakeRetrieve,
)


def _switch_actor(client: TestClient, actor_id: str) -> None:
    container: AppContainer = client.app.state.container  # type: ignore[attr-defined]
    token = container.auth.issue_jwt(actor_id)
    client.cookies.set(container.settings.cookie_name, token)


def _setup_case(client: TestClient) -> tuple[str, str]:
    workspace = client.post(
        "/api/v3/workspaces",
        json={"name": "跨境合规组"},
    ).json()
    workspace_id = workspace["workspace_id"]
    client.put(
        f"/api/v3/workspaces/{workspace_id}/members/github:editor",
        json={"role": "editor"},
    )
    _switch_actor(client, "github:editor")
    case = client.post(
        "/api/v3/cases",
        json={
            "workspace_id": workspace_id,
            "title": "海外客服项目",
            "assessment_date": "2026-08-07",
        },
    )
    assert case.status_code == 201
    return workspace_id, case.json()["case_id"]


def _upload_and_index(
    client: TestClient,
    case_id: str,
    *,
    document_type: str = "case_material",
    content: str = "境外接收方应承担安全保护责任",
) -> dict[str, Any]:
    uploaded = client.post(
        f"/api/v3/cases/{case_id}/documents",
        files={"file": ("case.txt", content.encode(), "text/plain")},
        data={"document_type": document_type},
    )
    assert uploaded.status_code == 202
    body = uploaded.json()
    job_id = body["job"]["job_id"]
    assert client.post(f"/api/v3/processing-jobs/{job_id}/parse").status_code == 200
    assert client.post(f"/api/v3/processing-jobs/{job_id}/index").status_code == 200
    return body


def _answer(
    client: TestClient,
    *,
    question: str,
    corpora: list[str],
    workspace_id: str | None = None,
    case_id: str | None = None,
    assessment_id: str | None = None,
) -> Any:
    payload: dict[str, object] = {
        "question": question,
        "corpora": corpora,
    }
    if workspace_id is not None:
        payload["workspace_id"] = workspace_id
    if case_id is not None:
        payload["case_id"] = case_id
    if assessment_id is not None:
        payload["assessment_id"] = assessment_id
    return client.post("/api/v3/qa", json=payload)


def _seed_assessment(client: TestClient, case_id: str) -> str:
    container: AppContainer = client.app.state.container  # type: ignore[attr-defined]
    case = container.case_repo.get(case_id)
    assert case is not None
    assessment = Assessment(
        assessment_id="assessment_api_001",
        case_id=case_id,
        version=1,
        status="review_required",
        assessment_date=case.assessment_date,
        jurisdiction=case.jurisdiction,
        ruleset_version="synthetic-v1",
        risk_level="high",
        candidate_paths=["security_assessment"],
        created_at=100.0,
        updated_at=100.0,
    )
    finding = Finding(
        finding_id="finding_api_001",
        assessment_id=assessment.assessment_id,
        finding_type="rule_trigger",
        severity="high",
        title="重要数据规则已触发",
        description="需要申报数据出境安全评估",
        clause_ids=["clause_001"],
        rule_ids=["rule_001"],
    )
    action = ActionItem(
        action_id="action_api_001",
        assessment_id=assessment.assessment_id,
        title="提交安全评估材料",
        priority="high",
        related_finding_ids=[finding.finding_id],
    )
    updated_case = case.model_copy(update={"active_assessment_id": assessment.assessment_id})
    container.assessment_repo.create_version(
        AssessmentBundle(
            assessment=assessment,
            findings=[finding],
            action_items=[action],
        ),
        None,
        updated_case,
    )
    return assessment.assessment_id


class TestV3EvidenceQA:
    def test_requires_authentication(self, client: TestClient) -> None:
        response = client.post(
            "/api/v3/qa",
            json={"question": "问题", "corpora": ["regulatory"]},
        )
        assert response.status_code == 401

    def test_case_qa_returns_verified_versioned_citation(
        self,
        authed_client: tuple[TestClient, dict[str, Any]],
    ) -> None:
        client, _ = authed_client
        _, case_id = _setup_case(client)
        uploaded = _upload_and_index(client, case_id)

        response = _answer(
            client,
            question="境外接收方有什么义务？",
            corpora=["case"],
            case_id=case_id,
        )

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "answered"
        assert body["answer"].endswith("[E1]")
        assert body["verification"]["method"] == "structural_v1"
        assert body["verification"]["valid"] is True
        assert body["support_verification"]["method"] == "independent_llm_v1"
        assert body["support_verification"]["valid"] is True
        citation = body["citations"][0]
        assert citation["document_id"] == uploaded["document"]["document_id"]
        assert citation["document_version_id"] == uploaded["version"]["version_id"]
        assert citation["page_number"] == 1
        assert citation["source_sha256"] == uploaded["version"]["sha256"]

    def test_workspace_qa_only_sees_admin_uploaded_workspace_knowledge(
        self,
        authed_client: tuple[TestClient, dict[str, Any]],
    ) -> None:
        client, user = authed_client
        workspace_id, case_id = _setup_case(client)
        response = client.post(
            f"/api/v3/cases/{case_id}/documents",
            files={"file": ("editor.txt", b"editor", "text/plain")},
            data={"document_type": "workspace_knowledge"},
        )
        assert response.status_code == 403

        _switch_actor(client, user["user_id"])
        workspace_doc = _upload_and_index(
            client,
            case_id,
            document_type="workspace_knowledge",
            content="Workspace 制度要求完成审批",
        )
        _switch_actor(client, "github:editor")
        _upload_and_index(
            client,
            case_id,
            content="普通案件材料不应进入 Workspace 范围",
        )

        answer = _answer(
            client,
            question="内部制度要求什么？",
            corpora=["workspace"],
            workspace_id=workspace_id,
        )

        assert answer.status_code == 200
        citations = answer.json()["citations"]
        assert [citation["document_id"] for citation in citations] == [
            workspace_doc["document"]["document_id"]
        ]

    def test_regulatory_qa_uses_public_retrieval(
        self,
        authed_client: tuple[TestClient, dict[str, Any]],
    ) -> None:
        client, _ = authed_client
        container: AppContainer = client.app.state.container  # type: ignore[attr-defined]
        retriever = FakeRetrieve(
            chunks=[
                Chunk(
                    chunk_id="law_001",
                    text="个人信息出境应具备法定条件之一。",
                    source_type="law",
                    source_name="个人信息保护法",
                    title="第三十八条",
                    score=0.9,
                )
            ]
        )
        container.evidence_qa._retriever = retriever

        response = _answer(
            client,
            question="个人信息出境有什么条件？",
            corpora=["regulatory"],
        )

        assert response.status_code == 200
        assert response.json()["citations"][0]["corpus"] == "regulatory"
        assert retriever.calls[0]["owner_id"] is None

    def test_assessment_qa_explains_authorized_assessment(
        self,
        authed_client: tuple[TestClient, dict[str, Any]],
    ) -> None:
        client, _ = authed_client
        _, case_id = _setup_case(client)
        assessment_id = _seed_assessment(client, case_id)

        response = _answer(
            client,
            question="为什么是高风险？",
            corpora=["assessment"],
            case_id=case_id,
            assessment_id=assessment_id,
        )

        assert response.status_code == 200
        citation = response.json()["citations"][0]
        assert citation["corpus"] == "assessment"
        assert citation["assessment_id"] == assessment_id
        assert "重要数据规则已触发" in citation["quote"]

    def test_no_evidence_and_unsupported_claim_fail_closed(
        self,
        authed_client: tuple[TestClient, dict[str, Any]],
    ) -> None:
        client, _ = authed_client
        _, case_id = _setup_case(client)
        no_evidence = _answer(
            client,
            question="未知问题",
            corpora=["case"],
            case_id=case_id,
        )
        assert no_evidence.status_code == 200
        assert no_evidence.json()["status"] == "refused"
        assert no_evidence.json()["citations"] == []

        _upload_and_index(client, case_id)
        container: AppContainer = client.app.state.container  # type: ignore[attr-defined]
        container.evidence_qa._support_verifier = FakeClaimSupportVerifier(
            ClaimSupportResult(
                judgements=[
                    ClaimSupportJudgement(
                        claim_id="C1",
                        supported=False,
                        citation_ids=[],
                        reason="原文不支持",
                    )
                ],
                unsupported_claim_ids=["C1"],
                valid=False,
            )
        )
        rejected = _answer(
            client,
            question="问题",
            corpora=["case"],
            case_id=case_id,
        )
        assert rejected.status_code == 200
        assert rejected.json()["status"] == "refused"
        assert rejected.json()["claims"] == []
        assert rejected.json()["citations"] == []

    def test_unknown_citation_from_generator_fails_closed(
        self,
        authed_client: tuple[TestClient, dict[str, Any]],
    ) -> None:
        client, _ = authed_client
        _, case_id = _setup_case(client)
        _upload_and_index(client, case_id)
        container: AppContainer = client.app.state.container  # type: ignore[attr-defined]
        container.evidence_qa._generator = FakeEvidenceQAGenerator(
            EvidenceQADraft(
                status="answered",
                claims=[
                    EvidenceQAClaim(
                        claim_id="C1",
                        text="伪造结论",
                        citation_ids=["UNKNOWN"],
                    )
                ],
            )
        )

        response = _answer(
            client,
            question="问题",
            corpora=["case"],
            case_id=case_id,
        )

        assert response.status_code == 200
        assert response.json()["status"] == "refused"

    def test_scope_validation_and_cross_case_isolation(
        self,
        authed_client: tuple[TestClient, dict[str, Any]],
    ) -> None:
        client, _ = authed_client
        workspace_id, case_id = _setup_case(client)
        missing_case = _answer(
            client,
            question="问题",
            corpora=["case"],
            workspace_id=workspace_id,
        )
        assert missing_case.status_code == 400
        extra_scope = _answer(
            client,
            question="问题",
            corpora=["regulatory"],
            workspace_id=workspace_id,
        )
        assert extra_scope.status_code == 400
        duplicate = _answer(
            client,
            question="问题",
            corpora=["case", "case"],
            case_id=case_id,
        )
        assert duplicate.status_code == 400

        _switch_actor(client, "github:outsider")
        hidden = _answer(
            client,
            question="问题",
            corpora=["case"],
            case_id=case_id,
        )
        assert hidden.status_code == 404
        assert hidden.json()["error_code"] == "CASE_NOT_FOUND"

    def test_rejects_extra_fields_and_overlong_question(
        self,
        authed_client: tuple[TestClient, dict[str, Any]],
    ) -> None:
        client, _ = authed_client
        extra = client.post(
            "/api/v3/qa",
            json={
                "question": "问题",
                "corpora": ["regulatory"],
                "actor_id": "github:admin",
            },
        )
        assert extra.status_code == 422
        overlong = client.post(
            "/api/v3/qa",
            json={"question": "x" * 2001, "corpora": ["regulatory"]},
        )
        assert overlong.status_code == 422

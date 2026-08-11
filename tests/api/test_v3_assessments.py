"""V3 Assessment 生成、查询和审批 API 测试。"""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from app.container import AppContainer
from domain import FactProposal, FactProposalEvidence
from tests.fakes import FakeFactProposalGenerator


def _switch_actor(client: TestClient, actor_id: str) -> None:
    container: AppContainer = client.app.state.container  # type: ignore[attr-defined]
    token = container.auth.issue_jwt(actor_id)
    client.cookies.set(container.settings.cookie_name, token)


def _setup_case(
    client: TestClient,
    *,
    reviewer_id: str | None = None,
) -> tuple[str, str]:
    workspace_id = client.post(
        "/api/v3/workspaces",
        json={"name": "跨境合规组"},
    ).json()["workspace_id"]
    if reviewer_id is not None:
        response = client.put(
            f"/api/v3/workspaces/{workspace_id}/members/{reviewer_id}",
            json={"role": "reviewer"},
        )
        assert response.status_code == 200
    payload: dict[str, object] = {
        "workspace_id": workspace_id,
        "title": "海外客服项目",
        "assessment_date": "2026-08-06",
    }
    if reviewer_id is not None:
        payload["reviewer_id"] = reviewer_id
    case_response = client.post("/api/v3/cases", json=payload)
    assert case_response.status_code == 201
    case_id = case_response.json()["case_id"]
    for target in ("collecting", "ready_for_assessment"):
        response = client.post(
            f"/api/v3/cases/{case_id}/transitions",
            json={"target": target},
        )
        assert response.status_code == 200
    return workspace_id, case_id


def _create_published_rule(
    client: TestClient,
    workspace_id: str,
    *,
    required_fact_fields: list[str],
) -> None:
    payload = {
        "rule_id": "SYNTHETIC-001",
        "ruleset_version": "synthetic-v1",
        "jurisdiction": "CN",
        "effective_from": "2026-01-01",
        "required_fact_fields": required_fact_fields,
        "condition": {
            "field": required_fact_fields[0],
            "operator": "eq",
            "value": True,
        },
        "result": {
            "candidate_path": "synthetic",
            "risk_level": "high",
            "required_actions": ["完成安全评估"],
        },
        "source_clause_ids": ["synthetic-clause"],
    }
    created = client.post(
        f"/api/v3/workspaces/{workspace_id}/policy-rules",
        json=payload,
    )
    assert created.status_code == 201
    published = client.post(
        f"/api/v3/workspaces/{workspace_id}/policy-rules/SYNTHETIC-001/synthetic-v1/publish"
    )
    assert published.status_code == 200


def _create_confirmed_fact(client: TestClient, case_id: str) -> str:
    created = client.post(
        f"/api/v3/cases/{case_id}/facts",
        json={
            "field_name": "important_data_involved",
            "value": True,
            "source_type": "user",
            "confidence": 1.0,
        },
    )
    assert created.status_code == 201
    fact_id = created.json()["fact"]["fact_id"]
    confirmed = client.post(
        f"/api/v3/facts/{fact_id}/transitions",
        json={"target": "confirmed"},
    )
    assert confirmed.status_code == 200
    return fact_id


def _create_confirmed_document_fact(client: TestClient, case_id: str) -> str:
    uploaded = client.post(
        f"/api/v3/cases/{case_id}/documents",
        files={
            "file": (
                "evidence.txt",
                "材料明确说明涉及重要数据。".encode(),
                "text/plain",
            )
        },
    )
    assert uploaded.status_code == 202
    body = uploaded.json()
    job_id = body["job"]["job_id"]
    assert client.post(f"/api/v3/processing-jobs/{job_id}/parse").status_code == 200
    assert client.post(f"/api/v3/processing-jobs/{job_id}/index").status_code == 200
    container: AppContainer = client.app.state.container  # type: ignore[attr-defined]
    container.fact_management._proposal_generator = FakeFactProposalGenerator(
        [
            FactProposal(
                field_name="important_data_involved",
                value=True,
                confidence=0.95,
                evidence=[
                    FactProposalEvidence(
                        document_id=body["document"]["document_id"],
                        document_version_id=body["version"]["version_id"],
                        page_number=1,
                        quote="涉及重要数据",
                        confidence=0.95,
                    )
                ],
            )
        ]
    )
    proposed = client.post(
        f"/api/v3/cases/{case_id}/fact-proposals",
        json={"field_names": ["important_data_involved"]},
    )
    assert proposed.status_code == 201, proposed.text
    fact_id = proposed.json()["facts"][0]["fact"]["fact_id"]
    confirmed = client.post(
        f"/api/v3/facts/{fact_id}/transitions",
        json={"target": "confirmed"},
    )
    assert confirmed.status_code == 200
    return fact_id


def _generate(client: TestClient, case_id: str) -> dict[str, Any]:
    response = client.post(
        f"/api/v3/cases/{case_id}/assessments",
        json={
            "ruleset_version": "synthetic-v1",
            "generated_by_run_id": "run_001",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


class TestAssessmentApi:
    def test_generate_query_and_approve_active_assessment(
        self,
        authed_client: tuple[TestClient, dict[str, Any]],
    ) -> None:
        client, _ = authed_client
        reviewer_id = "github:reviewer"
        workspace_id, case_id = _setup_case(client, reviewer_id=reviewer_id)
        fact_id = _create_confirmed_fact(client, case_id)
        _create_published_rule(
            client,
            workspace_id,
            required_fact_fields=["important_data_involved"],
        )

        generated = _generate(client, case_id)
        assessment_id = generated["assessment"]["assessment_id"]
        assert generated["assessment"]["status"] == "review_required"
        assert generated["assessment"]["fact_versions"] == {"important_data_involved": 1}
        assert generated["assessment"]["generated_by_run_id"] == "run_001"
        assert generated["findings"][0]["fact_ids"] == [fact_id]
        assert generated["action_items"][0]["title"] == "完成安全评估"

        case = client.get(f"/api/v3/cases/{case_id}")
        assert case.json()["status"] == "review_required"
        assert case.json()["active_assessment_id"] == assessment_id

        active = client.get(f"/api/v3/cases/{case_id}/assessments/active")
        assert active.status_code == 200
        assert active.json() == generated
        detail = client.get(f"/api/v3/assessments/{assessment_id}")
        assert detail.status_code == 200
        assert detail.json() == generated
        versions = client.get(f"/api/v3/cases/{case_id}/assessments")
        assert versions.status_code == 200
        assert versions.json()["assessments"][0]["version"] == 1

        _switch_actor(client, reviewer_id)
        reviewed = client.post(
            f"/api/v3/assessments/{assessment_id}/review",
            json={"decision": "approved", "comment": "证据和规则核验通过"},
        )
        assert reviewed.status_code == 200
        assert reviewed.json()["assessment"]["status"] == "approved"
        assert reviewed.json()["assessment"]["approved_by"] == reviewer_id
        completed_case = client.get(f"/api/v3/cases/{case_id}")
        assert completed_case.status_code == 200
        assert completed_case.json()["status"] == "completed"

    def test_document_fact_assessment_returns_evidence_snapshot(
        self,
        authed_client: tuple[TestClient, dict[str, Any]],
    ) -> None:
        client, _ = authed_client
        workspace_id, case_id = _setup_case(client)
        fact_id = _create_confirmed_document_fact(client, case_id)
        _create_published_rule(
            client,
            workspace_id,
            required_fact_fields=["important_data_involved"],
        )

        generated = _generate(client, case_id)

        finding = generated["findings"][0]
        citation = generated["evidence_citations"][0]
        assert finding["fact_ids"] == [fact_id]
        assert finding["evidence_ids"] == [citation["citation_id"]]
        assert citation["fact_id"] == fact_id
        assert citation["fact_version"] == 1
        assert citation["quote"] == "涉及重要数据"
        assert len(citation["source_sha256"]) == 64
        assert generated["findings"][0]["clause_ids"] == ["synthetic-clause"]

    def test_reject_requires_comment_and_allows_new_version(
        self,
        authed_client: tuple[TestClient, dict[str, Any]],
    ) -> None:
        client, user = authed_client
        workspace_id, case_id = _setup_case(client)
        _create_confirmed_fact(client, case_id)
        _create_published_rule(
            client,
            workspace_id,
            required_fact_fields=["important_data_involved"],
        )
        first = _generate(client, case_id)
        first_id = first["assessment"]["assessment_id"]

        missing_comment = client.post(
            f"/api/v3/assessments/{first_id}/review",
            json={"decision": "rejected"},
        )
        assert missing_comment.status_code == 400

        rejected = client.post(
            f"/api/v3/assessments/{first_id}/review",
            json={"decision": "rejected", "comment": "补充传输链路材料"},
        )
        assert rejected.status_code == 200
        assert rejected.json()["assessment"]["status"] == "rejected"
        assert client.get(f"/api/v3/cases/{case_id}").json()["status"] == ("ready_for_assessment")

        second = _generate(client, case_id)
        assert second["assessment"]["version"] == 2
        versions = client.get(f"/api/v3/cases/{case_id}/assessments").json()["assessments"]
        assert [item["status"] for item in versions] == [
            "review_required",
            "superseded",
        ]
        stale_review = client.post(
            f"/api/v3/assessments/{first_id}/review",
            json={"decision": "approved"},
        )
        assert stale_review.status_code == 409
        assert stale_review.json()["error_code"] == "ASSESSMENT_NOT_ACTIVE"
        assert user["user_id"] != ""

    def test_missing_facts_cannot_be_approved(
        self,
        authed_client: tuple[TestClient, dict[str, Any]],
    ) -> None:
        client, _ = authed_client
        workspace_id, case_id = _setup_case(client)
        _create_published_rule(
            client,
            workspace_id,
            required_fact_fields=["important_data_involved"],
        )
        generated = _generate(client, case_id)
        assessment_id = generated["assessment"]["assessment_id"]
        assert generated["assessment"]["risk_level"] == "unknown"

        response = client.post(
            f"/api/v3/assessments/{assessment_id}/review",
            json={"decision": "approved", "comment": "错误批准"},
        )
        assert response.status_code == 400
        assert "缺失事实" in response.json()["message"]

    def test_assigned_reviewer_and_workspace_isolation(
        self,
        authed_client: tuple[TestClient, dict[str, Any]],
    ) -> None:
        client, _ = authed_client
        assigned_reviewer = "github:assigned"
        workspace_id, case_id = _setup_case(
            client,
            reviewer_id=assigned_reviewer,
        )
        client.put(
            f"/api/v3/workspaces/{workspace_id}/members/github:other-reviewer",
            json={"role": "reviewer"},
        )
        _create_confirmed_fact(client, case_id)
        _create_published_rule(
            client,
            workspace_id,
            required_fact_fields=["important_data_involved"],
        )
        assessment_id = _generate(client, case_id)["assessment"]["assessment_id"]

        _switch_actor(client, "github:other-reviewer")
        forbidden = client.post(
            f"/api/v3/assessments/{assessment_id}/review",
            json={"decision": "approved"},
        )
        assert forbidden.status_code == 403

        _switch_actor(client, "github:outsider")
        hidden = client.get(f"/api/v3/assessments/{assessment_id}")
        assert hidden.status_code == 404
        assert hidden.json()["error_code"] == "ASSESSMENT_NOT_FOUND"

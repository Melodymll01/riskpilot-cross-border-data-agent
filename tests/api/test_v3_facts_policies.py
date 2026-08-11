"""V3 案件事实与规则 API 端到端测试。"""

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


def _create_case(
    client: TestClient,
    *,
    assessment_date: str | None = "2026-08-06",
) -> tuple[str, str]:
    workspace_id = client.post(
        "/api/v3/workspaces",
        json={"name": "跨境合规组"},
    ).json()["workspace_id"]
    payload: dict[str, object] = {
        "workspace_id": workspace_id,
        "title": "海外客服项目",
    }
    if assessment_date is not None:
        payload["assessment_date"] = assessment_date
    case_id = client.post("/api/v3/cases", json=payload).json()["case_id"]
    return workspace_id, case_id


def _upload_and_parse(client: TestClient, case_id: str) -> tuple[str, str]:
    uploaded = client.post(
        f"/api/v3/cases/{case_id}/documents",
        files={
            "file": (
                "policy.txt",
                "材料明确说明涉及重要数据。".encode(),
                "text/plain",
            )
        },
    ).json()
    job_id = uploaded["job"]["job_id"]
    client.post(f"/api/v3/processing-jobs/{job_id}/parse")
    client.post(f"/api/v3/processing-jobs/{job_id}/index")
    return uploaded["document"]["document_id"], uploaded["version"]["version_id"]


def _rule_payload() -> dict[str, object]:
    return {
        "rule_id": "SYNTHETIC-001",
        "ruleset_version": "synthetic-v1",
        "jurisdiction": "CN",
        "effective_from": "2026-01-01",
        "required_fact_fields": ["important_data_involved"],
        "condition": {
            "field": "important_data_involved",
            "operator": "eq",
            "value": True,
        },
        "result": {"candidate_path": "synthetic"},
        "source_clause_ids": ["synthetic-clause"],
    }


class TestFactApi:
    def test_document_fact_proposal_requires_reviewer_confirmation(
        self, authed_client: tuple[TestClient, dict[str, Any]]
    ) -> None:
        client, _ = authed_client
        workspace_id, case_id = _create_case(client)
        client.put(
            f"/api/v3/workspaces/{workspace_id}/members/github:editor",
            json={"role": "editor"},
        )
        client.put(
            f"/api/v3/workspaces/{workspace_id}/members/github:reviewer",
            json={"role": "reviewer"},
        )
        _switch_actor(client, "github:editor")
        document_id, version_id = _upload_and_parse(client, case_id)
        container: AppContainer = client.app.state.container  # type: ignore[attr-defined]
        generator = FakeFactProposalGenerator(
            [
                FactProposal(
                    field_name="important_data_involved",
                    value=True,
                    confidence=0.95,
                    evidence=[
                        FactProposalEvidence(
                            document_id=document_id,
                            document_version_id=version_id,
                            page_number=1,
                            quote="涉及重要数据",
                            confidence=0.95,
                        )
                    ],
                )
            ]
        )
        container.fact_management._proposal_generator = generator

        response = client.post(
            f"/api/v3/cases/{case_id}/fact-proposals",
            json={
                "field_names": ["important_data_involved"],
                "document_ids": [document_id],
            },
        )

        assert response.status_code == 201, response.text
        body = response.json()
        fact = body["facts"][0]["fact"]
        assert fact["status"] == "proposed"
        assert fact["criticality"] == "critical"
        assert body["conflict_field_names"] == []
        assert body["source_document_ids"] == [document_id]
        assert generator.calls[0]["field_names"] == ["important_data_involved"]

        denied = client.post(
            f"/api/v3/facts/{fact['fact_id']}/transitions",
            json={"target": "confirmed"},
        )
        assert denied.status_code == 403
        _switch_actor(client, "github:reviewer")
        confirmed = client.post(
            f"/api/v3/facts/{fact['fact_id']}/transitions",
            json={"target": "confirmed"},
        )
        assert confirmed.status_code == 200
        assert confirmed.json()["confirmed_by"] == "github:reviewer"

    def test_document_fact_proposal_marks_value_conflict(
        self, authed_client: tuple[TestClient, dict[str, Any]]
    ) -> None:
        client, _ = authed_client
        workspace_id, case_id = _create_case(client)
        document_id, version_id = _upload_and_parse(client, case_id)
        existing = client.post(
            f"/api/v3/cases/{case_id}/facts",
            json={
                "field_name": "important_data_involved",
                "value": False,
                "source_type": "user",
                "confidence": 1.0,
            },
        )
        assert existing.status_code == 201
        container: AppContainer = client.app.state.container  # type: ignore[attr-defined]
        container.fact_management._proposal_generator = FakeFactProposalGenerator(
            [
                FactProposal(
                    field_name="important_data_involved",
                    value=True,
                    confidence=0.95,
                    evidence=[
                        FactProposalEvidence(
                            document_id=document_id,
                            document_version_id=version_id,
                            page_number=1,
                            quote="涉及重要数据",
                        )
                    ],
                )
            ]
        )

        response = client.post(
            f"/api/v3/cases/{case_id}/fact-proposals",
            json={"field_names": ["important_data_involved"]},
        )

        assert response.status_code == 201, response.text
        candidate = response.json()["facts"][0]["fact"]
        assert candidate["status"] == "conflicting"
        assert response.json()["conflict_field_names"] == ["important_data_involved"]
        client.put(
            f"/api/v3/workspaces/{workspace_id}/members/github:reviewer",
            json={"role": "reviewer"},
        )
        _switch_actor(client, "github:reviewer")
        confirmed = client.post(
            f"/api/v3/facts/{candidate['fact_id']}/transitions",
            json={"target": "confirmed"},
        )
        assert confirmed.status_code == 200
        facts = client.get(f"/api/v3/cases/{case_id}/facts").json()["facts"]
        same_field = [fact for fact in facts if fact["field_name"] == "important_data_involved"]
        assert [fact["status"] for fact in same_field].count("confirmed") == 1
        assert [fact["status"] for fact in same_field].count("rejected") == 1

    def test_document_fact_create_revise_confirm_and_list(
        self, authed_client: tuple[TestClient, dict[str, Any]]
    ) -> None:
        client, _ = authed_client
        workspace_id, case_id = _create_case(client)
        document_id, version_id = _upload_and_parse(client, case_id)
        client.put(
            f"/api/v3/workspaces/{workspace_id}/members/github:reviewer",
            json={"role": "reviewer"},
        )

        created = client.post(
            f"/api/v3/cases/{case_id}/facts",
            json={
                "field_name": "important_data_involved",
                "value": True,
                "source_type": "document",
                "confidence": 0.95,
                "criticality": "critical",
                "evidence": [
                    {
                        "document_id": document_id,
                        "document_version_id": version_id,
                        "page_number": 1,
                        "quote": "涉及重要数据",
                        "confidence": 0.95,
                    }
                ],
            },
        )
        assert created.status_code == 201
        fact_id = created.json()["fact"]["fact_id"]
        assert created.json()["fact"]["status"] == "proposed"
        assert created.json()["evidence"][0]["fact_version"] == 1

        _switch_actor(client, "github:reviewer")
        confirmed = client.post(
            f"/api/v3/facts/{fact_id}/transitions",
            json={"target": "confirmed"},
        )
        assert confirmed.status_code == 200
        assert confirmed.json()["confirmed_by"] == "github:reviewer"

        _switch_actor(client, created.json()["fact"]["created_by"])
        revised = client.post(
            f"/api/v3/facts/{fact_id}/revisions",
            json={
                "value": False,
                "source_type": "document",
                "confidence": 0.7,
                "evidence": [
                    {
                        "document_id": document_id,
                        "document_version_id": version_id,
                        "page_number": 1,
                        "quote": "涉及重要数据",
                    }
                ],
            },
        )
        assert revised.status_code == 200
        assert revised.json()["fact"]["version"] == 2
        assert revised.json()["fact"]["status"] == "proposed"

        listed = client.get(f"/api/v3/cases/{case_id}/facts")
        assert listed.status_code == 200
        assert listed.json()["facts"][0]["version"] == 2

    def test_unverified_quote_rejected(
        self, authed_client: tuple[TestClient, dict[str, Any]]
    ) -> None:
        client, _ = authed_client
        _, case_id = _create_case(client)
        document_id, version_id = _upload_and_parse(client, case_id)
        response = client.post(
            f"/api/v3/cases/{case_id}/facts",
            json={
                "field_name": "important_data_involved",
                "value": True,
                "source_type": "document",
                "confidence": 0.5,
                "evidence": [
                    {
                        "document_id": document_id,
                        "document_version_id": version_id,
                        "page_number": 1,
                        "quote": "原文不存在",
                    }
                ],
            },
        )
        assert response.status_code == 400
        assert response.json()["error_code"] == "INVALID_DOCUMENT_CONTENT"


class TestPolicyApi:
    def test_admin_create_publish_and_evaluate_confirmed_fact(
        self, authed_client: tuple[TestClient, dict[str, Any]]
    ) -> None:
        client, _ = authed_client
        workspace_id, case_id = _create_case(client)
        fact = client.post(
            f"/api/v3/cases/{case_id}/facts",
            json={
                "field_name": "important_data_involved",
                "value": True,
                "source_type": "user",
                "confidence": 1.0,
            },
        ).json()["fact"]
        client.post(
            f"/api/v3/facts/{fact['fact_id']}/transitions",
            json={"target": "confirmed"},
        )

        created = client.post(
            f"/api/v3/workspaces/{workspace_id}/policy-rules",
            json=_rule_payload(),
        )
        assert created.status_code == 201
        assert created.json()["workspace_id"] == workspace_id
        assert created.json()["status"] == "draft"

        published = client.post(
            f"/api/v3/workspaces/{workspace_id}/policy-rules/SYNTHETIC-001/synthetic-v1/publish"
        )
        assert published.status_code == 200
        assert published.json()["status"] == "published"

        report = client.post(
            f"/api/v3/cases/{case_id}/policy-evaluations",
            json={"ruleset_version": "synthetic-v1"},
        )
        assert report.status_code == 200
        assert report.json()["evaluations"][0]["status"] == "triggered"
        assert report.json()["evaluations"][0]["consumed_fact_versions"] == {
            "important_data_involved": 1
        }

    def test_editor_cannot_create_or_publish_rule(
        self, authed_client: tuple[TestClient, dict[str, Any]]
    ) -> None:
        client, _ = authed_client
        workspace_id, _ = _create_case(client)
        client.put(
            f"/api/v3/workspaces/{workspace_id}/members/github:editor",
            json={"role": "editor"},
        )
        _switch_actor(client, "github:editor")
        response = client.post(
            f"/api/v3/workspaces/{workspace_id}/policy-rules",
            json=_rule_payload(),
        )
        assert response.status_code == 403

    def test_workspace_rule_isolation(
        self, authed_client: tuple[TestClient, dict[str, Any]]
    ) -> None:
        client, _ = authed_client
        workspace_a, _ = _create_case(client)
        client.post(
            f"/api/v3/workspaces/{workspace_a}/policy-rules",
            json=_rule_payload(),
        )
        workspace_b = client.post(
            "/api/v3/workspaces",
            json={"name": "另一个工作空间"},
        ).json()["workspace_id"]
        listed = client.get(f"/api/v3/workspaces/{workspace_b}/policy-rules")
        assert listed.status_code == 200
        assert listed.json()["rules"] == []

    def test_case_without_assessment_date_rejected(
        self, authed_client: tuple[TestClient, dict[str, Any]]
    ) -> None:
        client, _ = authed_client
        _, case_id = _create_case(client, assessment_date=None)
        response = client.post(
            f"/api/v3/cases/{case_id}/policy-evaluations",
            json={"ruleset_version": "synthetic-v1"},
        )
        assert response.status_code == 400
        assert "assessment_date" in response.json()["message"]

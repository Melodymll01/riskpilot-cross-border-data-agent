"""V3 Case Assessment Run API 端到端测试。"""

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


def _setup_case(client: TestClient) -> tuple[str, str]:
    workspace_id = client.post(
        "/api/v3/workspaces",
        json={"name": "跨境合规组"},
    ).json()["workspace_id"]
    for user_id, role in (
        ("github:editor", "editor"),
        ("github:reviewer", "reviewer"),
        ("github:viewer", "viewer"),
    ):
        response = client.put(
            f"/api/v3/workspaces/{workspace_id}/members/{user_id}",
            json={"role": role},
        )
        assert response.status_code == 200
    _switch_actor(client, "github:editor")
    case = client.post(
        "/api/v3/cases",
        json={
            "workspace_id": workspace_id,
            "title": "海外客服项目",
            "assessment_date": "2026-08-07",
            "reviewer_id": "github:reviewer",
        },
    )
    assert case.status_code == 201
    case_id = case.json()["case_id"]
    for target in ("collecting", "ready_for_assessment"):
        response = client.post(
            f"/api/v3/cases/{case_id}/transitions",
            json={"target": target},
        )
        assert response.status_code == 200
    _switch_actor(client, case.json()["owner_id"])
    return workspace_id, case_id


def _publish_rule(client: TestClient, workspace_id: str) -> None:
    container: AppContainer = client.app.state.container  # type: ignore[attr-defined]
    workspace = container.workspace_repo.get(workspace_id)
    assert workspace is not None
    _switch_actor(client, workspace.created_by)
    payload = {
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
        "result": {
            "candidate_path": "security_assessment",
            "risk_level": "high",
            "required_actions": ["提交安全评估材料"],
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
    _switch_actor(client, "github:editor")


def _upload(client: TestClient, case_id: str) -> dict[str, Any]:
    response = client.post(
        f"/api/v3/cases/{case_id}/documents",
        files={
            "file": (
                "case.txt",
                "材料明确说明涉及重要数据。".encode(),
                "text/plain",
            )
        },
    )
    assert response.status_code == 202
    return response.json()


def _process_document(client: TestClient, job_id: str) -> None:
    parsed = client.post(f"/api/v3/processing-jobs/{job_id}/parse")
    assert parsed.status_code == 200
    indexed = client.post(f"/api/v3/processing-jobs/{job_id}/index")
    assert indexed.status_code == 200
    assert indexed.json()["document"]["status"] == "ready"


def _confirm_fact(client: TestClient, case_id: str) -> str:
    created = client.post(
        f"/api/v3/cases/{case_id}/facts",
        json={
            "field_name": "important_data_involved",
            "value": True,
            "source_type": "user",
            "confidence": 1.0,
            "criticality": "critical",
        },
    )
    assert created.status_code == 201
    fact_id = created.json()["fact"]["fact_id"]
    _switch_actor(client, "github:reviewer")
    confirmed = client.post(
        f"/api/v3/facts/{fact_id}/transitions",
        json={"target": "confirmed"},
    )
    assert confirmed.status_code == 200
    _switch_actor(client, "github:editor")
    return fact_id


def _configure_document_fact_proposal(
    client: TestClient,
    uploaded: dict[str, Any],
    *,
    value: bool,
) -> FakeFactProposalGenerator:
    generator = FakeFactProposalGenerator(
        [
            FactProposal(
                field_name="important_data_involved",
                value=value,
                confidence=0.95,
                evidence=[
                    FactProposalEvidence(
                        document_id=uploaded["document"]["document_id"],
                        document_version_id=uploaded["version"]["version_id"],
                        page_number=1,
                        quote="涉及重要数据",
                        confidence=0.95,
                    )
                ],
            )
        ],
        token_usage=80,
    )
    container: AppContainer = client.app.state.container  # type: ignore[attr-defined]
    container.fact_management._proposal_generator = generator
    return generator


def _start(client: TestClient, case_id: str) -> Any:
    return client.post(
        f"/api/v3/cases/{case_id}/assessment-runs",
        json={
            "ruleset_version": "synthetic-v1",
            "model_config_snapshot": {
                "provider": "deterministic",
                "model": "rule-engine",
            },
        },
    )


class TestAssessmentRunApi:
    def test_requires_authentication(self, client: TestClient) -> None:
        response = client.get("/api/v3/runs/run_x")
        assert response.status_code == 401

    def test_ready_case_runs_to_review_and_reviewer_approves(
        self,
        authed_client: tuple[TestClient, dict[str, Any]],
    ) -> None:
        client, _ = authed_client
        workspace_id, case_id = _setup_case(client)
        _publish_rule(client, workspace_id)
        uploaded = _upload(client, case_id)
        _process_document(client, uploaded["job"]["job_id"])
        _confirm_fact(client, case_id)

        started = _start(client, case_id)
        assert started.status_code == 201
        run = started.json()
        run_id = run["run_id"]
        assert run["status"] == "waiting_for_review"
        assert run["current_stage"] == "human_review"
        assert "thread_id" not in run
        assert "model_config_snapshot" not in run

        detail = client.get(f"/api/v3/runs/{run_id}")
        assert detail.status_code == 200
        assert detail.json() == run
        listed = client.get(f"/api/v3/cases/{case_id}/assessment-runs")
        assert listed.status_code == 200
        assert listed.json()["runs"] == [run]

        events = client.get(f"/api/v3/runs/{run_id}/events")
        assert events.status_code == 200
        event_items = events.json()["events"]
        assert event_items[-1]["event_type"] == "human_review_required"
        assert [item["sequence"] for item in event_items] == list(range(1, len(event_items) + 1))
        assert all("thought" not in item["payload"] for item in event_items)
        tool_events = [item for item in event_items if item["event_type"] == "tool_completed"]
        assert [item["payload"]["tool_name"] for item in tool_events] == [
            "retrieve_case_evidence",
            "retrieve_regulations",
            "evaluate_deterministic_rules",
            "verify_claim_citations",
        ]
        assert all(
            "workspace_id" not in item["payload"]["arguments"]
            and "case_id" not in item["payload"]["arguments"]
            and "actor_id" not in item["payload"]["arguments"]
            for item in tool_events
        )
        plan = client.get(f"/api/v3/runs/{run_id}/plan")
        assert plan.status_code == 200
        assert plan.json()["required_fact_fields"] == ["important_data_involved"]
        assert "evaluate_deterministic_rules" in plan.json()["planned_tools"]
        run_detail = client.get(f"/api/v3/runs/{run_id}/detail")
        assert run_detail.status_code == 200
        detail_body = run_detail.json()
        assert detail_body["run"] == run
        assert detail_body["duration_ms"] >= 0
        assert detail_body["cost_currency"] == "unspecified"
        assert detail_body["evidence_plan"]["required_fact_fields"] == ["important_data_involved"]
        assert [item["tool_name"] for item in detail_body["tool_calls"]] == [
            "retrieve_case_evidence",
            "retrieve_regulations",
            "evaluate_deterministic_rules",
            "verify_claim_citations",
        ]
        assert detail_body["rule_evaluation"]["triggered_rule_ids"] == ["SYNTHETIC-001"]
        assert detail_body["citation_verification"]["valid"] is True
        assert detail_body["assessment"]["assessment"]["generated_by_run_id"] == run_id
        assert detail_body["actions"] == {
            "can_continue": False,
            "can_retry": False,
            "can_cancel": True,
            "can_review": False,
        }
        timeline_stages = [
            item["stage"]
            for item in detail_body["timeline"]
            if item["event_type"] == "stage_completed"
        ]
        assert "build_evidence_plan" in timeline_stages
        assert "verify_claim_citations" in timeline_stages
        assert all(item["duration_ms"] >= 0 for item in detail_body["timeline"])
        assert all(
            not ({"workspace_id", "case_id", "actor_id", "owner_id"} & set(item["arguments"]))
            for item in detail_body["tool_calls"]
        )
        serialized_detail = run_detail.text.lower()
        for forbidden in (
            "chain_of_thought",
            "raw_prompt",
            "authorization",
            "api_key",
            "password",
            "secret",
            "thought",
        ):
            assert forbidden not in serialized_detail
        unchanged = client.post(f"/api/v3/runs/{run_id}/continue")
        assert unchanged.status_code == 200
        assert unchanged.json()["status"] == "waiting_for_review"
        assert unchanged.json()["current_stage"] == "human_review"
        after = client.get(
            f"/api/v3/runs/{run_id}/events",
            params={"after_sequence": event_items[-2]["sequence"]},
        )
        assert after.json()["events"] == [event_items[-1]]

        _switch_actor(client, "github:reviewer")
        reviewer_detail = client.get(f"/api/v3/runs/{run_id}/detail")
        assert reviewer_detail.status_code == 200
        assert reviewer_detail.json()["actions"]["can_review"] is True
        approved = client.post(
            f"/api/v3/runs/{run_id}/review",
            json={"decision": "approved", "comment": "审核通过"},
        )
        assert approved.status_code == 200
        assert approved.json()["status"] == "completed"
        case = client.get(f"/api/v3/cases/{case_id}")
        assert case.status_code == 200
        assert case.json()["status"] == "completed"

    def test_document_and_fact_interrupts_continue_until_review(
        self,
        authed_client: tuple[TestClient, dict[str, Any]],
    ) -> None:
        client, _ = authed_client
        workspace_id, case_id = _setup_case(client)
        _publish_rule(client, workspace_id)
        uploaded = _upload(client, case_id)

        started = _start(client, case_id)
        assert started.status_code == 201
        run_id = started.json()["run_id"]
        assert started.json()["current_stage"] == "inspect_documents"
        plan_pending = client.get(f"/api/v3/runs/{run_id}/plan")
        assert plan_pending.status_code == 409

        still_waiting = client.post(f"/api/v3/runs/{run_id}/continue")
        assert still_waiting.status_code == 200
        assert still_waiting.json()["current_stage"] == "inspect_documents"

        _process_document(client, uploaded["job"]["job_id"])
        missing_fact = client.post(f"/api/v3/runs/{run_id}/continue")
        assert missing_fact.status_code == 200
        assert missing_fact.json()["current_stage"] == "human_fact_confirmation"
        missing_detail = client.get(f"/api/v3/runs/{run_id}/detail")
        assert missing_detail.status_code == 200
        interrupt = missing_detail.json()["interrupt"]
        assert interrupt["kind"] == "fact_confirmation"
        assert interrupt["stage"] == "human_fact_confirmation"
        assert interrupt["missing_fact_fields"] == ["important_data_involved"]
        assert interrupt["reason"] == "等待人工确认关键事实"
        assert missing_detail.json()["assessment"] is None
        assert missing_detail.json()["actions"]["can_continue"] is True

        _confirm_fact(client, case_id)
        review = client.post(f"/api/v3/runs/{run_id}/continue")
        assert review.status_code == 200
        assert review.json()["status"] == "waiting_for_review"

    def test_graph_proposes_document_fact_and_resumes_after_confirmation(
        self,
        authed_client: tuple[TestClient, dict[str, Any]],
    ) -> None:
        client, _ = authed_client
        workspace_id, case_id = _setup_case(client)
        _publish_rule(client, workspace_id)
        uploaded = _upload(client, case_id)
        _process_document(client, uploaded["job"]["job_id"])
        generator = _configure_document_fact_proposal(client, uploaded, value=True)

        started = _start(client, case_id)

        assert started.status_code == 201, started.text
        run = started.json()
        assert run["status"] == "waiting_for_user"
        assert run["current_stage"] == "human_fact_confirmation"
        assert run["token_usage"] == 80
        assert generator.calls
        container: AppContainer = client.app.state.container  # type: ignore[attr-defined]
        proposed = container.case_fact_repo.list_for_case(
            case_id,
            statuses={"proposed"},
        )
        assert len(proposed) == 1
        events = client.get(f"/api/v3/runs/{run['run_id']}/events").json()["events"]
        assert any(item["event_type"] == "facts_proposed" for item in events)
        extract_event = next(
            item
            for item in events
            if item["event_type"] == "tool_completed"
            and item["payload"]["tool_name"] == "extract_fact_candidates"
        )
        assert extract_event["payload"]["output"]["fact_ids"] == [proposed[0].fact_id]
        assert extract_event["payload"]["token_usage"] == 80
        assert "token_usage" not in extract_event["payload"]["output"]

        _switch_actor(client, "github:reviewer")
        confirmed = client.post(
            f"/api/v3/facts/{proposed[0].fact_id}/transitions",
            json={"target": "confirmed"},
        )
        assert confirmed.status_code == 200
        _switch_actor(client, "github:editor")
        resumed = client.post(f"/api/v3/runs/{run['run_id']}/continue")
        assert resumed.status_code == 200, resumed.text
        assert resumed.json()["status"] == "waiting_for_review"

    def test_fact_conflict_requires_reviewer_before_graph_can_resume(
        self,
        authed_client: tuple[TestClient, dict[str, Any]],
    ) -> None:
        client, _ = authed_client
        workspace_id, case_id = _setup_case(client)
        _publish_rule(client, workspace_id)
        uploaded = _upload(client, case_id)
        _process_document(client, uploaded["job"]["job_id"])
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
        _configure_document_fact_proposal(client, uploaded, value=True)

        started = _start(client, case_id)

        assert started.status_code == 201, started.text
        run = started.json()
        assert run["status"] == "waiting_for_review"
        assert run["current_stage"] == "detect_fact_conflicts"
        container: AppContainer = client.app.state.container  # type: ignore[attr-defined]
        conflicting = container.case_fact_repo.list_for_case(
            case_id,
            statuses={"conflicting"},
        )
        assert len(conflicting) == 1
        denied = client.post(f"/api/v3/runs/{run['run_id']}/continue")
        assert denied.status_code == 403
        assert client.get(f"/api/v3/runs/{run['run_id']}").json()["status"] == (
            "waiting_for_review"
        )

        _switch_actor(client, "github:reviewer")
        confirmed = client.post(
            f"/api/v3/facts/{conflicting[0].fact_id}/transitions",
            json={"target": "confirmed"},
        )
        assert confirmed.status_code == 200
        resumed = client.post(f"/api/v3/runs/{run['run_id']}/continue")
        assert resumed.status_code == 200, resumed.text
        assert resumed.json()["status"] == "waiting_for_review"
        assert resumed.json()["current_stage"] == "human_review"

    def test_duplicate_run_and_sensitive_snapshot_rejected(
        self,
        authed_client: tuple[TestClient, dict[str, Any]],
    ) -> None:
        client, _ = authed_client
        workspace_id, case_id = _setup_case(client)
        _publish_rule(client, workspace_id)
        _upload(client, case_id)

        sensitive = client.post(
            f"/api/v3/cases/{case_id}/assessment-runs",
            json={
                "ruleset_version": "synthetic-v1",
                "model_config_snapshot": {"api_key": "secret"},
            },
        )
        assert sensitive.status_code == 400
        assert "敏感" in sensitive.json()["message"]

        first = _start(client, case_id)
        assert first.status_code == 201
        duplicate = _start(client, case_id)
        assert duplicate.status_code == 409
        assert duplicate.json()["error_code"] == "AGENT_RUN_ALREADY_ACTIVE"
        retry_active = client.post(f"/api/v3/runs/{first.json()['run_id']}/retry")
        assert retry_active.status_code == 400
        invalid_continue = client.post(
            f"/api/v3/runs/{first.json()['run_id']}/review",
            json={"decision": "approved", "extra": True},
        )
        assert invalid_continue.status_code == 422

        cancelled = client.post(f"/api/v3/runs/{first.json()['run_id']}/cancel")
        assert cancelled.status_code == 200
        assert cancelled.json()["status"] == "cancelled"
        repeated = client.post(f"/api/v3/runs/{first.json()['run_id']}/cancel")
        assert repeated.json() == cancelled.json()
        continued = client.post(f"/api/v3/runs/{first.json()['run_id']}/continue")
        assert continued.status_code == 400

    def test_viewer_and_outsider_cannot_access_run(
        self,
        authed_client: tuple[TestClient, dict[str, Any]],
    ) -> None:
        client, _ = authed_client
        workspace_id, case_id = _setup_case(client)
        _publish_rule(client, workspace_id)
        _upload(client, case_id)

        _switch_actor(client, "github:viewer")
        forbidden = _start(client, case_id)
        assert forbidden.status_code == 403

        _switch_actor(client, "github:editor")
        run_id = _start(client, case_id).json()["run_id"]
        _switch_actor(client, "github:outsider")
        hidden = client.get(f"/api/v3/runs/{run_id}")
        assert hidden.status_code == 404
        assert hidden.json()["error_code"] == "AGENT_RUN_NOT_FOUND"
        hidden_detail = client.get(f"/api/v3/runs/{run_id}/detail")
        assert hidden_detail.status_code == 404
        events = client.get(f"/api/v3/runs/{run_id}/events")
        assert events.status_code == 404

    def test_review_rejects_wrong_role_and_missing_comment(
        self,
        authed_client: tuple[TestClient, dict[str, Any]],
    ) -> None:
        client, _ = authed_client
        workspace_id, case_id = _setup_case(client)
        _publish_rule(client, workspace_id)
        uploaded = _upload(client, case_id)
        _process_document(client, uploaded["job"]["job_id"])
        _confirm_fact(client, case_id)
        run_id = _start(client, case_id).json()["run_id"]

        editor_review = client.post(
            f"/api/v3/runs/{run_id}/review",
            json={"decision": "approved"},
        )
        assert editor_review.status_code == 403

        _switch_actor(client, "github:reviewer")
        missing_comment = client.post(
            f"/api/v3/runs/{run_id}/review",
            json={"decision": "rejected"},
        )
        assert missing_comment.status_code == 400
        detail = client.get(f"/api/v3/runs/{run_id}")
        assert detail.json()["status"] == "waiting_for_review"

"""Evidence QA API 端到端测试。"""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from app.container import AppContainer
from domain import (
    ClaimSupportJudgement,
    ClaimSupportResult,
    EvidenceQAClaim,
    EvidenceQADraft,
    EvidenceQAFlowchart,
    EvidenceQAFlowEdge,
    EvidenceQAFlowNode,
    EvidenceQAPresentation,
    EvidenceQATable,
    EvidenceQATableRow,
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


def _setup_owner(client: TestClient) -> str:
    _switch_actor(client, "github:editor")
    return "github:editor"


def _upload_and_index(
    client: TestClient,
    *,
    content: str = "境外接收方应承担安全保护责任",
) -> dict[str, Any]:
    uploaded = client.post(
        "/api/v2/files",
        files={"file": ("case.txt", content.encode(), "text/plain")},
    )
    assert uploaded.status_code == 201
    body = uploaded.json()
    job_id = body["job"]["job_id"]
    container = client.app.state.container
    container.document_pipeline.run(job_id)
    return body


def _answer(
    client: TestClient,
    *,
    question: str,
    corpora: list[str],
) -> Any:
    return client.post("/api/v2/qa", json={"question": question, "corpora": corpora})


class TestEvidenceQA:
    def test_requires_authentication(self, client: TestClient) -> None:
        response = client.post(
            "/api/v2/qa",
            json={"question": "问题", "corpora": ["regulatory"]},
        )
        assert response.status_code == 401

    def test_case_qa_returns_verified_versioned_citation(
        self,
        authed_client: tuple[TestClient, dict[str, Any]],
    ) -> None:
        client, _ = authed_client
        _setup_owner(client)
        uploaded = _upload_and_index(client)

        response = _answer(
            client,
            question="境外接收方有什么义务？",
            corpora=["user_docs"],
        )

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "answered"
        assert body["answer"].endswith("[E1]")
        assert body["verification"]["method"] == "structural_v1"
        assert body["verification"]["valid"] is True
        assert body["support_verification"]["method"] == "independent_llm_v1"
        assert body["support_verification"]["valid"] is True
        assert body["repair_report"] == {
            "status": "not_needed",
            "original_claim_count": 1,
            "kept_claim_ids": ["C1"],
            "removed_claim_ids": [],
            "removal_reasons": {},
            "method": "bounded_filter_v1",
        }
        citation = body["citations"][0]
        assert citation["document_id"] == uploaded["document"]["document_id"]
        assert citation["document_version_id"] == uploaded["version"]["version_id"]
        assert citation["page_number"] == 1
        assert citation["source_sha256"] == uploaded["version"]["sha256"]

    def test_verified_table_presentation_is_returned(
        self,
        authed_client: tuple[TestClient, dict[str, Any]],
    ) -> None:
        client, _ = authed_client
        _setup_owner(client)
        _upload_and_index(client)
        container: AppContainer = client.app.state.container  # type: ignore[attr-defined]
        container.evidence_qa._generator = FakeEvidenceQAGenerator(
            EvidenceQADraft(
                status="answered",
                claims=[
                    EvidenceQAClaim(
                        claim_id="C1",
                        text="境外接收方应承担安全保护责任。",
                        citation_ids=["E1"],
                    )
                ],
                presentation=EvidenceQAPresentation(
                    kind="table",
                    table=EvidenceQATable(
                        rows=[EvidenceQATableRow(label="义务", claim_ids=["C1"])]
                    ),
                ),
            )
        )

        body = _answer(client, question="用表格说明义务", corpora=["user_docs"]).json()

        assert body["presentation"]["kind"] == "table"
        assert body["presentation"]["table"]["rows"] == [{"label": "义务", "claim_ids": ["C1"]}]
        assert "| 项目 | 经核验结论 | 证据 |" in body["answer"]

    def test_generic_flow_v2_is_exposed_by_response_and_openapi(
        self,
        authed_client: tuple[TestClient, dict[str, Any]],
    ) -> None:
        client, _ = authed_client
        _setup_owner(client)
        _upload_and_index(client, content="事件按优先级进入不同上报路径")
        container: AppContainer = client.app.state.container  # type: ignore[attr-defined]
        claims = [
            EvidenceQAClaim(
                claim_id="C1",
                text="系统收到事件后进入分类步骤。",
                citation_ids=["E1"],
            ),
            EvidenceQAClaim(
                claim_id="C2",
                text="系统根据事件级别选择上报路径。",
                citation_ids=["E1"],
            ),
            EvidenceQAClaim(
                claim_id="C3",
                text="事件为高优先级时，立即上报值班负责人。",
                citation_ids=["E1"],
            ),
            EvidenceQAClaim(
                claim_id="C4",
                text="事件为普通优先级时，进入常规处理队列。",
                citation_ids=["E1"],
            ),
        ]
        container.evidence_qa._generator = FakeEvidenceQAGenerator(
            EvidenceQADraft(
                status="answered",
                claims=claims,
                presentation=EvidenceQAPresentation(
                    kind="flowchart",
                    flowchart=EvidenceQAFlowchart(
                        title="事件上报",
                        nodes=[
                            EvidenceQAFlowNode(node_id="N1", kind="start", claim_ids=["C1"]),
                            EvidenceQAFlowNode(node_id="N2", kind="decision", claim_ids=["C2"]),
                            EvidenceQAFlowNode(node_id="N3", kind="terminal", claim_ids=["C3"]),
                            EvidenceQAFlowNode(node_id="N4", kind="terminal", claim_ids=["C4"]),
                        ],
                        edges=[
                            EvidenceQAFlowEdge(
                                source_node_id="N1",
                                target_node_id="N2",
                                claim_ids=["C1"],
                            ),
                            EvidenceQAFlowEdge(
                                source_node_id="N2",
                                target_node_id="N3",
                                condition="高优先级",
                                claim_ids=["C3"],
                            ),
                            EvidenceQAFlowEdge(
                                source_node_id="N2",
                                target_node_id="N4",
                                condition="普通优先级",
                                claim_ids=["C4"],
                            ),
                        ],
                    ),
                ),
            )
        )

        response = _answer(client, question="事件如何上报？", corpora=["user_docs"])

        assert response.status_code == 200
        body = response.json()
        flowchart = body["presentation"]["flowchart"]
        assert flowchart["schema_version"] == "2.0"
        assert [node["kind"] for node in flowchart["nodes"]] == [
            "start",
            "decision",
            "terminal",
            "terminal",
        ]
        assert flowchart["edges"][1]["condition"] == "高优先级"
        assert '"schema_version":"2.0"' in body["answer"]

        schemas = client.get("/openapi.json").json()["components"]["schemas"]
        node_schema = schemas["EvidenceQAFlowNode"]["properties"]
        edge_schema = schemas["EvidenceQAFlowEdge"]["properties"]
        flow_schema = schemas["EvidenceQAFlowchart"]["properties"]
        assert node_schema["kind"]["enum"] == [
            "start",
            "action",
            "decision",
            "merge",
            "terminal",
        ]
        assert edge_schema["condition"]["maxLength"] == 80
        assert flow_schema["schema_version"]["const"] == "2.0"

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

    def test_no_evidence_and_unsupported_claim_fail_closed(
        self,
        authed_client: tuple[TestClient, dict[str, Any]],
    ) -> None:
        client, _ = authed_client
        _setup_owner(client)
        no_evidence = _answer(
            client,
            question="未知问题",
            corpora=["user_docs"],
        )
        assert no_evidence.status_code == 200
        assert no_evidence.json()["status"] == "refused"
        assert no_evidence.json()["citations"] == []

        _upload_and_index(client)
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
            corpora=["user_docs"],
        )
        assert rejected.status_code == 200
        assert rejected.json()["status"] == "refused"
        assert rejected.json()["claims"] == []
        assert rejected.json()["citations"] == []
        assert rejected.json()["repair_report"]["status"] == "failed"
        assert rejected.json()["repair_report"]["removed_claim_ids"] == ["C1"]

    def test_unknown_citation_from_generator_fails_closed(
        self,
        authed_client: tuple[TestClient, dict[str, Any]],
    ) -> None:
        client, _ = authed_client
        _setup_owner(client)
        _upload_and_index(client)
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
            corpora=["user_docs"],
        )

        assert response.status_code == 200
        assert response.json()["status"] == "refused"
        assert response.json()["repair_report"]["removal_reasons"] == {"C1": ["unknown_citation"]}

    def test_mixed_claims_return_partial_answer_and_repair_report(
        self,
        authed_client: tuple[TestClient, dict[str, Any]],
    ) -> None:
        client, _ = authed_client
        _setup_owner(client)
        _upload_and_index(client)
        container: AppContainer = client.app.state.container  # type: ignore[attr-defined]
        container.evidence_qa._generator = FakeEvidenceQAGenerator(
            EvidenceQADraft(
                status="answered",
                claims=[
                    EvidenceQAClaim(
                        claim_id="C1",
                        text="可信结论。",
                        citation_ids=["E1"],
                    ),
                    EvidenceQAClaim(
                        claim_id="C2",
                        text="无引用结论。",
                        citation_ids=[],
                    ),
                ],
            )
        )

        response = _answer(
            client,
            question="问题",
            corpora=["user_docs"],
        )

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "partially_answered"
        assert [claim["claim_id"] for claim in body["claims"]] == ["C1"]
        assert body["repair_report"]["status"] == "repaired"
        assert body["repair_report"]["removal_reasons"] == {"C2": ["uncited"]}
        assert "C2" in body["unanswered_aspects"][0]

    def test_scope_validation_and_owner_isolation(self, authed_client) -> None:
        client, _ = authed_client
        _setup_owner(client)
        uploaded = _upload_and_index(client)
        _switch_actor(client, "github:outsider")
        response = client.post(
            "/api/v2/qa",
            json={
                "question": "问题",
                "corpora": ["user_docs"],
                "document_ids": [uploaded["document"]["document_id"]],
            },
        )
        assert response.status_code == 400
        assert (
            client.post(
                "/api/v2/qa",
                json={"question": "问题", "corpora": ["user_docs"], "owner_id": "github:editor"},
            ).status_code
            == 422
        )

    def test_rejects_extra_fields_and_overlong_question(
        self,
        authed_client: tuple[TestClient, dict[str, Any]],
    ) -> None:
        client, _ = authed_client
        extra = client.post(
            "/api/v2/qa",
            json={
                "question": "问题",
                "corpora": ["regulatory"],
                "actor_id": "github:admin",
            },
        )
        assert extra.status_code == 422
        overlong = client.post(
            "/api/v2/qa",
            json={"question": "x" * 2001, "corpora": ["regulatory"]},
        )
        assert overlong.status_code == 422

"""Real SQLite, parser, object files and threaded pipeline; only model/embedding are Fake."""

from __future__ import annotations

import json
import time
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

from api.v2 import build_v2_router
from api.v2.errors import install_exception_handlers
from app.container import AppContainer
from config import Settings
from infra.storage._db import SqliteConnectionPool
from infra.storage.sqlite_task_repo import SqliteTaskRepo
from tests.fakes import (
    FakeChat,
    FakeClaimSupportVerifier,
    FakeEmbed,
    FakeEvidenceQAGenerator,
    FakeKbRepo,
    FakeRetrieve,
    FakeToolCallingModel,
)


def test_persistent_conversation_and_real_attachment_pipeline(tmp_path: Path) -> None:
    model = FakeToolCallingModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "id": "search1",
                        "name": "search_user_docs",
                        "args": {"query": "境外接收方责任"},
                    }
                ],
            ),
            AIMessage(content="材料要求境外接收方采取安全措施。"),
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "id": "search2",
                        "name": "search_user_docs",
                        "args": {"query": "继续解释责任"},
                    }
                ],
            ),
            AIMessage(content="延续前文，双方需要约定安全保护责任。"),
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "id": "search3",
                        "name": "search_user_docs",
                        "args": {"query": "清除后还能引用吗"},
                    }
                ],
            ),
            AIMessage(content="不应绕过附件范围。"),
        ]
    )
    settings = Settings(
        _env_file=None,
        llm_provider="local",
        embed_provider="local",
        sqlite_db_path=str(tmp_path / "chat.sqlite3"),
        langgraph_checkpoint_db_path=str(tmp_path / "checkpoints.sqlite3"),
        object_store_dir=str(tmp_path / "objects"),
        enable_reranker=False,
        memory_summary_enabled=False,
        rate_limit_enabled=False,
    )
    container = AppContainer(
        settings,
        embedder=FakeEmbed(),
        chat=FakeChat(),
        agent_model=model,
        retriever=FakeRetrieve(),
        kb_repo=FakeKbRepo(),
        evidence_qa_generator=FakeEvidenceQAGenerator(),
        claim_support_verifier=FakeClaimSupportVerifier(),
    )
    app = FastAPI()
    app.include_router(build_v2_router(container), prefix="/api/v2")
    install_exception_handlers(app)
    try:
        with TestClient(app) as client:
            owner = client.post("/api/v2/auth/anonymous").json()["user"]["user_id"]
            raw = "境外接收方责任：采取安全措施并保护个人信息。".encode()
            uploaded = client.post(
                "/api/v2/files", files={"file": ("agreement.txt", raw, "text/plain")}
            )
            assert uploaded.status_code == 201
            doc_id = uploaded.json()["document"]["document_id"]
            other = client.post(
                "/api/v2/files",
                files={
                    "file": (
                        "other.txt",
                        "B 材料：境外接收方承担另一项责任。".encode(),
                        "text/plain",
                    )
                },
            )
            assert other.status_code == 201
            other_doc_id = other.json()["document"]["document_id"]
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                detail = client.get(f"/api/v2/files/{doc_id}").json()
                other_detail = client.get(f"/api/v2/files/{other_doc_id}").json()
                if detail["document"]["status"] in {"ready", "failed"} and other_detail["document"][
                    "status"
                ] in {"ready", "failed"}:
                    break
                time.sleep(0.02)
            assert detail["document"]["status"] == "ready", detail
            assert other_detail["document"]["status"] == "ready", other_detail
            assert client.get(f"/api/v2/files/{doc_id}/download").content == raw
            response = client.post(
                "/api/v2/copilot/chat/stream",
                json={"message": "材料说了什么？", "attachment_doc_ids": [doc_id]},
            )
            assert response.status_code == 200
            assert "event: tool_call" in response.text and "event: tool_result" in response.text
            assert "安全措施" in response.text
            frames = [
                json.loads(line[6:])
                for line in response.text.splitlines()
                if line.startswith("data: ")
            ]
            task_id = next(frame["task_id"] for frame in frames if "task_id" in frame)
            run_id = next(frame["run_id"] for frame in frames if "run_id" in frame)
            answer = next(frame for frame in frames if "citations" in frame)
            assert {item["document_id"] for item in answer["citations"]} == {doc_id}, (
                answer,
                container.evidence_qa_generator.calls,
                container.claim_support_verifier.calls,
            )
            assert answer["citations"][0]["document_version_id"]
            assert answer["citations"][0]["source_version"] == "1"
            assert answer["citations"][0]["page_number"] == 1
            assert len(answer["citations"][0]["source_sha256"]) == 64
            run_status = client.get(f"/api/v2/copilot/runs/{run_id}").json()
            assert run_status["status"] == "completed"
            assert run_status["attachment_doc_ids"] == [doc_id]
            assert run_status["verified_citation_ids"] == [answer["citations"][0]["citation_id"]]
            # Agent 决策 2 次 + 附件检索 embedding + Claim 生成 + 语义验证。
            assert run_status["model_call_count"] == 5
            assert run_status["tool_call_count"] == 1
            task_detail = client.get(f"/api/v2/tasks/{task_id}").json()
            assert task_detail["attachment_doc_ids"] == [doc_id]
            second = client.post(
                "/api/v2/copilot/chat", json={"message": "继续解释责任", "task_id": task_id}
            )
            assert second.status_code == 200
            second_answer = next(
                event["payload"]
                for event in second.json()["events"]
                if event["event_type"] == "answer"
            )
            assert {item["document_id"] for item in second_answer["citations"]} == {doc_id}
            assert "材料说了什么" in str(model.calls[-2])
            assert container.retriever.calls == []

            cleared = client.post(
                "/api/v2/copilot/chat",
                json={
                    "message": "清除后还能引用吗",
                    "task_id": task_id,
                    "attachment_doc_ids": [],
                },
            )
            assert cleared.status_code == 200
            cleared_answer = next(
                event["payload"]
                for event in cleared.json()["events"]
                if event["event_type"] == "answer"
            )
            assert cleared_answer["citations"] == []
            assert len(container.retriever.calls) == 1
            detail = client.get(f"/api/v2/tasks/{task_id}").json()
            assert detail["attachment_doc_ids"] == []
            assert [message["role"] for message in detail["messages"]] == [
                "user",
                "assistant",
                "user",
                "assistant",
                "user",
                "assistant",
            ]
            assert (
                container.pool.get().execute("SELECT count(*) FROM tool_calls").fetchone()[0] == 3
            )
            linked_runs = (
                container.pool.get()
                .execute("SELECT DISTINCT run_id FROM tool_calls WHERE task_id = ?", (task_id,))
                .fetchall()
            )
            assert len(linked_runs) == 3
            assert all(row["run_id"] for row in linked_runs)
            with TestClient(app) as outsider:
                outsider.post("/api/v2/auth/anonymous")
                assert outsider.get(f"/api/v2/files/{doc_id}").status_code == 404
                assert outsider.get(f"/api/v2/tasks/{task_id}").status_code == 404
                assert (
                    outsider.post(
                        "/api/v2/copilot/chat", json={"message": "继续", "task_id": task_id}
                    ).status_code
                    == 404
                )
                assert (
                    outsider.get("/api/v2/files/search", params={"query": "责任"}).json()["hits"]
                    == []
                )
                assert (
                    outsider.post(
                        "/api/v2/copilot/chat",
                        json={"message": "读材料", "attachment_doc_ids": [doc_id]},
                    ).status_code
                    == 404
                )
        restored = SqliteTaskRepo(SqliteConnectionPool(settings.sqlite_db_path))
        assert restored.get(task_id, owner) is not None
        assert len(restored.list_messages(task_id)) == 6
    finally:
        container.close()

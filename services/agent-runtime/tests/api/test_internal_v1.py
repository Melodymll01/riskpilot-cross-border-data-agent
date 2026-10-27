from __future__ import annotations

import json
import secrets
import threading
import time
from pathlib import Path

import jwt
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator

from api.internal_v1 import build_internal_v1_router
from app.internal_runtime import InternalRuntimeService
from domain.errors import RuntimeConflict
from domain.runtime import DocumentResult, ResultProposal, RunExecute, RuntimeSignal
from infra.agents.langchain_copilot import AgentAttemptState, RestoredAgentAttempt
from infra.auth.service_principal import ServicePrincipalMiddleware


class FakeAgent:
    def __init__(self) -> None:
        self.started = threading.Event()
        self.checkpoint_thread_id: str | None = None
        self.restore_calls: list[RunExecute] = []

    def execute(self, state, control, *, resume=False):
        self.started.set()
        if state.snapshot.question == "cancel-me":
            control.wait(5)
            raise AssertionError("cancelled execution continued")
        if state.snapshot.question == "interrupt-me" and not resume:
            self.checkpoint_thread_id = state.snapshot.thread_id
            control.interrupt()
            yield RuntimeSignal("interrupted", {"stage": "agent"})
            return
        yield RuntimeSignal("stage", {"stage": "agent"})
        yield RuntimeSignal(
            "result_proposal",
            ResultProposal(
                status="completed",
                answer="verified",
                citations=[],
                verified_citation_ids=[],
                verified_context_message_ids=[],
                error_type=None,
                end_reason="answered",
            ).model_dump(),
        )

    def restore(self, snapshot: RunExecute) -> RestoredAgentAttempt:
        self.restore_calls.append(snapshot)
        if not snapshot.resume or snapshot.thread_id != self.checkpoint_thread_id:
            raise RuntimeConflict("checkpoint mismatch")
        return RestoredAgentAttempt(
            state=AgentAttemptState(snapshot=snapshot),
            model_calls=1,
            tool_calls=0,
            input_tokens=3,
            output_tokens=2,
        )


class FakeDocuments:
    def __init__(self) -> None:
        self.commands = []

    def process(self, body):
        self.commands.append(body)
        return DocumentResult(
            document_id=body.document_id,
            status="ready",
            title=body.source_name,
            chunk_count=1,
            error_code=None,
            message="ok",
        )


def _snapshot(question: str = "hello") -> dict:
    return {
        "schema_version": "1.0",
        "run_id": "run-1",
        "attempt_id": 1,
        "thread_id": "thread-1",
        "owner_id": "github:alice",
        "task_id": "task-1",
        "input_message_id": "message-1",
        "question": question,
        "attachment_versions": [],
        "memory_snapshot": [],
        "recent_messages": [],
        "conversation_summary": "",
        "budget": {
            "deadline_at": time.time() + 30,
            "max_model_calls": 4,
            "max_tool_calls": 4,
        },
    }


def _signed(secret: str, method: str, target: str, owner: str = "github:alice") -> dict[str, str]:
    now = int(time.time())
    claims = {
        "iss": "riskpilot-go",
        "aud": "riskpilot-agent",
        "sub": owner,
        "iat": now,
        "exp": now + 30,
        "jti": secrets.token_hex(24),
        "method": method,
        "target": target,
    }
    return {"X-Riskpilot-Identity": jwt.encode(claims, secret, algorithm="HS256")}


@pytest.fixture
def private_runtime(tmp_path):
    secret = "internal-test-secret-" * 3
    key = tmp_path / "service.key"
    key.write_text(secret)
    agent = FakeAgent()
    runtime = InternalRuntimeService(
        agent,  # type: ignore[arg-type]
        model_call_timeout_seconds=2,
        tool_call_timeout_seconds=2,
        poll_seconds=0.01,
    )
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.include_router(build_internal_v1_router(runtime, FakeDocuments()), prefix="/internal/v1")
    app.add_middleware(ServicePrincipalMiddleware, key_file=str(key))
    with TestClient(app) as client:
        yield client, runtime, agent, secret
    runtime.close()


def _events(response) -> list[dict]:
    return [json.loads(line) for line in response.text.splitlines() if line]


def test_execute_streams_only_frozen_run_events(private_runtime) -> None:
    client, _, _, secret = private_runtime
    path = "/internal/v1/runs/execute"
    response = client.post(path, json=_snapshot(), headers=_signed(secret, "POST", path))
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/x-ndjson")
    events = _events(response)
    assert [event["event_type"] for event in events] == ["stage", "usage", "result_proposal"]
    assert [event["event_seq"] for event in events] == [1, 2, 3]
    schema_path = Path(__file__).parents[4] / "contracts/internal/v1/run-event.schema.json"
    validator = Draft202012Validator(json.loads(schema_path.read_text()))
    for event in events:
        validator.validate(event)
        assert event["schema_version"] == "1.0"
        assert event["run_id"] == "run-1"
        assert event["attempt_id"] == 1


def test_resume_reposts_execute_and_restores_checkpoint_after_python_restart(
    private_runtime, tmp_path
) -> None:
    client, _, _, secret = private_runtime
    execute_path = "/internal/v1/runs/execute"
    first = client.post(
        execute_path,
        json=_snapshot("interrupt-me"),
        headers=_signed(secret, "POST", execute_path),
    )
    assert [item["event_type"] for item in _events(first)] == ["interrupted"]

    _, _, agent, _ = private_runtime
    restarted_runtime = InternalRuntimeService(
        agent,  # type: ignore[arg-type]
        model_call_timeout_seconds=2,
        tool_call_timeout_seconds=2,
        poll_seconds=0.01,
    )
    key = tmp_path / "restart-service.key"
    key.write_text(secret)
    restarted_app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    restarted_app.include_router(
        build_internal_v1_router(restarted_runtime, FakeDocuments()),
        prefix="/internal/v1",
    )
    restarted_app.add_middleware(ServicePrincipalMiddleware, key_file=str(key))
    resumed_snapshot = _snapshot("interrupt-me")
    resumed_snapshot["attempt_id"] = 2
    resumed_snapshot["resume"] = True
    with TestClient(restarted_app) as restarted_client:
        resumed = restarted_client.post(
            execute_path,
            json=resumed_snapshot,
            headers=_signed(secret, "POST", execute_path),
        )
    events = _events(resumed)
    assert resumed.status_code == 200
    assert [item["event_type"] for item in events] == ["stage", "usage", "result_proposal"]
    assert [item["event_seq"] for item in events] == [1, 2, 3]
    assert all(item["attempt_id"] == 2 for item in events)
    assert agent.restore_calls[-1].thread_id == "thread-1"
    restarted_runtime.close()


def test_cancel_is_cooperative_and_emits_no_result_proposal(private_runtime) -> None:
    client, runtime, agent, secret = private_runtime
    events: list = []
    iterator = runtime.execute(RunExecute.model_validate(_snapshot("cancel-me")))
    consumer = threading.Thread(target=lambda: events.extend(iterator), daemon=True)
    consumer.start()
    assert agent.started.wait(timeout=1)

    path = "/internal/v1/runs/run-1/cancel"
    response = client.post(
        path,
        json={
            "schema_version": "1.0",
            "run_id": "run-1",
            "attempt_id": 1,
            "reason": "user_cancelled",
        },
        headers=_signed(secret, "POST", path),
    )
    consumer.join(timeout=2)
    assert response.status_code == 204
    assert not consumer.is_alive()
    assert all(event.event_type != "result_proposal" for event in events)


def test_external_routes_and_contract_extras_are_absent(private_runtime) -> None:
    client, _, _, secret = private_runtime
    old_path = "/api/v2/copilot/chat/stream"
    assert client.post(old_path, headers=_signed(secret, "POST", old_path)).status_code == 404
    assert client.get("/", headers=_signed(secret, "GET", "/")).status_code == 404
    assert (
        client.get("/openapi.json", headers=_signed(secret, "GET", "/openapi.json")).status_code
        == 404
    )
    resume_path = "/internal/v1/runs/run-1/resume"
    assert client.post(resume_path, headers=_signed(secret, "POST", resume_path)).status_code == 404

    path = "/internal/v1/runs/execute"
    body = _snapshot()
    body["unexpected"] = True
    assert client.post(path, json=body, headers=_signed(secret, "POST", path)).status_code == 422


def test_identity_owner_binding_and_replay_fail_closed(private_runtime) -> None:
    client, _, _, secret = private_runtime
    path = "/internal/v1/runs/execute"
    assert client.post(path, json=_snapshot()).status_code == 401
    assert (
        client.post(
            path,
            json=_snapshot(),
            headers=_signed(secret, "POST", path, owner="github:bob"),
        ).status_code
        == 403
    )
    headers = _signed(secret, "POST", path)
    first = client.post(path, json=_snapshot(), headers=headers)
    assert first.status_code == 200
    assert client.post(path, json=_snapshot(), headers=headers).status_code == 401


def test_document_process_uses_strict_internal_contract(private_runtime) -> None:
    client, _, _, secret = private_runtime
    path = "/internal/v1/documents/process"
    content = "aGVsbG8="
    body = {
        "schema_version": "1.0",
        "operation": "ingest",
        "document_id": "doc-1",
        "version_id": "ver-1",
        "actor_id": "github:alice",
        "owner_id": "github:alice",
        "source_name": "policy.txt",
        "source_type": "file",
        "source_url": None,
        "category": "law",
        "mime_type": "text/plain",
        "sha256": "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824",
        "content_base64": content,
    }
    response = client.post(path, json=body, headers=_signed(secret, "POST", path))
    assert response.status_code == 200
    assert response.json() == {
        "schema_version": "1.0",
        "document_id": "doc-1",
        "status": "ready",
        "title": "policy.txt",
        "chunk_count": 1,
        "error_code": None,
        "message": "ok",
    }

    missing_required = dict(body)
    missing_required.pop("owner_id")
    assert (
        client.post(
            path,
            json=missing_required,
            headers=_signed(secret, "POST", path),
        ).status_code
        == 422
    )

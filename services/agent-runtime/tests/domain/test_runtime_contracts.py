import json
import time
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from pydantic import ValidationError

from domain.runtime import (
    DocumentProcess,
    DocumentResult,
    RunControlCommand,
    RunEvent,
    RunExecute,
    ServicePrincipal,
)

CONTRACTS = Path(__file__).parents[4] / "contracts/internal/v1"


def _execute_payload() -> dict:
    return {
        "schema_version": "1.0",
        "run_id": "r1",
        "attempt_id": 1,
        "thread_id": "t1",
        "owner_id": "owner",
        "task_id": "task",
        "input_message_id": "m1",
        "question": "question",
        "attachment_versions": [],
        "memory_snapshot": [],
        "budget": {"deadline_at": time.time() + 10, "max_model_calls": 2, "max_tool_calls": 2},
    }


@pytest.mark.parametrize(
    ("filename", "model", "payload"),
    [
        ("run-execute.schema.json", RunExecute, _execute_payload()),
        (
            "run-control.schema.json",
            RunControlCommand,
            {"schema_version": "1.0", "run_id": "r1", "attempt_id": 1, "reason": "cancel"},
        ),
        (
            "run-event.schema.json",
            RunEvent,
            {
                "schema_version": "1.0",
                "run_id": "r1",
                "attempt_id": 1,
                "event_seq": 1,
                "event_type": "stage",
                "payload": {"stage": "agent"},
            },
        ),
        (
            "service-principal.schema.json",
            ServicePrincipal,
            {
                "iss": "riskpilot-go",
                "aud": "riskpilot-agent",
                "sub": "owner",
                "iat": 100,
                "exp": 130,
                "jti": "0123456789abcdef",
                "method": "POST",
                "target": "/internal/v1/runs/execute",
            },
        ),
        (
            "document-process.schema.json",
            DocumentProcess,
            {
                "schema_version": "1.0",
                "operation": "delete",
                "document_id": "doc-1",
                "version_id": "ver-1",
                "actor_id": "owner",
                "owner_id": "owner",
                "source_name": "policy.txt",
                "source_type": "file",
                "source_url": None,
                "category": "law",
                "mime_type": "text/plain",
                "sha256": "a" * 64,
                "content_base64": None,
            },
        ),
        (
            "document-result.schema.json",
            DocumentResult,
            {
                "schema_version": "1.0",
                "document_id": "doc-1",
                "status": "ready",
                "title": "Policy",
                "chunk_count": 1,
                "error_code": None,
                "message": "ok",
            },
        ),
    ],
)
def test_models_accept_frozen_schema_examples(filename, model, payload) -> None:
    Draft202012Validator(json.loads((CONTRACTS / filename).read_text())).validate(payload)
    model.model_validate(payload)


def test_models_reject_additional_properties() -> None:
    payload = _execute_payload()
    payload["legacy_cookie"] = "forbidden"
    with pytest.raises(ValidationError, match="extra_forbidden"):
        RunExecute.model_validate(payload)


def test_event_payloads_are_stricter_than_generic_contract_object() -> None:
    with pytest.raises(ValidationError, match="extra_forbidden"):
        RunEvent(
            run_id="r1",
            attempt_id=1,
            event_seq=1,
            event_type="stage",
            payload={"stage": "agent", "status": "running"},
        )

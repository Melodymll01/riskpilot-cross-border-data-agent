"""LangSmith Adapter 的默认关闭、白名单和隐私保护测试。"""

from __future__ import annotations

import pytest

from config import Settings
from domain.ports import TracePort
from infra.observability import (
    LangSmithTraceAdapter,
    NoopTraceAdapter,
    sanitize_trace_metadata,
)


def test_noop_trace_is_default_and_never_requires_credentials() -> None:
    from app.factories import build_trace

    trace = build_trace(Settings(_env_file=None))

    assert isinstance(trace, NoopTraceAdapter)
    assert isinstance(trace, TracePort)
    with trace.span(
        "riskpilot.test",
        metadata={"prompt": "不应记录的案件正文"},
    ) as span:
        span.add_metadata({"answer": "不应记录的模型回答"})


def test_enabled_trace_requires_key_and_hash_salt() -> None:
    from app.factories import build_trace

    settings = Settings(
        _env_file=None,
        risk_pilot_langsmith_enabled=True,
    )

    try:
        build_trace(settings)
    except ValueError as exc:
        assert "LANGSMITH_API_KEY" in str(exc)
    else:
        raise AssertionError("启用 LangSmith 但未配置 key 必须失败")


def test_global_langsmith_switch_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.factories import build_trace

    monkeypatch.setenv("LANGSMITH_TRACING", "true")

    with pytest.raises(ValueError, match="全局追踪开关"):
        build_trace(Settings(_env_file=None))


def test_metadata_allowlist_hashes_ids_and_drops_content() -> None:
    sanitized = sanitize_trace_metadata(
        {
            "owner_id": "github:alice",
            "case_id": "case_001",
            "ruleset_version": "private-customer-rules-v7",
            "query": "某企业是否需要申报安全评估",
            "document": "含个人信息的合同正文",
            "answer": "模型生成的长答案",
            "status": "completed",
            "query_length": 15,
            "web_search_used": False,
        },
        hash_salt="test-salt-with-16-characters",
    )

    assert sanitized["owner_id_hash"] != "github:alice"
    assert sanitized["case_id_hash"] != "case_001"
    assert sanitized["ruleset_version_hash"] != "private-customer-rules-v7"
    assert len(str(sanitized["owner_id_hash"])) == 24
    assert sanitized["status"] == "completed"
    assert sanitized["query_length"] == 15
    assert sanitized["web_search_used"] is False
    assert "query" not in sanitized
    assert "document" not in sanitized
    assert "answer" not in sanitized


def test_langsmith_client_hides_payloads_errors_events_and_serialized_graph() -> None:
    adapter = LangSmithTraceAdapter(
        api_key="test-key",
        endpoint="https://example.invalid",
        project="riskpilot-test",
        sampling_rate=1.0,
        hash_salt="test-salt-with-16-characters",
    )
    client = adapter._client

    transformed = client._run_transform(
        {
            "name": "customer-secret-case-name",
            "run_type": "llm",
            "inputs": {"prompt": "secret prompt"},
            "outputs": {"answer": "secret answer"},
            "error": "contract body leaked from exception",
            "events": [{"name": "custom", "payload": "secret event"}],
            "attachments": {"case.pdf": ("application/pdf", b"secret")},
            "serialized": {"kwargs": {"template": "secret template"}},
            "extra": {
                "metadata": {
                    "owner_id": "github:alice",
                    "document": "secret document",
                    "status": "failed",
                }
            },
        },
        copy=True,
    )

    assert transformed["inputs"] == {}
    assert transformed["outputs"] == {}
    assert transformed["name"] == "riskpilot.framework.operation"
    assert transformed["error"] == "[redacted by RiskPilot privacy policy]"
    assert "events" not in transformed
    assert "attachments" not in transformed
    assert "serialized" not in transformed
    assert transformed["extra"]["metadata"]["owner_id_hash"] != "github:alice"
    assert len(transformed["extra"]["metadata"]["owner_id_hash"]) == 24
    assert "document" not in transformed["extra"]["metadata"]

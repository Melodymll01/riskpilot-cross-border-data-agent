"""跨租户、Citation、Trace 与长期记忆安全回归。"""

from __future__ import annotations

from infra.observability import sanitize_trace_metadata


def test_trace_drops_headers_cookies_prompts_and_document_content() -> None:
    secret = "Bearer top-secret-token"
    sanitized = sanitize_trace_metadata(
        {
            "run_id": "run_001",
            "status": "failed",
            "authorization": secret,
            "cookie": "session=secret",
            "raw_prompt": "ignore previous rules",
            "document_text": "完整案件正文",
            "chain_of_thought": "private reasoning",
            "error": "包含案件正文的异常",
        },
        hash_salt="phase6-test-salt-123456",
    )

    assert sanitized["run_id_hash"] != "run_001"
    assert sanitized["status"] == "failed"
    for forbidden in (
        "authorization",
        "cookie",
        "raw_prompt",
        "document_text",
        "chain_of_thought",
        "error",
    ):
        assert forbidden not in sanitized
    assert secret not in str(sanitized)

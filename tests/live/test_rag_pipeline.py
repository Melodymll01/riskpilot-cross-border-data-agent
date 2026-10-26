"""Explicit live validation through current file and conversational APIs."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.live
def test_full_rag_pipeline_real_services(live_app) -> None:
    law = json.loads((ROOT / "assets/demo/pipl_article19.json").read_text())
    with TestClient(live_app) as client:
        assert client.post("/api/v2/auth/anonymous").status_code == 201
        uploaded = client.post(
            "/api/v2/files",
            files={
                "file": ("article19.txt", law["text"].encode(), "text/plain"),
            },
        )
        assert uploaded.status_code == 201, uploaded.text
        document_id = uploaded.json()["document"]["document_id"]
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            detail = client.get(f"/api/v2/files/{document_id}").json()
            if detail["document"]["status"] in {"ready", "failed"}:
                break
            time.sleep(0.1)
        assert detail["document"]["status"] == "ready", detail
        response = client.post(
            "/api/v2/copilot/chat",
            json={
                "message": "仅依据上传的法规摘录，解释个人信息保存期限原则。请检索附件并引用原文。",
                "attachment_doc_ids": [document_id],
            },
        )
        assert response.status_code == 200, response.text
        events = response.json()["events"]
        answer = next(e["payload"] for e in reversed(events) if e["event_type"] == "answer")
        assert answer["citations"], answer
        assert all(c["document_id"] == document_id for c in answer["citations"])
        assert "保存" in answer["text"] or "期限" in answer["text"]

"""Step 026d —— RAG 链路真服务端到端验证。

唯一一条贯穿真实外部服务的用例：
匿名登录 → 上传《个人信息保护法》→ 提问 → 校验真 LLM 产出答案。

覆盖到的真实路径：
- 上传 → 真 **embedding-3**（智谱）把 chunk 向量化写入临时 chroma（chunk_count>=1 即证）
- 提问 → 真 query embedding + 检索 + 真 **GLM-5**（百炼）ReAct 推理产答

断言保持"宽松但有意义"：真 LLM 输出有随机性，只验非空 + 命中领域关键词 + ReAct 跑过，
不对法条号 / 引用条目做精确匹配，避免 flaky。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

# tests/live/test_rag_pipeline.py → 项目根
_ROOT = Path(__file__).resolve().parents[2]
LAW_FILE = _ROOT / "data" / "uploads" / "个人信息保护法.txt"


@pytest.mark.live
def test_full_rag_pipeline_real_services(live_app) -> None:
    assert LAW_FILE.exists(), f"测试语料缺失: {LAW_FILE}"

    with TestClient(live_app) as client:
        # 1) 匿名登录（自动持 session cookie）
        login = client.post("/api/v2/auth/anonymous")
        assert login.status_code == 201, login.text

        # 2) 上传文档 —— 触发真实 embedding 写入临时 chroma
        with LAW_FILE.open("rb") as fh:
            up = client.post(
                "/api/v2/documents/file",
                files={"file": ("个人信息保护法.txt", fh, "text/plain")},
            )
        assert up.status_code == 201, up.text
        ingest = up.json()
        assert ingest["success"] is True, ingest
        assert ingest["chunk_count"] >= 1, f"chunk_count 异常: {ingest}"
        source_name = ingest["source_name"]

        # 3) 提问 —— 真 query embedding + 检索 + 真 chat LLM（同步聚合端点）
        chat = client.post(
            "/api/v2/copilot/chat",
            json={
                "message": "根据《个人信息保护法》，个人信息向境外提供需要满足哪些条件？",
                "mode": "qa",
                "attachment_doc_ids": [source_name],
            },
        )
        assert chat.status_code == 200, chat.text
        events = chat.json()["events"]
        types = [e["event_type"] for e in events]

        # 4) 断言：ReAct 真跑过 + 真 LLM 产出有效答案
        assert "thought" in types, f"未见 thought（ReAct 未运行）；events={types}"
        assert "answer" in types, f"无 answer 事件；events={types}"

        answer = next(e for e in events if e["event_type"] == "answer")["payload"]
        text = answer["text"]
        assert isinstance(text, str) and len(text.strip()) >= 20, f"答案过短: {text!r}"
        assert any(
            kw in text for kw in ("个人信息", "出境", "境外", "评估", "保护")
        ), f"答案未命中领域关键词: {text!r}"

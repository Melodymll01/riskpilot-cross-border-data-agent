"""``ToolSpec`` + ``register_default_tools`` 行为契约测试。

不构造真实 AppContainer（避免拉起 SQLite/openai 等），而是用 SimpleNamespace
+ Fake Port 拼一个"瘦容器"传给 register_default_tools。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.agent.tools import ToolSpec, register_default_tools
from domain.models import Chunk, EvidenceJudgement, WebResult
from tests.fakes.fake_evidence import FakeEvidence
from tests.fakes.fake_retrieve import FakeRetrieve
from tests.fakes.fake_websearch import FakeWebSearch


def _chunk(cid: str, text: str = "片段", score: float = 0.9) -> Chunk:
    return Chunk(
        chunk_id=cid,
        text=text,
        source_type="law",
        source_name="PIPL",
        title="个人信息保护法",
        source_url="https://example.com/pipl",
        category="law",
        score=score,
    )


def _container(
    *,
    retriever: object | None = None,
    web_search: object | None = None,
    evidence: object | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        retriever=retriever or FakeRetrieve(),
        web_search=web_search or FakeWebSearch(),
        evidence=evidence or FakeEvidence(),
    )


class TestToolSpec:
    def test_frozen(self) -> None:
        from dataclasses import FrozenInstanceError

        spec = ToolSpec(
            name="x",
            description="d",
            parameters_schema={},
            handler=lambda **_: None,
        )
        with pytest.raises(FrozenInstanceError):
            spec.name = "y"  # type: ignore[misc]

    def test_defaults(self) -> None:
        spec = ToolSpec(
            name="x", description="d", parameters_schema={}, handler=lambda **_: None
        )
        assert spec.timeout_s == 30.0
        assert spec.requires_owner is True


class TestRegisterDefaultTools:
    def test_returns_expected_tool_set(self) -> None:
        registry = register_default_tools(_container())  # type: ignore[arg-type]
        assert set(registry.keys()) == {
            "search_law",
            "search_user_docs",
            "web_search",
            "evidence_judge",
        }

    def test_all_specs_well_formed(self) -> None:
        registry = register_default_tools(_container())  # type: ignore[arg-type]
        for name, spec in registry.items():
            assert spec.name == name
            assert spec.description
            assert "type" in spec.parameters_schema
            assert callable(spec.handler)

    def test_search_law_calls_retriever_with_law_corpus(self) -> None:
        fake = FakeRetrieve([_chunk("c1"), _chunk("c2")])
        registry = register_default_tools(_container(retriever=fake))  # type: ignore[arg-type]
        result = registry["search_law"].handler(
            query="PIPL 38条", owner_id="anon:x", top_k=3
        )
        assert fake.calls[0]["corpus"] == "law"
        assert fake.calls[0]["owner_id"] == "anon:x"
        assert fake.calls[0]["top_k"] == 3
        assert len(result) == 2
        assert result[0]["chunk_id"] == "c1"
        assert result[0]["source_name"] == "PIPL"

    def test_search_user_docs_uses_user_docs_corpus(self) -> None:
        fake = FakeRetrieve([_chunk("u1")])
        registry = register_default_tools(_container(retriever=fake))  # type: ignore[arg-type]
        registry["search_user_docs"].handler(
            query="政策摘要", owner_id="github:alice"
        )
        assert fake.calls[0]["corpus"] == "user_docs"
        assert fake.calls[0]["owner_id"] == "github:alice"

    def test_search_user_docs_isolates_by_owner(self) -> None:
        fake = FakeRetrieve([_chunk("u1")])
        registry = register_default_tools(_container(retriever=fake))  # type: ignore[arg-type]
        registry["search_user_docs"].handler(query="q", owner_id="anon:1")
        registry["search_user_docs"].handler(query="q", owner_id="anon:2")
        assert fake.calls[0]["owner_id"] == "anon:1"
        assert fake.calls[1]["owner_id"] == "anon:2"

    def test_web_search_serializes_results(self) -> None:
        results = [
            WebResult(title="T1", url="https://a", snippet="s1"),
            WebResult(title="T2", url="https://b", snippet="s2"),
        ]
        fake = FakeWebSearch(results)
        registry = register_default_tools(_container(web_search=fake))  # type: ignore[arg-type]
        out = registry["web_search"].handler(
            query="跨境数据传输", owner_id="anon:x"
        )
        assert fake.calls[0] == ("跨境数据传输", 3)
        assert out == [
            {"title": "T1", "url": "https://a", "snippet": "s1"},
            {"title": "T2", "url": "https://b", "snippet": "s2"},
        ]

    def test_evidence_judge_serializes_judgement(self) -> None:
        fake = FakeEvidence(
            {
                "F1": EvidenceJudgement(
                    factor_id="F1", label="high", rationale="敏感数据", confidence=0.9
                )
            }
        )
        registry = register_default_tools(_container(evidence=fake))  # type: ignore[arg-type]
        out = registry["evidence_judge"].handler(
            factor_id="F1",
            document="doc text",
            target="overseas",
            owner_id="anon:x",
        )
        assert out["factor_id"] == "F1"
        assert out["label"] == "high"
        assert out["confidence"] == 0.9
        assert fake.calls[0][0] == "F1"
        assert fake.calls[0][1] == {"document": "doc text", "target": "overseas"}

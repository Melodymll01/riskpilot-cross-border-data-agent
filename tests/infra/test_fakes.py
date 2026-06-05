"""Fake 适配器自身满足 Port 协议的契约测试。"""

from __future__ import annotations

from domain.models import Chunk, EvidenceJudgement, WebResult
from domain.ports import (
    ChatPort,
    EmbedPort,
    EvidencePort,
    RetrievePort,
    TaskRepoPort,
    UserRepoPort,
    WebSearchPort,
)
from tests.fakes import (
    FakeChat,
    FakeEmbed,
    FakeEvidence,
    FakeRetrieve,
    FakeWebSearch,
    InMemoryTaskRepo,
    InMemoryUserRepo,
)


class TestFakeProtocolConformance:
    def test_fake_chat_is_chat_port(self) -> None:
        assert isinstance(FakeChat(), ChatPort)

    def test_fake_embed_is_embed_port(self) -> None:
        assert isinstance(FakeEmbed(), EmbedPort)

    def test_fake_retrieve_is_retrieve_port(self) -> None:
        assert isinstance(FakeRetrieve(), RetrievePort)

    def test_fake_evidence_is_evidence_port(self) -> None:
        assert isinstance(FakeEvidence(), EvidencePort)

    def test_fake_websearch_is_web_search_port(self) -> None:
        assert isinstance(FakeWebSearch(), WebSearchPort)

    def test_in_memory_user_repo_is_user_repo_port(self) -> None:
        assert isinstance(InMemoryUserRepo(), UserRepoPort)

    def test_in_memory_task_repo_is_task_repo_port(self) -> None:
        assert isinstance(InMemoryTaskRepo(), TaskRepoPort)


class TestFakeBehavior:
    def test_fake_chat_records_calls(self) -> None:
        chat = FakeChat(responses=["a", "b"])
        assert chat.chat([{"role": "user", "content": "1"}]) == "a"
        assert chat.chat([{"role": "user", "content": "2"}], temperature=0.7) == "b"
        assert chat.chat([{"role": "user", "content": "3"}]) == "b"  # 复用最后
        assert len(chat.calls) == 3
        assert chat.calls[1]["temperature"] == 0.7

    def test_fake_embed_deterministic(self) -> None:
        emb = FakeEmbed(dim=4)
        v1 = emb.embed(["hello"])
        v2 = emb.embed(["hello"])
        assert v1 == v2
        assert len(v1[0]) == 4

    def test_fake_retrieve_respects_top_k(self) -> None:
        chunks = [
            Chunk(
                chunk_id=f"c{i}", text=f"t{i}",
                source_type="law", source_name="PIPL",
            )
            for i in range(5)
        ]
        ret = FakeRetrieve(chunks=chunks)
        result = ret.retrieve("q", top_k=3)
        assert [c.chunk_id for c in result] == ["c0", "c1", "c2"]
        assert ret.calls[0]["query"] == "q"

    def test_fake_evidence_default_label(self) -> None:
        ev = FakeEvidence()
        j = ev.judge("F1", {"region": "EU"})
        assert isinstance(j, EvidenceJudgement)
        assert j.label == "moderate"
        assert ev.calls == [("F1", {"region": "EU"})]

    def test_fake_websearch_max_results(self) -> None:
        ws = FakeWebSearch(
            results=[
                WebResult(title=f"t{i}", url=f"https://x.com/{i}")
                for i in range(5)
            ]
        )
        out = ws.search("q", max_results=2)
        assert len(out) == 2
        assert ws.calls == [("q", 2)]

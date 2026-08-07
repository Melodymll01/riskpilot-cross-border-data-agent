"""Fake 适配器自身满足 Port 协议的契约测试。"""

from __future__ import annotations

import pytest

from domain.models import Chunk, EvidenceJudgement, KbChunk, KbDocument, WebResult
from domain.ports import (
    AgentRunRepoPort,
    AssessmentRepoPort,
    AuditLogPort,
    CaseFactRepoPort,
    ChatPort,
    DocumentLoaderPort,
    DocumentParserPort,
    DocumentRepoPort,
    EmbedPort,
    EvidenceChunkerPort,
    EvidenceIndexPort,
    EvidencePort,
    KbDocumentRepoPort,
    ObjectStorePort,
    PolicyRuleRepoPort,
    RetrievePort,
    TaskRepoPort,
    UserRepoPort,
    WebSearchPort,
)
from tests.fakes import (
    FakeAuditLogRepo,
    FakeChat,
    FakeDocumentLoader,
    FakeDocumentParser,
    FakeEmbed,
    FakeEvidence,
    FakeEvidenceChunker,
    FakeEvidenceIndex,
    FakeKbRepo,
    FakeObjectStore,
    FakeRetrieve,
    FakeWebSearch,
    InMemoryAgentRunRepo,
    InMemoryAssessmentRepo,
    InMemoryCaseFactRepo,
    InMemoryDocumentRepo,
    InMemoryPolicyRuleRepo,
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

    def test_fake_kb_repo_is_kb_document_repo_port(self) -> None:
        assert isinstance(FakeKbRepo(), KbDocumentRepoPort)

    def test_fake_document_loader_is_document_loader_port(self) -> None:
        assert isinstance(FakeDocumentLoader(), DocumentLoaderPort)

    def test_fake_document_parser_is_document_parser_port(self) -> None:
        assert isinstance(FakeDocumentParser(), DocumentParserPort)

    def test_fake_audit_log_is_audit_log_port(self) -> None:
        assert isinstance(FakeAuditLogRepo(), AuditLogPort)

    def test_in_memory_document_repo_is_document_repo_port(self) -> None:
        assert isinstance(InMemoryDocumentRepo(), DocumentRepoPort)

    def test_in_memory_case_fact_repo_is_case_fact_repo_port(self) -> None:
        assert isinstance(InMemoryCaseFactRepo(), CaseFactRepoPort)

    def test_in_memory_assessment_repo_is_assessment_repo_port(self) -> None:
        assert isinstance(InMemoryAssessmentRepo(), AssessmentRepoPort)

    def test_in_memory_agent_run_repo_is_agent_run_repo_port(self) -> None:
        assert isinstance(InMemoryAgentRunRepo(), AgentRunRepoPort)

    def test_fake_object_store_is_object_store_port(self) -> None:
        assert isinstance(FakeObjectStore(), ObjectStorePort)

    def test_in_memory_policy_rule_repo_is_port(self) -> None:
        assert isinstance(InMemoryPolicyRuleRepo(), PolicyRuleRepoPort)


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

    def test_evidence_fakes_satisfy_ports(self) -> None:
        assert isinstance(FakeEvidenceChunker(), EvidenceChunkerPort)
        assert isinstance(FakeEvidenceIndex(), EvidenceIndexPort)

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


class TestFakeKbRepoBehavior:
    """``FakeKbRepo`` 行为：列表 / 删除 / add_chunks 先删后插 / 长度断言。"""

    def _make_chunks(self, source: str, n: int) -> list[KbChunk]:
        return [
            KbChunk(
                chunk_id=f"{source}:{i}",
                text=f"text-{i}",
                source_name=source,
                source_type="file",
                title=f"{source} 标题",
                chunk_index=i,
            )
            for i in range(n)
        ]

    def test_empty_initial_state(self) -> None:
        repo = FakeKbRepo()
        assert repo.list_documents() == []
        assert repo.count_chunks() == 0
        assert repo.get_document("missing") is None

    def test_add_chunks_then_list(self) -> None:
        repo = FakeKbRepo()
        chunks = self._make_chunks("PIPL", 3)
        repo.add_chunks(chunks, [[0.1, 0.2]] * 3)

        docs = repo.list_documents()
        assert len(docs) == 1
        assert isinstance(docs[0], KbDocument)
        assert docs[0].source_name == "PIPL"
        assert docs[0].chunk_count == 3
        assert repo.count_chunks() == 3

    def test_add_chunks_overwrites_same_source(self) -> None:
        repo = FakeKbRepo()
        repo.add_chunks(self._make_chunks("PIPL", 3), [[0.1]] * 3)
        repo.add_chunks(self._make_chunks("PIPL", 5), [[0.2]] * 5)
        # 同 source 覆盖：总数 5（不是 3+5=8）
        assert repo.count_chunks() == 5
        assert repo.get_document("PIPL") is not None
        assert repo.get_document("PIPL").chunk_count == 5  # type: ignore[union-attr]

    def test_delete_document_returns_count(self) -> None:
        repo = FakeKbRepo()
        repo.add_chunks(self._make_chunks("PIPL", 3), [[0.1]] * 3)
        repo.add_chunks(self._make_chunks("DSL", 2), [[0.2]] * 2)
        n = repo.delete_document("PIPL")
        assert n == 3
        assert repo.delete_document("PIPL") == 0  # 已不存在
        assert repo.count_chunks() == 2

    def test_length_mismatch_raises(self) -> None:
        repo = FakeKbRepo()
        with pytest.raises(ValueError, match="长度必须一致"):
            repo.add_chunks(self._make_chunks("PIPL", 3), [[0.1]] * 2)

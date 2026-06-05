"""``ChromaKbRepo`` 适配器单测（Step 016a）。

策略：注入 ``_StubVectorStore``，验证：
- 满足 ``KbDocumentRepoPort`` 协议
- 字段转换正确（包括老 vector_store 的 ``"unknown"`` source_type 兜底）
- ``add_chunks`` 长度不一致时抛 ValueError，并触发"先删后插"语义
- ``ChunkWithMetadata`` 转换字段全到位

不起真实 chroma；真实端到端走 ``tests/api/test_documents.py``（Step 016c）。
"""

from __future__ import annotations

from typing import Any

import pytest

from domain.models import KbChunk, KbDocument
from domain.ports import KbDocumentRepoPort
from infra.kb import ChromaKbRepo


class _StubVectorStore:
    """伪 ``VectorStore``：实现 ChromaKbRepo 用到的最小子集，记录调用。"""

    def __init__(self, sources: list[dict[str, Any]] | None = None, total: int = 0) -> None:
        self._sources = sources or []
        self._total = total
        self.add_calls: list[tuple[list[Any], list[list[float]]]] = []
        self.delete_calls: list[str] = []

    def get_all_sources(self) -> list[dict[str, Any]]:
        return list(self._sources)

    def get_total_count(self) -> int:
        return self._total

    def add_chunks(self, chunks: list[Any], embeddings: list[list[float]]) -> None:
        self.add_calls.append((list(chunks), list(embeddings)))

    def delete_by_source(self, source_name: str) -> int:
        self.delete_calls.append(source_name)
        return sum(1 for s in self._sources if s.get("source_name") == source_name)


class TestProtocolConformance:
    def test_chroma_kb_repo_is_kb_document_repo_port(self) -> None:
        repo = ChromaKbRepo(_StubVectorStore())  # type: ignore[arg-type]
        assert isinstance(repo, KbDocumentRepoPort)


class TestRead:
    def test_list_documents_maps_fields(self) -> None:
        vs = _StubVectorStore(
            sources=[
                {
                    "source_type": "file",
                    "source_name": "PIPL.txt",
                    "title": "个人信息保护法",
                    "source_url": "",
                    "chunk_count": 12,
                },
                {
                    "source_type": "web",
                    "source_name": "https://example.com/x",
                    "title": "示例网页",
                    "source_url": "https://example.com/x",
                    "chunk_count": 3,
                },
            ]
        )
        repo = ChromaKbRepo(vs)  # type: ignore[arg-type]
        docs = repo.list_documents()
        assert [d.source_name for d in docs] == ["PIPL.txt", "https://example.com/x"]
        assert docs[0].source_type == "file"
        assert docs[0].title == "个人信息保护法"
        assert docs[0].source_url is None  # 空字符串归一为 None
        assert docs[0].chunk_count == 12
        assert docs[1].source_type == "web"
        assert docs[1].source_url == "https://example.com/x"
        # 都是 frozen domain 模型
        assert all(isinstance(d, KbDocument) for d in docs)

    def test_list_documents_unknown_source_type_falls_back_to_file(self) -> None:
        vs = _StubVectorStore(
            sources=[{"source_type": "unknown", "source_name": "x", "chunk_count": 1}]
        )
        repo = ChromaKbRepo(vs)  # type: ignore[arg-type]
        docs = repo.list_documents()
        assert docs[0].source_type == "file"  # 兜底

    def test_get_document_hit_and_miss(self) -> None:
        vs = _StubVectorStore(
            sources=[{"source_type": "file", "source_name": "PIPL.txt", "chunk_count": 1}]
        )
        repo = ChromaKbRepo(vs)  # type: ignore[arg-type]
        assert repo.get_document("PIPL.txt") is not None
        assert repo.get_document("missing") is None

    def test_count_chunks_proxies_total(self) -> None:
        vs = _StubVectorStore(total=42)
        repo = ChromaKbRepo(vs)  # type: ignore[arg-type]
        assert repo.count_chunks() == 42


class TestWrite:
    def _make_chunks(self, source: str, n: int) -> list[KbChunk]:
        return [
            KbChunk(
                chunk_id=f"{source}:{i}",
                text=f"text-{i}",
                source_name=source,
                source_type="file",
                title="标题",
                chunk_index=i,
            )
            for i in range(n)
        ]

    def test_delete_document_empty_raises(self) -> None:
        repo = ChromaKbRepo(_StubVectorStore())  # type: ignore[arg-type]
        with pytest.raises(ValueError, match="不能为空"):
            repo.delete_document("")

    def test_delete_document_proxies(self) -> None:
        vs = _StubVectorStore(sources=[{"source_type": "file", "source_name": "x", "chunk_count": 5}])
        repo = ChromaKbRepo(vs)  # type: ignore[arg-type]
        repo.delete_document("x")
        assert vs.delete_calls == ["x"]

    def test_add_chunks_length_mismatch_raises(self) -> None:
        repo = ChromaKbRepo(_StubVectorStore())  # type: ignore[arg-type]
        with pytest.raises(ValueError, match="长度必须一致"):
            repo.add_chunks(self._make_chunks("PIPL", 3), [[0.1]] * 2)

    def test_add_chunks_empty_noop(self) -> None:
        vs = _StubVectorStore()
        repo = ChromaKbRepo(vs)  # type: ignore[arg-type]
        repo.add_chunks([], [])
        assert vs.add_calls == []
        assert vs.delete_calls == []

    def test_add_chunks_does_delete_then_insert(self) -> None:
        vs = _StubVectorStore()
        repo = ChromaKbRepo(vs)  # type: ignore[arg-type]
        chunks = self._make_chunks("PIPL", 3)
        repo.add_chunks(chunks, [[0.1]] * 3)
        # 先删
        assert vs.delete_calls == ["PIPL"]
        # 后插
        assert len(vs.add_calls) == 1
        cwm_list, embs = vs.add_calls[0]
        assert len(cwm_list) == 3
        assert len(embs) == 3
        # ChunkWithMetadata 字段对齐
        assert cwm_list[0].chunk_id == "PIPL:0"
        assert cwm_list[0].text == "text-0"
        assert cwm_list[0].source_name == "PIPL"
        assert cwm_list[0].source_type == "file"
        assert cwm_list[0].title == "标题"
        assert cwm_list[0].chunk_index == 0

    def test_add_chunks_multiple_sources_deletes_each(self) -> None:
        vs = _StubVectorStore()
        repo = ChromaKbRepo(vs)  # type: ignore[arg-type]
        chunks = self._make_chunks("PIPL", 2) + self._make_chunks("DSL", 2)
        repo.add_chunks(chunks, [[0.1]] * 4)
        assert set(vs.delete_calls) == {"PIPL", "DSL"}

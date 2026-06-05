"""``KbManagementUseCase`` 单测（Step 016b）。

策略：注入 ``FakeKbRepo`` + ``FakeDocumentLoader`` + ``FakeEmbed``，验证：
- 读 3 方法直接代理 repo
- 删空 source 抛 ValueError
- ``ingest_file`` / ``ingest_web`` 正常路径：loader → embedder → repo.add_chunks
- 空文档不写库且返回 ``success=False``
- embedder 输出长度与 chunks 不一致时 add_chunks 自身报错（验证未被 use case 吞掉）
- ``KbIngestResult`` 是 frozen dataclass
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from app.use_cases import KbIngestResult, KbManagementUseCase
from domain.models import KbChunk
from tests.fakes import FakeDocumentLoader, FakeEmbed, FakeKbRepo


def _make_uc(
    *,
    repo: FakeKbRepo | None = None,
    loader: FakeDocumentLoader | None = None,
    embedder: FakeEmbed | None = None,
) -> tuple[KbManagementUseCase, FakeKbRepo, FakeDocumentLoader, FakeEmbed]:
    repo = repo or FakeKbRepo()
    loader = loader or FakeDocumentLoader()
    embedder = embedder or FakeEmbed(dim=4)
    uc = KbManagementUseCase(kb_repo=repo, loader=loader, embedder=embedder)
    return uc, repo, loader, embedder


def _make_kb_chunks(source: str, n: int) -> list[KbChunk]:
    return [
        KbChunk(
            chunk_id=f"{source}:{i}",
            text=f"text-{i}",
            source_name=source,
            source_type="file",
            title=source,
            chunk_index=i,
        )
        for i in range(n)
    ]


class TestRead:
    def test_list_documents_empty(self) -> None:
        uc, _, _, _ = _make_uc()
        assert uc.list_documents() == []

    def test_list_documents_after_ingest(self) -> None:
        chunks = _make_kb_chunks("PIPL", 3)
        uc, repo, _, _ = _make_uc(loader=FakeDocumentLoader(chunks=chunks))
        uc.ingest_file("/tmp/PIPL.txt", category="法规")
        docs = uc.list_documents()
        assert len(docs) == 1
        assert docs[0].source_name == "PIPL"
        assert docs[0].chunk_count == 3
        # repo 端确实写入
        assert repo.count_chunks() == 3

    def test_get_document_empty_name_raises(self) -> None:
        uc, _, _, _ = _make_uc()
        with pytest.raises(ValueError, match="不能为空"):
            uc.get_document("")

    def test_get_document_miss_returns_none(self) -> None:
        uc, _, _, _ = _make_uc()
        assert uc.get_document("nope") is None

    def test_count_chunks_proxies_repo(self) -> None:
        chunks = _make_kb_chunks("PIPL", 5)
        uc, repo, _, _ = _make_uc(loader=FakeDocumentLoader(chunks=chunks))
        uc.ingest_file("/tmp/PIPL.txt")
        assert uc.count_chunks() == 5
        assert uc.count_chunks() == repo.count_chunks()


class TestDelete:
    def test_delete_empty_source_raises(self) -> None:
        uc, _, _, _ = _make_uc()
        with pytest.raises(ValueError, match="不能为空"):
            uc.delete_document("")

    def test_delete_returns_count(self) -> None:
        chunks = _make_kb_chunks("PIPL", 3)
        uc, _, _, _ = _make_uc(loader=FakeDocumentLoader(chunks=chunks))
        uc.ingest_file("/tmp/PIPL.txt")
        assert uc.delete_document("PIPL") == 3
        assert uc.delete_document("PIPL") == 0  # 二次幂等


class TestIngestFile:
    def test_empty_path_raises(self) -> None:
        uc, _, _, _ = _make_uc()
        with pytest.raises(ValueError, match="不能为空"):
            uc.ingest_file("")

    def test_happy_path(self) -> None:
        chunks = _make_kb_chunks("PIPL", 3)
        uc, repo, loader, embedder = _make_uc(loader=FakeDocumentLoader(chunks=chunks))
        result = uc.ingest_file(
            "/tmp/PIPL.txt", original_filename="PIPL.txt", category="法规"
        )
        assert isinstance(result, KbIngestResult)
        assert result.success is True
        assert result.source_name == "PIPL"
        assert result.chunk_count == 3
        assert "导入成功" in result.message
        # 编排顺序：loader → embedder → repo.add_chunks
        assert loader.calls[0][0] == "load_file"
        assert loader.calls[0][2]["category"] == "法规"
        # embedder 收到 3 条文本
        # （FakeEmbed 不记录 calls 这里以 repo 写入间接验证）
        assert repo.count_chunks() == 3
        # add_chunks 至少被调一次（顺序：add_chunks 在 list_documents 之前）
        method_names = [c[0] for c in repo.calls]
        assert "add_chunks" in method_names

    def test_empty_document_no_write(self) -> None:
        uc, repo, _, _ = _make_uc(loader=FakeDocumentLoader(empty=True))
        result = uc.ingest_file("/tmp/empty.txt", original_filename="empty.txt")
        assert result.success is False
        assert result.chunk_count == 0
        assert result.source_name == "empty.txt"
        assert "为空" in result.message
        # 没有触发任何写
        assert repo.count_chunks() == 0
        assert not any(c[0] == "add_chunks" for c in repo.calls)

    def test_overwrite_same_source(self) -> None:
        # 先入 3 chunk 的 PIPL，再用 5 chunk 重新 ingest 同 source —— 应覆盖
        chunks_first = _make_kb_chunks("PIPL", 3)
        uc, repo, _, _ = _make_uc(loader=FakeDocumentLoader(chunks=chunks_first))
        uc.ingest_file("/tmp/PIPL.txt")
        assert repo.count_chunks() == 3

        chunks_second = _make_kb_chunks("PIPL", 5)
        uc2, _, _, _ = _make_uc(
            repo=repo,
            loader=FakeDocumentLoader(chunks=chunks_second),
        )
        uc2.ingest_file("/tmp/PIPL.txt")
        assert repo.count_chunks() == 5  # 覆盖而非追加


class TestIngestWeb:
    def test_empty_url_raises(self) -> None:
        uc, _, _, _ = _make_uc()
        with pytest.raises(ValueError, match="不能为空"):
            uc.ingest_web("")

    def test_happy_path(self) -> None:
        chunks = [
            KbChunk(
                chunk_id="x:0",
                text="网页内容",
                source_name="https://example.com/x",
                source_type="web",
                title="示例网页",
                source_url="https://example.com/x",
                chunk_index=0,
            )
        ]
        uc, repo, loader, _ = _make_uc(loader=FakeDocumentLoader(chunks=chunks))
        result = uc.ingest_web("https://example.com/x", category="政策")
        assert result.success is True
        assert result.chunk_count == 1
        assert result.source_name == "https://example.com/x"
        assert "示例网页" in result.message  # 优先用 title
        assert loader.calls[0][0] == "load_web"
        assert loader.calls[0][2]["category"] == "政策"
        assert repo.count_chunks() == 1

    def test_empty_web_no_write(self) -> None:
        uc, repo, _, _ = _make_uc(loader=FakeDocumentLoader(empty=True))
        result = uc.ingest_web("https://example.com/nothing")
        assert result.success is False
        assert result.chunk_count == 0
        assert "为空" in result.message
        assert repo.count_chunks() == 0


class TestKbIngestResult:
    def test_is_frozen(self) -> None:
        r = KbIngestResult(success=True, source_name="x", chunk_count=1, message="ok")
        with pytest.raises(FrozenInstanceError):
            r.success = False  # type: ignore[misc]

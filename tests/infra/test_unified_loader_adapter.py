"""``UnifiedLoaderAdapter`` 单测（Step 016b）。

策略：用 stub ``UnifiedLoader`` 注入到 adapter，**不**真实读文件 / 抓网页。
验证：
- 满足 ``DocumentLoaderPort`` 协议
- ``RawDocument → KbChunk`` 字段映射正确（含 source_type unknown 兜底、空 URL → None）
- category 入参覆盖 cwm.category；不传时保持 ""（不是 None）
- 空文档（``build_chunks`` 返回空）→ 空列表，不抛
- 空路径 / 空 url 抛 ValueError
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from domain.models import KbChunk
from domain.ports import DocumentLoaderPort
from infra.kb import UnifiedLoaderAdapter


# ─── 用一个最小的 RawDocument 与 stub loader 替代真 IO ───────────────────
@dataclass
class _StubRawDoc:
    content: str
    source_type: str
    source_name: str
    title: str
    source_url: str | None = None
    extra_metadata: dict = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.extra_metadata is None:
            self.extra_metadata = {}


class _StubLoader:
    """伪 ``UnifiedLoader``：固定返回预置的 RawDocument。"""

    def __init__(
        self,
        *,
        file_doc: _StubRawDoc | None = None,
        web_doc: _StubRawDoc | None = None,
    ) -> None:
        self._file_doc = file_doc
        self._web_doc = web_doc
        self.calls: list[tuple[str, tuple[object, ...]]] = []

    def load_file(self, file_path: str, original_filename: str | None = None):
        self.calls.append(("load_file", (file_path, original_filename)))
        assert self._file_doc is not None, "测试未预置 file_doc"
        return self._file_doc

    def load_web(self, url: str):
        self.calls.append(("load_web", (url,)))
        assert self._web_doc is not None, "测试未预置 web_doc"
        return self._web_doc


class TestProtocolConformance:
    def test_unified_loader_adapter_is_document_loader_port(self) -> None:
        adapter = UnifiedLoaderAdapter(loader=_StubLoader())  # type: ignore[arg-type]
        assert isinstance(adapter, DocumentLoaderPort)


class TestLoadFile:
    def test_empty_path_raises(self) -> None:
        adapter = UnifiedLoaderAdapter(loader=_StubLoader())  # type: ignore[arg-type]
        with pytest.raises(ValueError, match="不能为空"):
            adapter.load_file("")

    def test_happy_path_maps_to_kb_chunks(self) -> None:
        raw = _StubRawDoc(
            content="第一条 ... 第二条 ...",
            source_type="file",
            source_name="PIPL.txt",
            title="个人信息保护法",
        )
        adapter = UnifiedLoaderAdapter(
            loader=_StubLoader(file_doc=raw),  # type: ignore[arg-type]
        )
        chunks = adapter.load_file("/tmp/PIPL.txt", original_filename="PIPL.txt")
        assert len(chunks) >= 1
        assert all(isinstance(c, KbChunk) for c in chunks)
        assert chunks[0].source_name == "PIPL.txt"
        assert chunks[0].source_type == "file"
        assert chunks[0].title == "个人信息保护法"
        # 默认 category 为空字符串（不是 None，因为 KbChunk.category: str = ""）
        assert chunks[0].category == ""
        # 默认 source_url 为 None
        assert chunks[0].source_url is None
        # chunk_index 单调
        for i, c in enumerate(chunks):
            assert c.chunk_index == i

    def test_category_overrides_cwm_category(self) -> None:
        raw = _StubRawDoc(content="第一条", source_type="file", source_name="x", title="t")
        adapter = UnifiedLoaderAdapter(
            loader=_StubLoader(file_doc=raw),  # type: ignore[arg-type]
        )
        chunks = adapter.load_file("/tmp/x.txt", category="法规")
        assert all(c.category == "法规" for c in chunks)

    def test_unknown_source_type_falls_back_to_file(self) -> None:
        raw = _StubRawDoc(
            content="一些内容",
            source_type="unknown",  # 老数据 / 异常类型
            source_name="legacy.bin",
            title="",
        )
        adapter = UnifiedLoaderAdapter(
            loader=_StubLoader(file_doc=raw),  # type: ignore[arg-type]
        )
        chunks = adapter.load_file("/tmp/legacy.bin")
        assert chunks[0].source_type == "file"

    def test_empty_content_returns_empty_list(self) -> None:
        raw = _StubRawDoc(content="", source_type="file", source_name="empty.txt", title="")
        adapter = UnifiedLoaderAdapter(
            loader=_StubLoader(file_doc=raw),  # type: ignore[arg-type]
        )
        chunks = adapter.load_file("/tmp/empty.txt")
        assert chunks == []


class TestLoadWeb:
    def test_empty_url_raises(self) -> None:
        adapter = UnifiedLoaderAdapter(loader=_StubLoader())  # type: ignore[arg-type]
        with pytest.raises(ValueError, match="不能为空"):
            adapter.load_web("")

    def test_happy_path_preserves_web_type_and_url(self) -> None:
        raw = _StubRawDoc(
            content="第一段网页内容",
            source_type="web",
            source_name="https://example.com/a",
            title="示例页面",
            source_url="https://example.com/a",
        )
        adapter = UnifiedLoaderAdapter(
            loader=_StubLoader(web_doc=raw),  # type: ignore[arg-type]
        )
        chunks = adapter.load_web("https://example.com/a")
        assert len(chunks) >= 1
        assert chunks[0].source_type == "web"
        assert chunks[0].source_url == "https://example.com/a"
        assert chunks[0].title == "示例页面"

    def test_empty_source_url_normalized_to_none(self) -> None:
        raw = _StubRawDoc(
            content="一些网页文本",
            source_type="web",
            source_name="https://example.com/b",
            title="无 URL",
            source_url="",
        )
        adapter = UnifiedLoaderAdapter(
            loader=_StubLoader(web_doc=raw),  # type: ignore[arg-type]
        )
        chunks = adapter.load_web("https://example.com/b")
        assert chunks[0].source_url is None

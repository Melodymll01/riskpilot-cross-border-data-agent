"""``DocumentLoaderPort`` 的生产实现：包 ``UnifiedLoader`` + ``build_chunks``。

把 v1 ``ingestion.unified_loader.UnifiedLoader`` 与 v1
``processing.metadata.build_chunks`` 打包成端口契约，对外只暴露 ``list[KbChunk]``，
让 ``KbManagementUseCase`` 无需感知 ingestion / processing 模块的内部结构。

设计：
- 加载 + 切分一体，**不**做 embedding，**不**做写库（职责在另两个 Port）
- 文档无可入库内容（空文件 / 空网页）返回空列表，**不**抛
- ``RawDocument.source_type`` 字符串映射到 ``KbSourceType`` Literal（"unknown" 兜底为 "file"）
- ``source_url`` 空字符串归一为 ``None``，与 ``ChromaKbRepo`` boundary 处的归一一致
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from domain.models import KbChunk, KbSourceType
from ingestion.unified_loader import UnifiedLoader
from processing.metadata import build_chunks

if TYPE_CHECKING:
    from ingestion.unified_loader import RawDocument
    from processing.metadata import ChunkWithMetadata


class UnifiedLoaderAdapter:
    """``DocumentLoaderPort`` ChromaDB+本地文件+网页一体生产实现。"""

    def __init__(self, loader: UnifiedLoader | None = None) -> None:
        # 允许注入便于测试 / 复用单例；不传则按 v1 行为自建一个
        self._loader = loader or UnifiedLoader()

    # ─── 公开方法（Port 契约） ──────────────────────────────────────

    def load_file(
        self,
        file_path: str,
        *,
        original_filename: str | None = None,
        category: str | None = None,
    ) -> list[KbChunk]:
        if not file_path:
            msg = "file_path 不能为空"
            raise ValueError(msg)
        raw = self._loader.load_file(file_path, original_filename)
        return self._raw_to_kb_chunks(raw, category=category)

    def load_web(
        self,
        url: str,
        *,
        category: str | None = None,
    ) -> list[KbChunk]:
        if not url:
            msg = "url 不能为空"
            raise ValueError(msg)
        raw = self._loader.load_web(url)
        return self._raw_to_kb_chunks(raw, category=category)

    # ─── 内部转换 ────────────────────────────────────────────────────

    @staticmethod
    def _normalize_source_type(raw_type: str) -> KbSourceType:
        # 与 ChromaKbRepo._to_kb_document 的归一规则保持一致：unknown -> file
        return "web" if raw_type == "web" else "file"

    @staticmethod
    def _normalize_url(raw_url: str | None) -> str | None:
        return raw_url or None

    def _raw_to_kb_chunks(
        self,
        raw: RawDocument,
        *,
        category: str | None,
    ) -> list[KbChunk]:
        cwm_list: list[ChunkWithMetadata] = build_chunks(raw)
        if not cwm_list:
            return []
        src_type = self._normalize_source_type(raw.source_type)
        url = self._normalize_url(raw.source_url)
        # category 入参优先；否则沿用 build_chunks 设置的 cwm.category（默认 ""）
        resolved_category = category if category else ""
        return [
            KbChunk(
                chunk_id=cwm.chunk_id,
                text=cwm.text,
                source_name=cwm.source_name,
                source_type=src_type,
                title=cwm.title or "",
                source_url=url,
                chunk_index=cwm.chunk_index,
                category=resolved_category or (cwm.category or ""),
            )
            for cwm in cwm_list
        ]

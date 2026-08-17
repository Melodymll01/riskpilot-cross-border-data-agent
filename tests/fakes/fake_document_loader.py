"""``DocumentLoaderPort`` 的 Fake：可预置返回值 + 记录调用。

测试模式：
- 默认：返回一条单 chunk 的简单 KbChunk 列表
- ``empty=True``：模拟"空文档"（loader 返回空 list）
- ``chunks=...``：直接指定要返回的 KbChunk 列表
- ``raise_for_path=...`` / ``raise_for_url=...``：模拟加载失败
"""

from __future__ import annotations

from typing import Any

from domain.models import KbChunk


class FakeDocumentLoader:
    """``DocumentLoaderPort`` in-memory 实现。"""

    def __init__(
        self,
        *,
        chunks: list[KbChunk] | None = None,
        empty: bool = False,
    ) -> None:
        self._preset_chunks = chunks
        self._empty = empty
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []

    # ─── Port 方法 ───────────────────────────────────────────────────

    def load_file(
        self,
        file_path: str,
        *,
        original_filename: str | None = None,
        category: str | None = None,
        owner_id: str | None = None,
    ) -> list[KbChunk]:
        self.calls.append(
            (
                "load_file",
                (file_path,),
                {
                    "original_filename": original_filename,
                    "category": category,
                    "owner_id": owner_id,
                },
            )
        )
        if self._empty:
            return []
        if self._preset_chunks is not None:
            # 按调用参数覆盖 owner_id，以避免预置样本与参数不一致
            return [c.model_copy(update={"owner_id": owner_id}) for c in self._preset_chunks]
        # 默认：返回 2 个简单 chunk
        source = original_filename or file_path or "fake.txt"
        return self._make_default_chunks(
            source_name=source,
            source_type="file",
            title=source,
            url=None,
            category=category or "",
            owner_id=owner_id,
        )

    def load_web(
        self,
        url: str,
        *,
        category: str | None = None,
        owner_id: str | None = None,
    ) -> list[KbChunk]:
        self.calls.append(("load_web", (url,), {"category": category, "owner_id": owner_id}))
        if self._empty:
            return []
        if self._preset_chunks is not None:
            return [c.model_copy(update={"owner_id": owner_id}) for c in self._preset_chunks]
        return self._make_default_chunks(
            source_name=url,
            source_type="web",
            title=f"Fake page @ {url}",
            url=url,
            category=category or "",
            owner_id=owner_id,
        )

    # ─── helpers ─────────────────────────────────────────────────────

    @staticmethod
    def _make_default_chunks(
        *,
        source_name: str,
        source_type: str,
        title: str,
        url: str | None,
        category: str,
        owner_id: str | None = None,
    ) -> list[KbChunk]:
        return [
            KbChunk(
                chunk_id=f"{source_name}:{i}",
                text=f"fake text {i}",
                source_name=source_name,
                source_type="web" if source_type == "web" else "file",
                title=title,
                source_url=url,
                chunk_index=i,
                category=category,
                owner_id=owner_id,
            )
            for i in range(2)
        ]

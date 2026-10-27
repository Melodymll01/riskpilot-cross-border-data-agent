"""Metadata 构建模块：为每个 chunk 附加来源元数据，生成可入库的结构。"""

import logging
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime

from ingestion.unified_loader import RawDocument
from processing.cleaner import TextCleaner
from processing.sections import section_segments
from processing.splitter import TextSplitter

logger = logging.getLogger(__name__)

# Step 025a：公共语料在 ChromaDB metadata 中的物理 owner_id 标记。
# domain 层语义上表达为 ``owner_id is None`` (公共)；infra 存储时必须是非空字符串
# （ChromaDB metadata 不接受 None 值）。二者转换集中在 ChunkWithMetadata.to_metadata_dict
# 与 VectorStore 读侧反映射中。
PUBLIC_OWNER_MARKER = "__public__"


@dataclass
class ChunkWithMetadata:
    """
    携带元数据的文本块，是向量化入库的最小单位。

    Attributes:
        chunk_id: 全局唯一标识
        text: 文本内容
        source_type: 来源类型 (file / web)
        source_name: 来源名称
        title: 文档标题
        source_url: 来源 URL（可选）
        chunk_index: 在原文档中的序号
        category: 文档分类（法规/政策/指南/标准 等），供多 Agent 按类别检索
        owner_id: Step 025a 多租户隔离字段。语义上 None=公共，非空=该 owner 私人。
            存入 ChromaDB 时 None 会被物化为 ``__public__``（ChromaDB 不接受 None）。
    """

    chunk_id: str
    text: str
    source_type: str
    source_name: str
    title: str
    embedding_text: str | None = None
    source_url: str | None = None
    chunk_index: int = 0
    category: str = ""  # 文档分类标签
    owner_id: str | None = None  # Step 025a: None=公共，非空=私人
    source_metadata: dict[str, str] = field(default_factory=dict)

    def to_metadata_dict(self) -> dict:
        """转换为可存入向量库的 metadata 字典。

        ``owner_id`` 为 None 时会被物化为 ``PUBLIC_OWNER_MARKER``，以便 ChromaDB
        ``where`` 过滤表达「仅公共」、「仅某 owner」、「公共∪某 owner」三种 scope。
        """
        meta = {
            "chunk_id": self.chunk_id,
            "source_type": self.source_type,
            "source_name": self.source_name,
            "title": self.title,
            "chunk_index": self.chunk_index,
            "imported_at": datetime.now(UTC).isoformat(),
            "owner_id": self.owner_id if self.owner_id else PUBLIC_OWNER_MARKER,
        }
        if self.source_url:
            meta["source_url"] = self.source_url
        if self.category:
            meta["category"] = self.category
        for key in (
            "source_version",
            "source_sha256",
            "legal_document_id",
            "jurisdiction",
            "clause_id",
            "corpus_id",
            "extraction_method",
            "extracted_text_chars",
            "content_link_density",
            "boilerplate_lines_removed",
            "pdf_page_count",
            "pdf_table_count",
            "pdf_table_pages",
            "pdf_visual_pages",
            "pdf_removed_edge_lines",
            "page_number",
            "content_kind",
            "page_table_count",
            "page_extraction_method",
            "visual_content_detected",
            "section_id",
            "parent_section_id",
            "section_path",
            "section_title",
            "parent_section_title",
            "chunk_role",
            "chunk_index_in_section",
            "index_schema_version",
        ):
            if self.source_metadata.get(key):
                meta[key] = self.source_metadata[key]
        return meta


def build_chunks(
    doc: RawDocument,
    cleaner: TextCleaner | None = None,
    splitter: TextSplitter | None = None,
) -> list[ChunkWithMetadata]:
    """
    完整的文档处理链路：清洗 → 切分 → 构建元数据。

    这是统一的处理入口，无论文档来源是文件还是网页，
    都经过同一条链路处理为 ChunkWithMetadata 列表。

    Args:
        doc: 统一的原始文档对象
        cleaner: 文本清洗器（可选，默认创建新实例）
        splitter: 文本切分器（可选，默认创建新实例）

    Returns:
        ChunkWithMetadata 列表
    """
    if cleaner is None:
        cleaner = TextCleaner()
    if splitter is None:
        splitter = TextSplitter()

    raw_segments = section_segments(doc)
    chunks: list[ChunkWithMetadata] = []
    section_chunk_counts: defaultdict[str, int] = defaultdict(int)
    cleaned_char_count = 0
    for segment in raw_segments:
        cleaned_text = cleaner.clean(segment.content)
        cleaned_char_count += len(cleaned_text)
        base_source_metadata = {
            str(key): str(value)
            for key, value in {**doc.extra_metadata, **segment.extra_metadata}.items()
        }
        for text in splitter.split(cleaned_text):
            section_id = base_source_metadata["section_id"]
            chunk_index_in_section = section_chunk_counts[section_id]
            section_chunk_counts[section_id] += 1
            source_metadata = {
                **base_source_metadata,
                "chunk_index_in_section": str(chunk_index_in_section),
            }
            chunks.append(
                ChunkWithMetadata(
                    chunk_id=str(uuid.uuid4()),
                    text=text,
                    embedding_text=_embedding_text(
                        section_path=source_metadata["section_path"],
                        text=text,
                        max_chars=splitter.chunk_size,
                    ),
                    source_type=doc.source_type,
                    source_name=doc.source_name,
                    title=doc.title,
                    source_url=doc.source_url,
                    chunk_index=len(chunks),
                    source_metadata=source_metadata,
                )
            )

    logger.info(
        f"文档 [{doc.title}] 处理完成: "
        f"原文 {len(doc.content)} 字 → 清洗后 {cleaned_char_count} 字 → {len(chunks)} 个 chunk"
    )
    return chunks


def _embedding_text(*, section_path: str, text: str, max_chars: int) -> str:
    prefix = section_path.strip()
    if not prefix:
        return text[:max_chars]
    if len(prefix) >= max_chars:
        return prefix[:max_chars]
    remaining = max_chars - len(prefix) - 1
    return prefix + "\n" + text[: max(0, remaining)]

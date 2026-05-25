"""Metadata 构建模块：为每个 chunk 附加来源元数据，生成可入库的结构。"""

import uuid
import logging
from dataclasses import dataclass, field
from typing import List, Optional
from datetime import datetime

from ingestion.unified_loader import RawDocument
from processing.cleaner import TextCleaner
from processing.splitter import TextSplitter

logger = logging.getLogger(__name__)


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
    """
    chunk_id: str
    text: str
    source_type: str
    source_name: str
    title: str
    source_url: Optional[str] = None
    chunk_index: int = 0
    category: str = ""  # 文档分类标签

    def to_metadata_dict(self) -> dict:
        """转换为可存入向量库的 metadata 字典。"""
        meta = {
            "chunk_id": self.chunk_id,
            "source_type": self.source_type,
            "source_name": self.source_name,
            "title": self.title,
            "chunk_index": self.chunk_index,
            "imported_at": datetime.utcnow().isoformat() + "Z",
        }
        if self.source_url:
            meta["source_url"] = self.source_url
        if self.category:
            meta["category"] = self.category
        return meta


def build_chunks(
    doc: RawDocument,
    cleaner: Optional[TextCleaner] = None,
    splitter: Optional[TextSplitter] = None,
) -> List[ChunkWithMetadata]:
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

    # 1. 清洗
    cleaned_text = cleaner.clean(doc.content)

    # 2. 切分
    chunk_texts = splitter.split(cleaned_text)

    # 3. 构建带元数据的 chunk
    chunks: List[ChunkWithMetadata] = []
    for idx, text in enumerate(chunk_texts):
        chunk = ChunkWithMetadata(
            chunk_id=str(uuid.uuid4()),
            text=text,
            source_type=doc.source_type,
            source_name=doc.source_name,
            title=doc.title,
            source_url=doc.source_url,
            chunk_index=idx,
        )
        chunks.append(chunk)

    logger.info(
        f"文档 [{doc.title}] 处理完成: "
        f"原文 {len(doc.content)} 字 → 清洗后 {len(cleaned_text)} 字 → {len(chunks)} 个 chunk"
    )
    return chunks

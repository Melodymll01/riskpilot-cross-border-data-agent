"""向量数据库模块：封装 ChromaDB 的存储与检索操作。"""

import logging
from typing import List, Dict, Any, Optional

import chromadb

from config import settings
from processing.metadata import ChunkWithMetadata

logger = logging.getLogger(__name__)

# ChromaDB collection 名称
COLLECTION_NAME = "rag_knowledge_base"


class VectorStore:
    """封装 ChromaDB 的存储、检索和管理操作。"""

    def __init__(self):
        self.client = chromadb.PersistentClient(path=settings.chroma_persist_dir)
        self.collection = self.client.get_or_create_collection(
            name=COLLECTION_NAME,
            metadata={"hnsw:space": "cosine"},  # 使用余弦相似度
        )
        # 订阅者：数据变动时通知（如 BM25Index.mark_dirty）
        self._change_listeners: List[Any] = []
        logger.info(
            f"ChromaDB 已连接，集合 [{COLLECTION_NAME}] 现有 {self.collection.count()} 条记录"
        )

    def register_change_listener(self, callback) -> None:
        """注册数据变动回调（参数无入参，仅用作失效通知）。"""
        self._change_listeners.append(callback)

    def _notify_changed(self) -> None:
        for cb in self._change_listeners:
            try:
                cb()
            except Exception as e:
                logger.warning(f"change_listener 回调异常: {e}")

    def add_chunks(
        self,
        chunks: List[ChunkWithMetadata],
        embeddings: List[List[float]],
    ) -> None:
        """
        将 chunk 及其向量批量存入向量库。

        Args:
            chunks: 带元数据的文本块列表
            embeddings: 对应的向量列表
        """
        if not chunks:
            return

        ids = [c.chunk_id for c in chunks]
        documents = [c.text for c in chunks]
        metadatas = [c.to_metadata_dict() for c in chunks]

        self.collection.add(
            ids=ids,
            embeddings=embeddings,
            documents=documents,
            metadatas=metadatas,
        )
        self._notify_changed()

        logger.info(f"已入库 {len(chunks)} 个 chunk，总记录数: {self.collection.count()}")

    def query(
        self,
        query_embedding: List[float],
        top_k: int = settings.top_k,
        where_filter: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        """
        根据查询向量检索最相似的 chunk。

        Args:
            query_embedding: 查询文本的向量
            top_k: 返回结果数
            where_filter: 可选的元数据过滤条件（如 {"category": "法规"}）

        Returns:
            检索结果列表，每项包含 id, text, metadata, distance
        """
        query_kwargs = {
            "query_embeddings": [query_embedding],
            "n_results": top_k,
            "include": ["documents", "metadatas", "distances"],
        }
        if where_filter:
            query_kwargs["where"] = where_filter
            

        #**这是字典解包，传递给 collection.query() 方法的参数。根据 where_filter 是否存在，query_kwargs 会包含不同的键值对。**
        results = self.collection.query(**query_kwargs)

        # 整理为更易用的格式，因为我们通常是单条查询，所以取 results["ids"][0] 等。注意检查结果是否存在。
        items = []
        if results and results["ids"] and results["ids"][0]:
            for i in range(len(results["ids"][0])):
                items.append({
                    "id": results["ids"][0][i],
                    "text": results["documents"][0][i],
                    "metadata": results["metadatas"][0][i],
                    "distance": results["distances"][0][i],
                })

        logger.info(f"检索完成，返回 {len(items)} 条结果")
        return items

    def keyword_search(self, keywords: List[str], top_k: int = settings.top_k) -> List[Dict[str, Any]]:
        """
        基于关键词的全文检索（利用 ChromaDB 的 where_document 能力）。

        用于混合检索：弥补向量检索对精确术语匹配不足的问题。
        法规文档中 "第X条"、"安全评估" 等精确短语需要关键词匹配。

        Args:
            keywords: 关键词列表
            top_k: 每个关键词返回的最大结果数

        Returns:
            检索结果列表（不含 distance，标记 match_type="keyword"）
        """
        if not keywords:
            return []
        # 用 id 去重，因为不同关键词可能匹配到同一条记录
        all_items = {}  

        for kw in keywords:
            if not kw.strip():
                continue
            try:
                results = self.collection.get(
                    where_document={"$contains": kw},
                    include=["documents", "metadatas"],
                    limit=top_k,
                )

                if results and results["ids"]:
                    for i in range(len(results["ids"])):
                        doc_id = results["ids"][i]
                        if doc_id not in all_items:
                            all_items[doc_id] = {
                                "id": doc_id,
                                "text": results["documents"][i],
                                "metadata": results["metadatas"][i],
                                "distance": 0.3,  # 关键词精确匹配给一个较优的默认距离
                                "match_type": "keyword",
                            }
            except Exception as e:
                logger.debug(f"关键词检索 '{kw}' 失败: {e}")
                continue

        items = list(all_items.values())
        logger.info(f"关键词检索完成，关键词 {keywords}，返回 {len(items)} 条结果")
        return items

    def get_all_sources(self) -> List[Dict[str, Any]]:
        """
        获取当前知识库中所有唯一的知识来源。

        Returns:
            来源列表，包含 source_type, source_name, title, source_url, chunk_count
        """
        # 获取所有 metadatas
        all_data = self.collection.get(include=["metadatas"])

        if not all_data or not all_data["metadatas"]:
            return []

        # 按 source_name 聚合
        source_map: Dict[str, Dict[str, Any]] = {}
        for meta in all_data["metadatas"]:
            name = meta.get("source_name", "unknown")
            if name not in source_map:
                source_map[name] = {
                    "source_type": meta.get("source_type", "unknown"),
                    "source_name": name,
                    "title": meta.get("title", ""),
                    "source_url": meta.get("source_url", ""),
                    "chunk_count": 0,
                }
            source_map[name]["chunk_count"] += 1

        return list(source_map.values())

    def delete_by_source(self, source_name: str) -> int:
        """
        按来源名称删除所有相关 chunk。

        Args:
            source_name: 来源名称

        Returns:
            删除的记录数
        """
        # 先查出所有匹配的 ids
        results = self.collection.get(
            where={"source_name": source_name},
            include=[],
        )

        if not results or not results["ids"]:
            return 0

        ids_to_delete = results["ids"]
        self.collection.delete(ids=ids_to_delete)
        self._notify_changed()

        logger.info(f"已删除来源 [{source_name}] 的 {len(ids_to_delete)} 条记录")
        return len(ids_to_delete)

    def get_total_count(self) -> int:
        """返回知识库中的总 chunk 数。"""
        return self.collection.count()

    def get_neighbor_chunks(
        self,
        source_name: str,
        chunk_index: int,
        window: int = 1,
    ) -> List[str]:
        """
        获取同一来源中相邻 chunk 的文本，用于上下文窗口扩展。

        Args:
            source_name: 来源名称
            chunk_index: 当前 chunk 的序号
            window: 前后各取几个 chunk

        Returns:
            按顺序排列的 chunk 文本列表（包含当前 chunk）
        """
        # 计算需要获取的 index 范围
        target_indices = list(range(
            max(0, chunk_index - window),
            chunk_index + window + 1,
        ))

        try:
            results = self.collection.get(
                where={
                    "$and": [
                        {"source_name": source_name},
                        {"chunk_index": {"$gte": target_indices[0]}},
                        {"chunk_index": {"$lte": target_indices[-1]}},
                    ]
                },
                include=["documents", "metadatas"],
            )
        except Exception as e:
            logger.debug(f"获取相邻 chunk 失败: {e}")
            return []

        if not results or not results["ids"]:
            return []

        # 按 chunk_index 排序
        pairs = []
        for i in range(len(results["ids"])):
            idx = results["metadatas"][i].get("chunk_index", 0)
            pairs.append((idx, results["documents"][i]))

        pairs.sort(key=lambda x: x[0])
        return [text for _, text in pairs]

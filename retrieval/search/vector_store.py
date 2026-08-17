"""向量数据库模块：封装 ChromaDB 的存储与检索操作。"""

import logging
from collections.abc import Iterable
from typing import Any, Optional

import chromadb

from config import settings
from processing.metadata import PUBLIC_OWNER_MARKER, ChunkWithMetadata

logger = logging.getLogger(__name__)

# ChromaDB collection 名称
COLLECTION_NAME = "rag_knowledge_base"


def _build_owner_clause(owners: Iterable[str | None] | None) -> dict[str, Any] | None:
    """根据 domain 层「可见 owner 集」构造 ChromaDB ``where`` 子句。

    语义：
    - ``owners`` 为 None 表示不过滤（admin 全库视角）。
    - ``owners`` 为集合时，domain 中的 ``None`` 被物化为 ``PUBLIC_OWNER_MARKER``；
      只有一个元素时返回 ``{"owner_id": <v>}``，否则返回 ``{"owner_id": {"$in": […]}}``。
    - 空集合返回一个不可能命中的条件（防止误传导致全库可见）。
    """
    if owners is None:
        return None
    materialized = [PUBLIC_OWNER_MARKER if o is None else o for o in owners]
    if not materialized:
        # 空集合：造一个必不命中的条件（避免意外全量可见）
        return {"owner_id": "__never_match__"}
    if len(materialized) == 1:
        return {"owner_id": materialized[0]}
    return {"owner_id": {"$in": materialized}}


def _and_clause(*clauses: dict[str, Any] | None) -> dict[str, Any] | None:
    """组合多个 where 子句为一个 AND 表达式；None 跳过。"""
    real = [c for c in clauses if c]
    if not real:
        return None
    if len(real) == 1:
        return real[0]
    return {"$and": real}


class _Unset:
    """用于区分「未传 owner_id」和「显式传 None=public」的哨兵类型。"""

    _instance: Optional["_Unset"] = None

    def __new__(cls) -> "_Unset":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __repr__(self) -> str:
        return "<UNSET>"


_UNSET: Any = _Unset()


class VectorStore:
    """封装 ChromaDB 的存储、检索和管理操作。"""

    def __init__(self):
        self.client = chromadb.PersistentClient(path=settings.chroma_persist_dir)
        self.collection = self.client.get_or_create_collection(
            name=COLLECTION_NAME,
            metadata={"hnsw:space": "cosine"},  # 使用余弦相似度
        )
        # 订阅者：数据变动时通知（如 BM25Index.mark_dirty）
        self._change_listeners: list[Any] = []
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
        chunks: list[ChunkWithMetadata],
        embeddings: list[list[float]],
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
        query_embedding: list[float],
        top_k: int = settings.top_k,
        where_filter: dict[str, Any] | None = None,
        owners: Iterable[str | None] | None = None,
    ) -> list[dict[str, Any]]:
        """
        根据查询向量检索最相似的 chunk。

        Args:
            query_embedding: 查询文本的向量
            top_k: 返回结果数
            where_filter: 可选的元数据过滤条件（如 {"category": "法规"}）
            owners: Step 025a 可见 owner 集合。None 表示不过滤（admin 全库）；domain
                None 会自动映射为 PUBLIC_OWNER_MARKER。与 ``where_filter`` 同时传入时两者 AND。

        Returns:
            检索结果列表，每项包含 id, text, metadata, distance
        """
        owner_clause = _build_owner_clause(owners)
        merged_where = _and_clause(where_filter, owner_clause)

        query_kwargs = {
            "query_embeddings": [query_embedding],
            "n_results": top_k,
            "include": ["documents", "metadatas", "distances"],
        }
        if merged_where:
            query_kwargs["where"] = merged_where

        # **这是字典解包，传递给 collection.query() 方法的参数。根据 where_filter 是否存在，query_kwargs 会包含不同的键值对。**
        results = self.collection.query(**query_kwargs)

        # 整理为更易用的格式，因为我们通常是单条查询，所以取 results["ids"][0] 等。注意检查结果是否存在。
        items = []
        if results and results["ids"] and results["ids"][0]:
            for i in range(len(results["ids"][0])):
                items.append(
                    {
                        "id": results["ids"][0][i],
                        "text": results["documents"][0][i],
                        "metadata": results["metadatas"][0][i],
                        "distance": results["distances"][0][i],
                    }
                )

        logger.info(f"检索完成，返回 {len(items)} 条结果")
        return items

    def keyword_search(
        self,
        keywords: list[str],
        top_k: int = settings.top_k,
        owners: Iterable[str | None] | None = None,
    ) -> list[dict[str, Any]]:
        """
        基于关键词的全文检索（利用 ChromaDB 的 where_document 能力）。

        用于混合检索：弥补向量检索对精确术语匹配不足的问题。
        法规文档中 "第X条"、"安全评估" 等精确短语需要关键词匹配。

        Args:
            keywords: 关键词列表
            top_k: 每个关键词返回的最大结果数
            owners: Step 025a 可见 owner 集合（语义同 query）

        Returns:
            检索结果列表（不含 distance，标记 match_type="keyword"）
        """
        if not keywords:
            return []
        owner_clause = _build_owner_clause(owners)
        # 用 id 去重，因为不同关键词可能匹配到同一条记录
        all_items = {}

        for kw in keywords:
            if not kw.strip():
                continue
            try:
                kwargs: dict[str, Any] = {
                    "where_document": {"$contains": kw},
                    "include": ["documents", "metadatas"],
                    "limit": top_k,
                }
                if owner_clause:
                    kwargs["where"] = owner_clause
                results = self.collection.get(**kwargs)

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

    def get_all_sources(
        self,
        owners: Iterable[str | None] | None = None,
    ) -> list[dict[str, Any]]:
        """
        获取当前知识库中所有唯一的知识来源。

        Args:
            owners: Step 025a 可见 owner 集合。None=全库视角（admin），集合=仅该集可见。

        Returns:
            来源列表，包含 source_type, source_name, title, source_url, chunk_count, owner_id
        """
        owner_clause = _build_owner_clause(owners)
        get_kwargs: dict[str, Any] = {"include": ["metadatas"]}
        if owner_clause:
            get_kwargs["where"] = owner_clause
        all_data = self.collection.get(**get_kwargs)

        if not all_data or not all_data["metadatas"]:
            return []

        # 按 (source_name, owner_id) 聚合：同名不同 owner 应拆开
        source_map: dict[tuple, dict[str, Any]] = {}
        for meta in all_data["metadatas"]:
            name = meta.get("source_name", "unknown")
            owner_raw = meta.get("owner_id", PUBLIC_OWNER_MARKER)
            owner_domain = None if owner_raw == PUBLIC_OWNER_MARKER else owner_raw
            key = (name, owner_raw)
            if key not in source_map:
                source_map[key] = {
                    "source_type": meta.get("source_type", "unknown"),
                    "source_name": name,
                    "title": meta.get("title", ""),
                    "source_url": meta.get("source_url", ""),
                    "chunk_count": 0,
                    "owner_id": owner_domain,
                    "category": meta.get("category", ""),
                }
            source_map[key]["chunk_count"] += 1

        return list(source_map.values())

    def delete_by_source(
        self,
        source_name: str,
        owner_id: Any = _UNSET,
    ) -> int:
        """
        按来源名称删除所有相关 chunk。

        Args:
            source_name: 来源名称
            owner_id: Step 025a。赋值为:
                - 未传 (sentinel) -> 不加 owner 过滤（admin 全能删）
                - None       -> 仅删 owner_id=PUBLIC 的 chunk
                - 某个字符串  -> 仅删该 owner 的 chunk

        Returns:
            删除的记录数
        """
        clauses: list[dict[str, Any]] = [{"source_name": source_name}]
        if owner_id is not _UNSET:
            owner_marker = PUBLIC_OWNER_MARKER if owner_id is None else owner_id
            clauses.append({"owner_id": owner_marker})
        merged_where = clauses[0] if len(clauses) == 1 else {"$and": clauses}

        results = self.collection.get(
            where=merged_where,
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

    def migrate_owner_id_marker(self) -> int:
        """Step 025a 启动迁移：扫描所有 metadata 缺失 ``owner_id`` 字段的 chunk，
        把它们标记为 ``PUBLIC_OWNER_MARKER``（视为 admin 公共库）。

        幂等：已带 owner_id 的 chunk 不动；下次启动扫描结果为空，零开销。

        Returns:
            本次迁移的 chunk 数（0 表示无需迁移）
        """
        all_data = self.collection.get(include=["metadatas"])
        if not all_data or not all_data["metadatas"]:
            return 0

        missing_ids: list[str] = []
        new_metas: list[dict[str, Any]] = []
        for cid, meta in zip(all_data["ids"], all_data["metadatas"], strict=True):
            if "owner_id" in meta:
                continue
            missing_ids.append(cid)
            patched = dict(meta)
            patched["owner_id"] = PUBLIC_OWNER_MARKER
            new_metas.append(patched)

        if not missing_ids:
            return 0

        # ChromaDB update 支持按 id 批量更新 metadata
        self.collection.update(ids=missing_ids, metadatas=new_metas)
        self._notify_changed()
        logger.info(
            f"[Step 025a] owner_id 启动迁移：{len(missing_ids)} 个 chunk 已标记为 "
            f"{PUBLIC_OWNER_MARKER}"
        )
        return len(missing_ids)

    def get_neighbor_chunks(
        self,
        source_name: str,
        chunk_index: int,
        window: int = 1,
        owners: Iterable[str | None] | None = None,
    ) -> list[str]:
        """
        获取同一来源中相邻 chunk 的文本，用于上下文窗口扩展。

        Args:
            source_name: 来源名称
            chunk_index: 当前 chunk 的序号
            window: 前后各取几个 chunk
            owners: Step 025a 可见 owner 集合（防止跨 owner 上下文窗口泄漏）

        Returns:
            按顺序排列的 chunk 文本列表（包含当前 chunk）
        """
        # 计算需要获取的 index 范围
        target_indices = list(
            range(
                max(0, chunk_index - window),
                chunk_index + window + 1,
            )
        )

        clauses: list[dict[str, Any]] = [
            {"source_name": source_name},
            {"chunk_index": {"$gte": target_indices[0]}},
            {"chunk_index": {"$lte": target_indices[-1]}},
        ]
        owner_clause = _build_owner_clause(owners)
        if owner_clause:
            clauses.append(owner_clause)

        try:
            results = self.collection.get(
                where={"$and": clauses},
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

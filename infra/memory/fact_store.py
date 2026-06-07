"""``FactStorePort`` 的 Chroma 实现：L4 语义事实存储（Step 030c）。

挂在独立 collection ``memory_facts`` 上（与 KB 的 ``rag_knowledge_base`` 隔离），
余弦相似度。向量由调用方用 ``EmbedPort`` 算好传入，本类只管存取 + owner 隔离。

设计要点：
- owner 隔离：所有读写都带 ``where={"owner_id": ...}``，与 KB 同机制（合规底线）。
- Chroma metadata 只接受 str/int/float/bool：``tags`` 序列化为 JSON 串；
  ``superseded_by`` 用空串表示 None。
- 余弦空间下相似度 = ``1 - distance``，统一裁剪到 [0, 1]。
- chromadb 懒导入：固化禁用时不触发底层加载。
"""

from __future__ import annotations

import json
import logging
from typing import Any

from config import settings
from domain.models import Fact

logger = logging.getLogger(__name__)

FACTS_COLLECTION = "memory_facts"


class ChromaFactStore:
    """``FactStorePort`` 的 Chroma 实现。"""

    def __init__(self, collection: Any | None = None) -> None:
        if collection is None:
            import chromadb

            client = chromadb.PersistentClient(path=settings.chroma_persist_dir)
            collection = client.get_or_create_collection(
                name=FACTS_COLLECTION,
                metadata={"hnsw:space": "cosine"},
            )
        self._col = collection

    # ── 写 ────────────────────────────────────────────────────────────────

    def add(self, fact: Fact, embedding: list[float]) -> None:
        self._col.upsert(
            ids=[fact.fact_id],
            embeddings=[embedding],
            documents=[fact.text],
            metadatas=[self._to_meta(fact)],
        )

    def mark_superseded(
        self, owner_id: str, fact_id: str, superseded_by: str
    ) -> None:
        fact = self.get(owner_id, fact_id)
        if fact is None:
            return
        self._col.update(
            ids=[fact_id],
            metadatas=[{**self._to_meta(fact), "superseded_by": superseded_by}],
        )

    def delete(self, owner_id: str, fact_id: str) -> None:
        # 先确认归属，避免跨 owner 误删。
        if self.get(owner_id, fact_id) is None:
            return
        self._col.delete(ids=[fact_id])

    # ── 读 ────────────────────────────────────────────────────────────────

    def query(
        self, owner_id: str, embedding: list[float], k: int
    ) -> list[tuple[Fact, float]]:
        if k <= 0:
            return []
        res = self._col.query(
            query_embeddings=[embedding],
            n_results=k,
            where={"owner_id": owner_id},
            include=["documents", "metadatas", "distances"],
        )
        out: list[tuple[Fact, float]] = []
        if res and res.get("ids") and res["ids"][0]:
            for i in range(len(res["ids"][0])):
                fact = self._from_record(
                    res["ids"][0][i],
                    res["documents"][0][i],
                    res["metadatas"][0][i],
                )
                sim = self._distance_to_sim(res["distances"][0][i])
                out.append((fact, sim))
        return out

    def get(self, owner_id: str, fact_id: str) -> Fact | None:
        res = self._col.get(
            ids=[fact_id],
            where={"owner_id": owner_id},
            include=["documents", "metadatas"],
        )
        if not res or not res.get("ids"):
            return None
        return self._from_record(
            res["ids"][0], res["documents"][0], res["metadatas"][0]
        )

    def list_owner(self, owner_id: str) -> list[Fact]:
        res = self._col.get(
            where={"owner_id": owner_id},
            include=["documents", "metadatas"],
        )
        if not res or not res.get("ids"):
            return []
        return [
            self._from_record(res["ids"][i], res["documents"][i], res["metadatas"][i])
            for i in range(len(res["ids"]))
        ]

    def count(self, owner_id: str) -> int:
        res = self._col.get(where={"owner_id": owner_id}, include=[])
        return len(res["ids"]) if res and res.get("ids") else 0

    # ── 序列化 ──────────────────────────────────────────────────────────────

    @staticmethod
    def _to_meta(fact: Fact) -> dict[str, Any]:
        return {
            "owner_id": fact.owner_id,
            "tags": json.dumps(fact.tags, ensure_ascii=False),
            "confidence": fact.confidence,
            "salience": fact.salience,
            "created_at": fact.created_at,
            "last_used_at": fact.last_used_at,
            "superseded_by": fact.superseded_by or "",
            "source_episode": fact.source_episode,
        }

    @staticmethod
    def _from_record(fact_id: str, document: str, meta: dict[str, Any]) -> Fact:
        raw_tags = meta.get("tags") or "[]"
        try:
            tags = json.loads(raw_tags)
        except (ValueError, TypeError):
            tags = []
        superseded = meta.get("superseded_by") or None
        return Fact(
            fact_id=fact_id,
            owner_id=meta["owner_id"],
            text=document,
            tags=tags,
            confidence=meta.get("confidence", 0.5),
            salience=meta.get("salience", 0.5),
            created_at=meta.get("created_at", 0.0),
            last_used_at=meta.get("last_used_at", 0.0),
            superseded_by=superseded,
            source_episode=meta.get("source_episode", ""),
        )

    @staticmethod
    def _distance_to_sim(distance: float) -> float:
        sim = 1.0 - float(distance)
        return max(0.0, min(1.0, sim))

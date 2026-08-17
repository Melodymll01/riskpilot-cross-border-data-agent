"""``FactStorePort`` / ``ConsolidationStatePort`` 内存 Fake（S-030c）。

``FakeFactStore`` 用真实余弦相似度做近邻检索，owner 隔离，
让固化 worker 的去重 / 冲突 / 容量逻辑可在无 Chroma 环境下确定性测试。
"""

from __future__ import annotations

import math

from domain.models import ConsolidationState, Fact


class FakeFactStore:
    """``FactStorePort`` 的内存实现（带余弦近邻）。"""

    def __init__(self) -> None:
        # fact_id -> (Fact, embedding)
        self._store: dict[str, tuple[Fact, list[float]]] = {}

    def add(self, fact: Fact, embedding: list[float]) -> None:
        self._store[fact.fact_id] = (fact, list(embedding))

    def query(self, owner_id: str, embedding: list[float], k: int) -> list[tuple[Fact, float]]:
        if k <= 0:
            return []
        scored: list[tuple[Fact, float]] = []
        for fact, emb in self._store.values():
            if fact.owner_id != owner_id:
                continue
            scored.append((fact, self._cosine(embedding, emb)))
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:k]

    def get(self, owner_id: str, fact_id: str) -> Fact | None:
        item = self._store.get(fact_id)
        if item is None or item[0].owner_id != owner_id:
            return None
        return item[0]

    def mark_superseded(self, owner_id: str, fact_id: str, superseded_by: str) -> None:
        item = self._store.get(fact_id)
        if item is None or item[0].owner_id != owner_id:
            return
        fact, emb = item
        self._store[fact_id] = (
            fact.model_copy(update={"superseded_by": superseded_by}),
            emb,
        )

    def list_owner(self, owner_id: str) -> list[Fact]:
        return [f for f, _ in self._store.values() if f.owner_id == owner_id]

    def delete(self, owner_id: str, fact_id: str) -> None:
        item = self._store.get(fact_id)
        if item is not None and item[0].owner_id == owner_id:
            del self._store[fact_id]

    def delete_owner(self, owner_id: str) -> int:
        ids = [fid for fid, (f, _) in self._store.items() if f.owner_id == owner_id]
        for fid in ids:
            del self._store[fid]
        return len(ids)

    def count(self, owner_id: str) -> int:
        return sum(1 for f, _ in self._store.values() if f.owner_id == owner_id)

    @staticmethod
    def _cosine(a: list[float], b: list[float]) -> float:
        dot = sum(x * y for x, y in zip(a, b, strict=False))
        na = math.sqrt(sum(x * x for x in a))
        nb = math.sqrt(sum(y * y for y in b))
        if na == 0.0 or nb == 0.0:
            return 0.0
        return dot / (na * nb)


class FakeConsolidationStateStore:
    """``ConsolidationStatePort`` 的内存实现。"""

    def __init__(self) -> None:
        self._states: dict[str, ConsolidationState] = {}

    def get(self, task_id: str, owner_id: str) -> ConsolidationState | None:
        state = self._states.get(task_id)
        if state is None or state.owner_id != owner_id:
            return None
        return state

    def upsert(self, state: ConsolidationState) -> None:
        self._states[state.task_id] = state

    def delete_owner(self, owner_id: str) -> int:
        ids = [tid for tid, s in self._states.items() if s.owner_id == owner_id]
        for tid in ids:
            del self._states[tid]
        return len(ids)

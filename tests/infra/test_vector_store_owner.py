"""``VectorStore`` 多租户 owners 过滤 + 启动迁移幂等性（Step 025a）。

策略：
- 用 ``tmp_path`` 隔离每个用例的 ChromaDB 持久化目录
- 真实 ``VectorStore.add_chunks`` + ``query`` / ``keyword_search`` / ``get_all_sources``
  覆盖 owners 过滤；预置含/不含 ``owner_id`` 的 metadata 验证启动迁移幂等
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from config import settings as global_settings
from processing.metadata import PUBLIC_OWNER_MARKER, ChunkWithMetadata
from retrieval.search.vector_store import (
    COLLECTION_NAME,
    VectorStore,
    _build_owner_clause,
)

# ── pure helpers ────────────────────────────────────────────────────


class TestBuildOwnerClause:
    def test_none_means_no_filter(self) -> None:
        assert _build_owner_clause(None) is None

    def test_single_public(self) -> None:
        assert _build_owner_clause([None]) == {"owner_id": PUBLIC_OWNER_MARKER}

    def test_single_user(self) -> None:
        assert _build_owner_clause(["github:alice"]) == {"owner_id": "github:alice"}

    def test_multiple_uses_in(self) -> None:
        clause = _build_owner_clause([None, "github:alice"])
        assert clause is not None
        assert "owner_id" in clause
        assert clause["owner_id"] == {"$in": [PUBLIC_OWNER_MARKER, "github:alice"]}

    def test_empty_list_never_matches(self) -> None:
        # 空集合必须返回不可命中的 where（防误返全量）
        assert _build_owner_clause([]) == {"owner_id": "__never_match__"}


# ── 真实 ChromaDB fixture（每个测试独立目录）─────────────────────────


@pytest.fixture
def isolated_vs(tmp_path, monkeypatch) -> Iterator[VectorStore]:
    """每个用例独立的 chroma 持久目录 + 独立 collection。"""
    monkeypatch.setattr(global_settings, "chroma_persist_dir", str(tmp_path))
    vs = VectorStore()
    yield vs
    # 清理（删 collection 把全量 chunk 抹掉）
    try:
        vs.client.delete_collection(COLLECTION_NAME)
    except Exception:
        pass


def _make_cwm(
    *,
    chunk_id: str,
    text: str,
    source_name: str,
    owner_id: str | None,
    chunk_index: int = 0,
) -> ChunkWithMetadata:
    return ChunkWithMetadata(
        chunk_id=chunk_id,
        text=text,
        source_type="file",
        source_name=source_name,
        title=source_name,
        source_url=None,
        chunk_index=chunk_index,
        category="",
        owner_id=owner_id,
    )


def _seed(vs: VectorStore) -> None:
    chunks = [
        _make_cwm(chunk_id="pub-0", text="公共条款一", source_name="pub.txt", owner_id=None),
        _make_cwm(chunk_id="pub-1", text="公共条款二", source_name="pub.txt", owner_id=None, chunk_index=1),
        _make_cwm(chunk_id="alice-0", text="爱丽丝的私人笔记", source_name="alice.txt", owner_id="github:alice"),
        _make_cwm(chunk_id="bob-0", text="鲍勃的合同草稿", source_name="bob.txt", owner_id="github:bob"),
    ]
    embeddings = [
        [1.0, 0.0, 0.0, 0.0],
        [0.9, 0.1, 0.0, 0.0],
        [0.0, 1.0, 0.0, 0.0],
        [0.0, 0.0, 1.0, 0.0],
    ]
    vs.add_chunks(chunks, embeddings)


# ── query() owners 过滤 ─────────────────────────────────────────────


class TestQueryOwners:
    def test_none_returns_all(self, isolated_vs: VectorStore) -> None:
        _seed(isolated_vs)
        res = isolated_vs.query([1.0, 0.0, 0.0, 0.0], top_k=10, owners=None)
        assert len(res) == 4

    def test_public_only(self, isolated_vs: VectorStore) -> None:
        _seed(isolated_vs)
        res = isolated_vs.query([1.0, 0.0, 0.0, 0.0], top_k=10, owners=[None])
        ids = [r["id"] for r in res]
        assert set(ids) == {"pub-0", "pub-1"}

    def test_single_owner(self, isolated_vs: VectorStore) -> None:
        _seed(isolated_vs)
        res = isolated_vs.query(
            [0.0, 1.0, 0.0, 0.0], top_k=10, owners=["github:alice"]
        )
        assert [r["id"] for r in res] == ["alice-0"]

    def test_public_plus_owner(self, isolated_vs: VectorStore) -> None:
        _seed(isolated_vs)
        res = isolated_vs.query(
            [1.0, 0.0, 0.0, 0.0], top_k=10, owners=[None, "github:alice"]
        )
        ids = {r["id"] for r in res}
        assert ids == {"pub-0", "pub-1", "alice-0"}

    def test_empty_owners_returns_nothing(
        self, isolated_vs: VectorStore
    ) -> None:
        _seed(isolated_vs)
        res = isolated_vs.query([1.0, 0.0, 0.0, 0.0], top_k=10, owners=[])
        assert res == []


# ── get_all_sources() 按 (source_name, owner_id) 聚合 ───────────────


class TestGetAllSourcesOwners:
    def test_aggregates_per_owner(self, isolated_vs: VectorStore) -> None:
        _seed(isolated_vs)
        # 再插一条 alice 同名不同 chunk
        more = _make_cwm(
            chunk_id="alice-1",
            text="爱丽丝的另一段",
            source_name="alice.txt",
            owner_id="github:alice",
            chunk_index=1,
        )
        isolated_vs.add_chunks([more], [[0.0, 0.9, 0.1, 0.0]])
        rows = isolated_vs.get_all_sources(owners=None)
        # 期望按 (source_name, owner_id) 聚合：pub(None,2) / alice(alice,2) / bob(bob,1)
        keys = {(r["source_name"], r["owner_id"]) for r in rows}
        assert keys == {
            ("pub.txt", None),
            ("alice.txt", "github:alice"),
            ("bob.txt", "github:bob"),
        }
        counts = {(r["source_name"], r["owner_id"]): r["chunk_count"] for r in rows}
        assert counts[("pub.txt", None)] == 2
        assert counts[("alice.txt", "github:alice")] == 2
        assert counts[("bob.txt", "github:bob")] == 1

    def test_owners_filter(self, isolated_vs: VectorStore) -> None:
        _seed(isolated_vs)
        rows = isolated_vs.get_all_sources(owners=[None])
        assert [r["source_name"] for r in rows] == ["pub.txt"]
        assert rows[0]["owner_id"] is None


# ── delete_by_source: 默认 / 指定 owner ─────────────────────────────


class TestDeleteByOwner:
    def test_default_deletes_all_owners(
        self, isolated_vs: VectorStore
    ) -> None:
        """未传 owner_id → admin 视角全删（含公共 + 所有 owner）。"""
        _seed(isolated_vs)
        # 额外加一条 alice 同 source_name 但不同 chunk
        extra = _make_cwm(
            chunk_id="pub-alice", text="x", source_name="pub.txt",
            owner_id="github:alice", chunk_index=2,
        )
        isolated_vs.add_chunks([extra], [[0.5, 0.5, 0.0, 0.0]])
        n = isolated_vs.delete_by_source("pub.txt")
        # pub.txt 共 3 条（2 public + 1 alice 同名）全删
        assert n == 3

    def test_delete_only_public(self, isolated_vs: VectorStore) -> None:
        _seed(isolated_vs)
        # 再注一条 alice 同名 pub.txt
        extra = _make_cwm(
            chunk_id="pub-alice", text="x", source_name="pub.txt",
            owner_id="github:alice", chunk_index=2,
        )
        isolated_vs.add_chunks([extra], [[0.5, 0.5, 0.0, 0.0]])
        n = isolated_vs.delete_by_source("pub.txt", owner_id=None)
        assert n == 2  # 只删两条公共
        # alice 的同名仍存在
        rows = isolated_vs.get_all_sources(owners=None)
        keys = {(r["source_name"], r["owner_id"]) for r in rows}
        assert ("pub.txt", "github:alice") in keys

    def test_delete_only_specific_user(
        self, isolated_vs: VectorStore
    ) -> None:
        _seed(isolated_vs)
        n = isolated_vs.delete_by_source("alice.txt", owner_id="github:alice")
        assert n == 1
        rows = isolated_vs.get_all_sources(owners=None)
        names = {r["source_name"] for r in rows}
        assert "alice.txt" not in names


# ── migrate_owner_id_marker 启动迁移 + 幂等 ────────────────────────


class TestMigrate:
    def test_migrate_zero_when_all_have_owner(
        self, isolated_vs: VectorStore
    ) -> None:
        """新增的 chunk 已带 owner_id（PUBLIC 或某用户），迁移应返回 0。"""
        _seed(isolated_vs)
        assert isolated_vs.migrate_owner_id_marker() == 0
        # 再次调用仍为 0（幂等）
        assert isolated_vs.migrate_owner_id_marker() == 0

    def test_migrate_back_fills_legacy_metadata(
        self, isolated_vs: VectorStore
    ) -> None:
        """模拟旧库：直接走 collection.add 写入不含 owner_id 的 metadata，
        迁移后应被标记为 PUBLIC_OWNER_MARKER。"""
        isolated_vs.collection.add(
            ids=["legacy-1", "legacy-2"],
            documents=["a", "b"],
            embeddings=[[1.0, 0, 0, 0], [0, 1.0, 0, 0]],
            metadatas=[
                {"source_name": "legacy.txt", "source_type": "file"},
                {"source_name": "legacy.txt", "source_type": "file"},
            ],
        )
        n = isolated_vs.migrate_owner_id_marker()
        assert n == 2

        # 验证 metadata 已落 PUBLIC_OWNER_MARKER
        got = isolated_vs.collection.get(
            ids=["legacy-1", "legacy-2"], include=["metadatas"]
        )
        assert got["metadatas"] is not None
        assert all(m["owner_id"] == PUBLIC_OWNER_MARKER for m in got["metadatas"])

        # 第二次调用应该幂等返 0
        assert isolated_vs.migrate_owner_id_marker() == 0

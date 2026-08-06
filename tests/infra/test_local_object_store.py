"""LocalObjectStore 路径安全与不可变写入测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from domain import ObjectStorePort
from infra.object_store import LocalObjectStore


@pytest.fixture
def store(tmp_path: Path) -> LocalObjectStore:
    return LocalObjectStore(tmp_path / "objects")


class TestLocalObjectStore:
    def test_satisfies_port(self, store: LocalObjectStore) -> None:
        assert isinstance(store, ObjectStorePort)

    def test_put_read_exists_delete(self, store: LocalObjectStore) -> None:
        key = "ws_001/doc_001/ver_001/source.pdf"
        store.put(key, b"pdf-bytes")
        assert store.exists(key) is True
        assert store.read(key) == b"pdf-bytes"
        assert store.delete(key) is True
        assert store.exists(key) is False
        assert store.delete(key) is False

    def test_same_content_put_is_idempotent(self, store: LocalObjectStore) -> None:
        store.put("a/b.txt", b"same")
        store.put("a/b.txt", b"same")
        assert store.read("a/b.txt") == b"same"

    def test_different_content_cannot_overwrite(self, store: LocalObjectStore) -> None:
        store.put("a/b.txt", b"first")
        with pytest.raises(FileExistsError):
            store.put("a/b.txt", b"second")
        assert store.read("a/b.txt") == b"first"

    @pytest.mark.parametrize(
        "key",
        [
            "",
            ".",
            "../secret",
            "a/../../secret",
            "/absolute/path",
            r"a\b.txt",
        ],
    )
    def test_rejects_invalid_object_keys(
        self,
        store: LocalObjectStore,
        key: str,
    ) -> None:
        with pytest.raises(ValueError):
            store.put(key, b"x")

    def test_empty_content_rejected(self, store: LocalObjectStore) -> None:
        with pytest.raises(ValueError, match="不能为空"):
            store.put("a/empty.txt", b"")

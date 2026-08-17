"""本地文件系统 ObjectStorePort 实现。"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from infra.object_store.keys import validate_object_key


class LocalObjectStore:
    """把对象键安全映射到固定根目录，使用同目录临时文件原子替换。"""

    def __init__(self, root: str | Path) -> None:
        self._root = Path(root).expanduser().resolve()
        self._root.mkdir(parents=True, exist_ok=True)

    @property
    def root(self) -> Path:
        return self._root

    def put(self, object_key: str, content: bytes) -> None:
        if not content:
            raise ValueError("对象内容不能为空")
        target = self._resolve(object_key)
        if target.exists():
            if target.read_bytes() == content:
                return
            raise FileExistsError(f"对象 {object_key!r} 已存在且内容不同")
        target.parent.mkdir(parents=True, exist_ok=True)
        temp_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                dir=target.parent,
                prefix=f".{target.name}.",
                suffix=".tmp",
                delete=False,
            ) as temp_file:
                temp_file.write(content)
                temp_file.flush()
                os.fsync(temp_file.fileno())
                temp_path = Path(temp_file.name)
            os.replace(temp_path, target)
        finally:
            if temp_path is not None and temp_path.exists():
                temp_path.unlink()

    def read(self, object_key: str) -> bytes:
        return self._resolve(object_key).read_bytes()

    def delete(self, object_key: str) -> bool:
        target = self._resolve(object_key)
        if not target.exists():
            return False
        target.unlink()
        self._remove_empty_parents(target.parent)
        return True

    def exists(self, object_key: str) -> bool:
        return self._resolve(object_key).is_file()

    def _resolve(self, object_key: str) -> Path:
        key = validate_object_key(object_key)
        target = self._root.joinpath(*key.split("/")).resolve()
        if not target.is_relative_to(self._root):
            raise ValueError("object_key 超出对象存储根目录")
        return target

    def _remove_empty_parents(self, directory: Path) -> None:
        current = directory
        while current != self._root and current.is_relative_to(self._root):
            try:
                current.rmdir()
            except OSError:
                break
            current = current.parent

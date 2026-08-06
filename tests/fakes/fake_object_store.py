"""内存 ObjectStorePort Fake。"""

from __future__ import annotations


class FakeObjectStore:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def put(self, object_key: str, content: bytes) -> None:
        if not content:
            raise ValueError("对象内容不能为空")
        self.objects[object_key] = bytes(content)

    def read(self, object_key: str) -> bytes:
        return self.objects[object_key]

    def delete(self, object_key: str) -> bool:
        return self.objects.pop(object_key, None) is not None

    def exists(self, object_key: str) -> bool:
        return object_key in self.objects

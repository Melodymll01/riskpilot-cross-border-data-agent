"""对象键验证：本地文件系统与 S3 Adapter 共用同一安全语义。"""

from __future__ import annotations

from pathlib import PurePosixPath


def validate_object_key(object_key: str) -> str:
    if not object_key or "\\" in object_key:
        raise ValueError("object_key 必须是非空 POSIX 相对路径")
    key = PurePosixPath(object_key)
    if (
        not key.parts
        or str(key) == "."
        or key.is_absolute()
        or any(part in {"", ".", ".."} for part in key.parts)
    ):
        raise ValueError("object_key 不能包含绝对路径或路径穿越")
    return key.as_posix()

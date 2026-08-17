"""对象存储适配器。"""

from infra.object_store.local import LocalObjectStore
from infra.object_store.s3 import S3ObjectStore

__all__ = ["LocalObjectStore", "S3ObjectStore"]

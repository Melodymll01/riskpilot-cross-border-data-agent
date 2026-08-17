"""S3/MinIO ObjectStorePort Adapter。"""

from __future__ import annotations

import hashlib
from typing import Any

import boto3
from botocore.client import Config
from botocore.exceptions import ClientError

from infra.object_store.keys import validate_object_key

_NOT_FOUND_CODES = {"404", "NoSuchKey", "NotFound"}
_PRECONDITION_CODES = {"409", "412", "ConditionalRequestConflict", "PreconditionFailed"}


class S3ObjectStore:
    """使用条件写实现不可变对象语义，兼容 AWS S3 与 MinIO。"""

    def __init__(
        self,
        *,
        bucket: str,
        endpoint_url: str | None = None,
        access_key_id: str | None = None,
        secret_access_key: str | None = None,
        region: str = "us-east-1",
        client: Any | None = None,
    ) -> None:
        if not bucket.strip():
            raise ValueError("bucket 不能为空")
        if bool(access_key_id) != bool(secret_access_key):
            raise ValueError("access_key_id 与 secret_access_key 必须同时配置或同时省略")
        self._bucket = bucket
        self._client_instance = client
        self._client_options = {
            "endpoint_url": endpoint_url,
            "aws_access_key_id": access_key_id,
            "aws_secret_access_key": secret_access_key,
            "region_name": region,
            "config": Config(signature_version="s3v4", s3={"addressing_style": "path"}),
        }

    @property
    def bucket(self) -> str:
        return self._bucket

    def put(self, object_key: str, content: bytes) -> None:
        key = validate_object_key(object_key)
        if not content:
            raise ValueError("对象内容不能为空")
        digest = hashlib.sha256(content).hexdigest()
        try:
            self._client().put_object(
                Bucket=self._bucket,
                Key=key,
                Body=content,
                ContentLength=len(content),
                Metadata={"sha256": digest},
                IfNoneMatch="*",
            )
        except ClientError as exc:
            if _error_code(exc) not in _PRECONDITION_CODES:
                raise
            if self._same_content(key, digest=digest, size=len(content)):
                return
            raise FileExistsError(f"对象 {object_key!r} 已存在且内容不同") from exc

    def read(self, object_key: str) -> bytes:
        key = validate_object_key(object_key)
        try:
            response = self._client().get_object(Bucket=self._bucket, Key=key)
        except ClientError as exc:
            if _error_code(exc) in _NOT_FOUND_CODES:
                raise FileNotFoundError(object_key) from exc
            raise
        return bytes(response["Body"].read())

    def delete(self, object_key: str) -> bool:
        key = validate_object_key(object_key)
        if not self.exists(key):
            return False
        self._client().delete_object(Bucket=self._bucket, Key=key)
        return True

    def exists(self, object_key: str) -> bool:
        key = validate_object_key(object_key)
        try:
            self._client().head_object(Bucket=self._bucket, Key=key)
        except ClientError as exc:
            if _error_code(exc) in _NOT_FOUND_CODES:
                return False
            raise
        return True

    def _same_content(self, key: str, *, digest: str, size: int) -> bool:
        try:
            response = self._client().head_object(Bucket=self._bucket, Key=key)
        except ClientError as exc:
            if _error_code(exc) in _NOT_FOUND_CODES:
                return False
            raise
        metadata = response.get("Metadata", {})
        if int(response.get("ContentLength", -1)) != size:
            return False
        existing_digest = metadata.get("sha256")
        if existing_digest:
            return existing_digest == digest
        return hashlib.sha256(self.read(key)).hexdigest() == digest

    def _client(self) -> Any:
        if self._client_instance is None:
            self._client_instance = boto3.client("s3", **self._client_options)
        return self._client_instance


def _error_code(exc: ClientError) -> str:
    return str(exc.response.get("Error", {}).get("Code", ""))

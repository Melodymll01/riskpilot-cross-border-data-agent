"""S3ObjectStore 离线 contract 与可选真实 MinIO contract。"""

from __future__ import annotations

import io
import os
from collections.abc import Iterator
from typing import Any

import boto3
import pytest
from botocore.client import Config
from botocore.exceptions import ClientError

from domain import ObjectStorePort
from infra.object_store import S3ObjectStore

_LIVE_ENDPOINT = os.getenv("TEST_S3_ENDPOINT_URL")
_LIVE_ACCESS_KEY = os.getenv("TEST_S3_ACCESS_KEY_ID")
_LIVE_SECRET_KEY = os.getenv("TEST_S3_SECRET_ACCESS_KEY")
_LIVE_BUCKET = os.getenv("TEST_S3_BUCKET", "riskpilot-test")


def _client_error(code: str, operation: str) -> ClientError:
    return ClientError(
        {"Error": {"Code": code, "Message": code}},
        operation,
    )


class FakeS3Client:
    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], tuple[bytes, dict[str, str]]] = {}

    def put_object(self, **kwargs: Any) -> dict[str, str]:
        location = (kwargs["Bucket"], kwargs["Key"])
        if kwargs.get("IfNoneMatch") == "*" and location in self.objects:
            raise _client_error("PreconditionFailed", "PutObject")
        body = bytes(kwargs["Body"])
        self.objects[location] = (body, dict(kwargs.get("Metadata", {})))
        return {"ETag": "fake"}

    def get_object(self, **kwargs: Any) -> dict[str, Any]:
        location = (kwargs["Bucket"], kwargs["Key"])
        if location not in self.objects:
            raise _client_error("NoSuchKey", "GetObject")
        content, metadata = self.objects[location]
        return {
            "Body": io.BytesIO(content),
            "ContentLength": len(content),
            "Metadata": metadata,
        }

    def head_object(self, **kwargs: Any) -> dict[str, Any]:
        location = (kwargs["Bucket"], kwargs["Key"])
        if location not in self.objects:
            raise _client_error("404", "HeadObject")
        content, metadata = self.objects[location]
        return {"ContentLength": len(content), "Metadata": metadata}

    def delete_object(self, **kwargs: Any) -> dict[str, str]:
        self.objects.pop((kwargs["Bucket"], kwargs["Key"]), None)
        return {"DeleteMarker": "true"}


@pytest.fixture
def fake_store() -> S3ObjectStore:
    return S3ObjectStore(bucket="riskpilot-test", client=FakeS3Client())


def test_satisfies_port(fake_store: S3ObjectStore) -> None:
    assert isinstance(fake_store, ObjectStorePort)


def test_put_read_exists_delete(fake_store: S3ObjectStore) -> None:
    key = "ws_001/doc_001/ver_001/source.pdf"
    fake_store.put(key, b"pdf-bytes")
    assert fake_store.exists(key) is True
    assert fake_store.read(key) == b"pdf-bytes"
    assert fake_store.delete(key) is True
    assert fake_store.exists(key) is False
    assert fake_store.delete(key) is False
    with pytest.raises(FileNotFoundError):
        fake_store.read(key)


def test_same_content_is_idempotent_but_overwrite_is_rejected(
    fake_store: S3ObjectStore,
) -> None:
    fake_store.put("a/b.txt", b"same")
    fake_store.put("a/b.txt", b"same")
    with pytest.raises(FileExistsError):
        fake_store.put("a/b.txt", b"different")
    assert fake_store.read("a/b.txt") == b"same"


@pytest.mark.parametrize(
    "key",
    ["", ".", "../secret", "a/../../secret", "/absolute/path", r"a\b.txt"],
)
def test_rejects_invalid_object_keys(fake_store: S3ObjectStore, key: str) -> None:
    with pytest.raises(ValueError):
        fake_store.put(key, b"x")


def test_empty_content_and_invalid_credentials_are_rejected(
    fake_store: S3ObjectStore,
) -> None:
    with pytest.raises(ValueError, match="不能为空"):
        fake_store.put("a/empty.txt", b"")
    with pytest.raises(ValueError, match="同时配置"):
        S3ObjectStore(
            bucket="riskpilot-test",
            access_key_id="only-access-key",
            client=FakeS3Client(),
        )


@pytest.fixture
def live_store() -> Iterator[S3ObjectStore]:
    if not (_LIVE_ENDPOINT and _LIVE_ACCESS_KEY and _LIVE_SECRET_KEY):
        pytest.skip("需要 TEST_S3_* 环境变量验证真实 MinIO")
    client = boto3.client(
        "s3",
        endpoint_url=_LIVE_ENDPOINT,
        aws_access_key_id=_LIVE_ACCESS_KEY,
        aws_secret_access_key=_LIVE_SECRET_KEY,
        region_name="us-east-1",
        config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
    )
    try:
        client.create_bucket(Bucket=_LIVE_BUCKET)
    except ClientError as exc:
        if str(exc.response.get("Error", {}).get("Code")) not in {
            "BucketAlreadyOwnedByYou",
            "BucketAlreadyExists",
        }:
            raise
    store = S3ObjectStore(bucket=_LIVE_BUCKET, client=client)
    yield store
    response = client.list_objects_v2(Bucket=_LIVE_BUCKET, Prefix="contract/")
    for item in response.get("Contents", []):
        client.delete_object(Bucket=_LIVE_BUCKET, Key=item["Key"])


def test_two_independent_adapters_share_minio(live_store: S3ObjectStore) -> None:
    client = boto3.client(
        "s3",
        endpoint_url=_LIVE_ENDPOINT,
        aws_access_key_id=_LIVE_ACCESS_KEY,
        aws_secret_access_key=_LIVE_SECRET_KEY,
        region_name="us-east-1",
        config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
    )
    worker_store = S3ObjectStore(bucket=_LIVE_BUCKET, client=client)
    key = "contract/api-to-worker/source.txt"
    live_store.put(key, b"shared-object")
    assert worker_store.read(key) == b"shared-object"
    worker_store.put(key, b"shared-object")
    with pytest.raises(FileExistsError):
        worker_store.put(key, b"changed")

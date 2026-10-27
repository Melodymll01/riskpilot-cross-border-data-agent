"""SQLite storage for Python-owned immutable parse artifacts."""

from __future__ import annotations

import json

from domain.document_content import DocumentParseSnapshot
from infra.storage._db import SqliteConnectionPool

_SCHEMA = """
CREATE TABLE IF NOT EXISTS parse_artifacts (
    document_version_id TEXT PRIMARY KEY,
    document_id         TEXT NOT NULL,
    owner_id            TEXT NOT NULL,
    source_sha256       TEXT NOT NULL,
    payload_json        TEXT NOT NULL,
    parsed_at           REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_parse_artifacts_scope
    ON parse_artifacts(owner_id, document_id, document_version_id);
"""


class SqliteParseArtifactStore:
    def __init__(self, pool: SqliteConnectionPool) -> None:
        self._pool = pool
        with self._pool.get() as conn:
            conn.executescript(_SCHEMA)

    def save(
        self,
        *,
        owner_id: str,
        document_id: str,
        document_version_id: str,
        source_sha256: str,
        snapshot: DocumentParseSnapshot,
    ) -> None:
        if snapshot.document_version_id != document_version_id:
            raise ValueError("parse artifact version does not match immutable input")
        if snapshot.source_sha256 != source_sha256:
            raise ValueError("parse artifact SHA-256 does not match immutable input")
        payload = snapshot.model_dump_json()
        conn = self._pool.get()
        with conn:
            existing = conn.execute(
                "SELECT document_id, owner_id, source_sha256, payload_json "
                "FROM parse_artifacts WHERE document_version_id = ?",
                (document_version_id,),
            ).fetchone()
            if existing is not None:
                if (
                    existing["document_id"] != document_id
                    or existing["owner_id"] != owner_id
                    or existing["source_sha256"] != source_sha256
                    or json.loads(existing["payload_json"]) != json.loads(payload)
                ):
                    raise ValueError(
                        "immutable parse artifact already exists with different content"
                    )
                return
            conn.execute(
                "INSERT INTO parse_artifacts "
                "(document_version_id, document_id, owner_id, source_sha256, payload_json, parsed_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    document_version_id,
                    document_id,
                    owner_id,
                    source_sha256,
                    payload,
                    snapshot.parsed_at,
                ),
            )

    def get(
        self,
        *,
        owner_id: str,
        document_id: str,
        document_version_id: str,
        source_sha256: str,
    ) -> DocumentParseSnapshot | None:
        row = (
            self._pool.get()
            .execute(
                "SELECT payload_json FROM parse_artifacts "
                "WHERE owner_id = ? AND document_id = ? AND document_version_id = ? "
                "AND source_sha256 = ?",
                (owner_id, document_id, document_version_id, source_sha256),
            )
            .fetchone()
        )
        if row is None:
            return None
        return DocumentParseSnapshot.model_validate_json(row["payload_json"])

    def delete(
        self,
        *,
        owner_id: str,
        document_id: str,
        document_version_id: str,
        source_sha256: str,
    ) -> bool:
        conn = self._pool.get()
        with conn:
            cursor = conn.execute(
                "DELETE FROM parse_artifacts WHERE owner_id = ? AND document_id = ? "
                "AND document_version_id = ? AND source_sha256 = ?",
                (owner_id, document_id, document_version_id, source_sha256),
            )
        return cursor.rowcount == 1

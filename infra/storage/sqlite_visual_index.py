"""SQLite Case 图片向量索引。"""

from __future__ import annotations

import json
import math

from domain.visual import VisualAsset, VisualSearchHit
from infra.storage._db import SqliteConnectionPool


class SqliteVisualIndex:
    def __init__(self, pool: SqliteConnectionPool) -> None:
        self._pool = pool

    def add(self, asset: VisualAsset, embedding: list[float]) -> None:
        if not embedding:
            raise ValueError("图片 embedding 不能为空")
        conn = self._pool.get()
        row = conn.execute(
            """
            SELECT c.workspace_id
            FROM compliance_cases AS c
            WHERE c.case_id = ?
            """,
            (asset.case_id,),
        ).fetchone()
        if row is None or row["workspace_id"] != asset.workspace_id:
            raise ValueError("图片的 Workspace/Case 作用域无效")
        with conn:
            conn.execute(
                """
                INSERT INTO visual_assets
                    (asset_id, workspace_id, case_id, object_key, filename,
                     mime_type, sha256, width, height, caption, embedding_json,
                     created_by, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    asset.asset_id,
                    asset.workspace_id,
                    asset.case_id,
                    asset.object_key,
                    asset.filename,
                    asset.mime_type,
                    asset.sha256,
                    asset.width,
                    asset.height,
                    asset.caption,
                    json.dumps(embedding),
                    asset.created_by,
                    asset.created_at,
                ),
            )

    def search(
        self,
        *,
        workspace_id: str,
        case_id: str,
        query_embedding: list[float],
        top_k: int,
    ) -> list[VisualSearchHit]:
        if not query_embedding:
            raise ValueError("query_embedding 不能为空")
        if top_k < 1:
            raise ValueError("top_k 必须大于 0")
        rows = (
            self._pool.get()
            .execute(
                """
                SELECT * FROM visual_assets
                WHERE workspace_id = ? AND case_id = ?
                """,
                (workspace_id, case_id),
            )
            .fetchall()
        )
        scored: list[VisualSearchHit] = []
        for row in rows:
            embedding = [float(value) for value in json.loads(row["embedding_json"])]
            if len(embedding) != len(query_embedding):
                raise ValueError("图片与查询 embedding 维度不一致")
            scored.append(
                VisualSearchHit(
                    asset=_row_to_asset(row),
                    score=_cosine(query_embedding, embedding),
                )
            )
        scored.sort(key=lambda hit: (hit.score, hit.asset.asset_id), reverse=True)
        return scored[:top_k]

    def get(self, asset_id: str) -> VisualAsset | None:
        row = (
            self._pool.get()
            .execute(
                "SELECT * FROM visual_assets WHERE asset_id = ?",
                (asset_id,),
            )
            .fetchone()
        )
        return _row_to_asset(row) if row is not None else None


def _row_to_asset(row) -> VisualAsset:
    return VisualAsset(
        asset_id=row["asset_id"],
        workspace_id=row["workspace_id"],
        case_id=row["case_id"],
        object_key=row["object_key"],
        filename=row["filename"],
        mime_type=row["mime_type"],
        sha256=row["sha256"],
        width=row["width"],
        height=row["height"],
        caption=row["caption"],
        created_by=row["created_by"],
        created_at=row["created_at"],
    )


def _cosine(left: list[float], right: list[float]) -> float:
    dot = sum(a * b for a, b in zip(left, right, strict=True))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm == 0.0 or right_norm == 0.0:
        return 0.0
    return max(-1.0, min(1.0, dot / (left_norm * right_norm)))

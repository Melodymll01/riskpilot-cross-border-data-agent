"""SQLite 图片索引的作用域、排序和维度检查。"""

from __future__ import annotations

import time

from domain.cases import Case
from domain.visual import VisualAsset
from domain.workspaces import Workspace, WorkspaceMembership
from infra.storage import SqliteCaseRepo, SqliteVisualIndex, SqliteWorkspaceRepo
from infra.storage._db import SqliteConnectionPool


def _seed(pool: SqliteConnectionPool) -> tuple[SqliteVisualIndex, str, str]:
    workspace_repo = SqliteWorkspaceRepo(pool)
    case_repo = SqliteCaseRepo(pool)
    now = time.time()
    workspace = Workspace(
        workspace_id="ws_001",
        name="visual",
        created_by="github:alice",
        created_at=now,
        updated_at=now,
    )
    workspace_repo.create(
        workspace,
        WorkspaceMembership(
            workspace_id=workspace.workspace_id,
            user_id="github:alice",
            role="admin",
            joined_at=now,
        ),
    )
    case = Case(
        case_id="case_001",
        workspace_id=workspace.workspace_id,
        title="visual",
        owner_id="github:alice",
        created_at=now,
        updated_at=now,
    )
    case_repo.create(case)
    return SqliteVisualIndex(pool), workspace.workspace_id, case.case_id


def _asset(asset_id: str, workspace_id: str, case_id: str) -> VisualAsset:
    return VisualAsset(
        asset_id=asset_id,
        workspace_id=workspace_id,
        case_id=case_id,
        object_key=f"visual/{asset_id}.png",
        filename=f"{asset_id}.png",
        mime_type="image/png",
        sha256="a" * 64,
        width=32,
        height=32,
        created_by="github:alice",
        created_at=time.time(),
    )


def test_search_ranks_cosine_and_filters_scope(tmp_path) -> None:
    index, workspace_id, case_id = _seed(
        SqliteConnectionPool(str(tmp_path / "visual.sqlite3"))
    )
    index.add(_asset("red", workspace_id, case_id), [1.0, 0.0])
    index.add(_asset("blue", workspace_id, case_id), [0.0, 1.0])

    hits = index.search(
        workspace_id=workspace_id,
        case_id=case_id,
        query_embedding=[0.9, 0.1],
        top_k=2,
    )

    assert [hit.asset.asset_id for hit in hits] == ["red", "blue"]
    assert index.search(
        workspace_id=workspace_id,
        case_id="case_other",
        query_embedding=[1.0, 0.0],
        top_k=2,
    ) == []


def test_get_round_trip(tmp_path) -> None:
    index, workspace_id, case_id = _seed(
        SqliteConnectionPool(str(tmp_path / "visual.sqlite3"))
    )
    asset = _asset("asset_001", workspace_id, case_id)
    index.add(asset, [1.0, 0.0])

    assert index.get(asset.asset_id) == asset

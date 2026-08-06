"""SqlitePolicyRuleRepo 测试。"""

from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path

import pytest

from domain import PolicyRule, PolicyRuleRepoPort, Workspace, WorkspaceMembership
from infra.storage import SqlitePolicyRuleRepo, SqliteWorkspaceRepo
from infra.storage._db import SqliteConnectionPool


@pytest.fixture
def repo(tmp_path: Path) -> SqlitePolicyRuleRepo:
    pool = SqliteConnectionPool(str(tmp_path / "rules.db"))
    workspace_repo = SqliteWorkspaceRepo(pool)
    for workspace_id in ("ws_001", "ws_002"):
        workspace_repo.create(
            Workspace(
                workspace_id=workspace_id,
                name=workspace_id,
                created_by="github:alice",
                created_at=100.0,
                updated_at=100.0,
            ),
            WorkspaceMembership(
                workspace_id=workspace_id,
                user_id="github:alice",
                role="admin",
                joined_at=100.0,
            ),
        )
    return SqlitePolicyRuleRepo(pool)


def _rule(**overrides: object) -> PolicyRule:
    values: dict[str, object] = {
        "workspace_id": "ws_001",
        "rule_id": "SYNTHETIC-001",
        "ruleset_version": "synthetic-v1",
        "jurisdiction": "CN",
        "effective_from": date(2026, 1, 1),
        "status": "draft",
        "required_fact_fields": ["flag"],
        "condition": {"field": "flag", "operator": "eq", "value": True},
        "result": {"candidate_path": "synthetic"},
        "source_clause_ids": ["synthetic-clause"],
    }
    values.update(overrides)
    return PolicyRule(**values)  # type: ignore[arg-type]


class TestSqlitePolicyRuleRepo:
    def test_satisfies_port(self, repo: SqlitePolicyRuleRepo) -> None:
        assert isinstance(repo, PolicyRuleRepoPort)

    def test_create_get_and_json_round_trip(self, repo: SqlitePolicyRuleRepo) -> None:
        rule = _rule()
        repo.create(rule)
        assert repo.get(rule.workspace_id, rule.rule_id, rule.ruleset_version) == rule

    def test_same_rule_version_cannot_be_overwritten(self, repo: SqlitePolicyRuleRepo) -> None:
        rule = _rule()
        repo.create(rule)
        with pytest.raises(sqlite3.IntegrityError):
            repo.create(rule)

    def test_list_filters_and_update_status(self, repo: SqlitePolicyRuleRepo) -> None:
        cn = _rule(rule_id="cn")
        eu = _rule(rule_id="eu", jurisdiction="EU")
        other = _rule(rule_id="other", ruleset_version="synthetic-v2")
        for rule in (cn, eu, other):
            repo.create(rule)
        published = cn.model_copy(update={"status": "published"})
        repo.update_status(published)

        assert repo.list_rules(
            workspace_id="ws_001",
            ruleset_version="synthetic-v1",
            jurisdiction="CN",
            status="published",
        ) == [published]

    def test_workspace_scope_isolated(self, repo: SqlitePolicyRuleRepo) -> None:
        workspace_a = _rule(rule_id="same")
        workspace_b = _rule(rule_id="same", workspace_id="ws_002")
        repo.create(workspace_a)
        repo.create(workspace_b)
        assert repo.list_rules(workspace_id="ws_001") == [workspace_a]
        assert repo.list_rules(workspace_id="ws_002") == [workspace_b]

"""SQLite PolicyRuleRepoPort 实现。"""

from __future__ import annotations

import json
from datetime import date
from typing import Any, cast

from domain.policies import PolicyRule, PolicyRuleStatus
from infra.storage._db import SqliteConnectionPool


class SqlitePolicyRuleRepo:
    def __init__(self, pool: SqliteConnectionPool) -> None:
        self._pool = pool

    def create(self, rule: PolicyRule) -> None:
        conn = self._pool.get()
        conn.execute(
            """
            INSERT INTO policy_rules
                (workspace_id, rule_id, ruleset_version, jurisdiction, effective_from,
                 effective_to, status, required_fact_fields, condition_json,
                 result_json, source_clause_ids)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            _rule_values(rule),
        )
        conn.commit()

    def get(
        self,
        workspace_id: str,
        rule_id: str,
        ruleset_version: str,
    ) -> PolicyRule | None:
        row = (
            self._pool.get()
            .execute(
                """
            SELECT * FROM policy_rules
            WHERE workspace_id = ? AND rule_id = ? AND ruleset_version = ?
            """,
                (workspace_id, rule_id, ruleset_version),
            )
            .fetchone()
        )
        return None if row is None else _row_to_rule(row)

    def list_rules(
        self,
        *,
        workspace_id: str,
        ruleset_version: str | None = None,
        jurisdiction: str | None = None,
        status: str | None = None,
    ) -> list[PolicyRule]:
        clauses: list[str] = ["workspace_id = ?"]
        params: list[str] = [workspace_id]
        if ruleset_version is not None:
            clauses.append("ruleset_version = ?")
            params.append(ruleset_version)
        if jurisdiction is not None:
            clauses.append("jurisdiction = ?")
            params.append(jurisdiction)
        if status is not None:
            clauses.append("status = ?")
            params.append(status)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = (
            self._pool.get()
            .execute(
                f"""
            SELECT * FROM policy_rules
            {where}
            ORDER BY ruleset_version, rule_id
            """,
                params,
            )
            .fetchall()
        )
        return [_row_to_rule(row) for row in rows]

    def update_status(self, rule: PolicyRule) -> None:
        conn = self._pool.get()
        conn.execute(
            """
            UPDATE policy_rules SET status = ?
            WHERE workspace_id = ? AND rule_id = ? AND ruleset_version = ?
            """,
            (
                rule.status,
                rule.workspace_id,
                rule.rule_id,
                rule.ruleset_version,
            ),
        )
        conn.commit()


def _rule_values(rule: PolicyRule) -> tuple[object, ...]:
    return (
        rule.workspace_id,
        rule.rule_id,
        rule.ruleset_version,
        rule.jurisdiction,
        rule.effective_from.isoformat(),
        rule.effective_to.isoformat() if rule.effective_to else None,
        rule.status,
        json.dumps(rule.required_fact_fields, ensure_ascii=False),
        json.dumps(rule.condition, ensure_ascii=False),
        json.dumps(rule.result, ensure_ascii=False),
        json.dumps(rule.source_clause_ids, ensure_ascii=False),
    )


def _row_to_rule(row: Any) -> PolicyRule:
    return PolicyRule(
        workspace_id=row["workspace_id"],
        rule_id=row["rule_id"],
        ruleset_version=row["ruleset_version"],
        jurisdiction=row["jurisdiction"],
        effective_from=date.fromisoformat(row["effective_from"]),
        effective_to=(
            None if row["effective_to"] is None else date.fromisoformat(row["effective_to"])
        ),
        status=_validate_status(row["status"]),
        required_fact_fields=json.loads(row["required_fact_fields"]),
        condition=json.loads(row["condition_json"]),
        result=json.loads(row["result_json"]),
        source_clause_ids=json.loads(row["source_clause_ids"]),
    )


def _validate_status(value: str) -> PolicyRuleStatus:
    if value not in {"draft", "published", "retired"}:
        raise ValueError(f"invalid policy rule status in DB: {value!r}")
    return cast("PolicyRuleStatus", value)

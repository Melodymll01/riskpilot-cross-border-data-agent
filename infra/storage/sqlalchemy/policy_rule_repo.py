"""SQLAlchemy PolicyRuleRepoPort 实现。"""

from __future__ import annotations

from typing import Any, cast

from sqlalchemy import select, update
from sqlalchemy.engine import CursorResult

from domain.policies import PolicyRule
from infra.storage.sqlalchemy.database import SqlAlchemyDatabase
from infra.storage.sqlalchemy.models import PolicyRuleRow


class SqlAlchemyPolicyRuleRepo:
    def __init__(self, database: SqlAlchemyDatabase) -> None:
        self._database = database

    def create(self, rule: PolicyRule) -> None:
        with self._database.session() as session:
            session.add(_row(rule))

    def get(
        self,
        workspace_id: str,
        rule_id: str,
        ruleset_version: str,
    ) -> PolicyRule | None:
        with self._database.read_session() as session:
            row = session.get(
                PolicyRuleRow,
                (workspace_id, rule_id, ruleset_version),
            )
            return None if row is None else _rule(row)

    def list_rules(
        self,
        *,
        workspace_id: str,
        ruleset_version: str | None = None,
        jurisdiction: str | None = None,
        status: str | None = None,
    ) -> list[PolicyRule]:
        statement = select(PolicyRuleRow).where(PolicyRuleRow.workspace_id == workspace_id)
        if ruleset_version is not None:
            statement = statement.where(PolicyRuleRow.ruleset_version == ruleset_version)
        if jurisdiction is not None:
            statement = statement.where(PolicyRuleRow.jurisdiction == jurisdiction)
        if status is not None:
            statement = statement.where(PolicyRuleRow.status == status)
        statement = statement.order_by(
            PolicyRuleRow.ruleset_version,
            PolicyRuleRow.rule_id,
        )
        with self._database.read_session() as session:
            return [_rule(row) for row in session.scalars(statement)]

    def update_status(self, rule: PolicyRule) -> None:
        statement = (
            update(PolicyRuleRow)
            .where(
                PolicyRuleRow.workspace_id == rule.workspace_id,
                PolicyRuleRow.rule_id == rule.rule_id,
                PolicyRuleRow.ruleset_version == rule.ruleset_version,
            )
            .values(status=rule.status)
        )
        with self._database.session() as session:
            result = cast("CursorResult[Any]", session.execute(statement))
            if result.rowcount != 1:
                raise ValueError("待更新 PolicyRule 不存在")


def _row(rule: PolicyRule) -> PolicyRuleRow:
    return PolicyRuleRow(
        workspace_id=rule.workspace_id,
        rule_id=rule.rule_id,
        ruleset_version=rule.ruleset_version,
        jurisdiction=rule.jurisdiction,
        effective_from=rule.effective_from,
        effective_to=rule.effective_to,
        status=rule.status,
        required_fact_fields=rule.required_fact_fields,
        condition=rule.condition,
        result=rule.result,
        source_clause_ids=rule.source_clause_ids,
    )


def _rule(row: PolicyRuleRow) -> PolicyRule:
    return PolicyRule(
        workspace_id=row.workspace_id,
        rule_id=row.rule_id,
        ruleset_version=row.ruleset_version,
        jurisdiction=row.jurisdiction,
        effective_from=row.effective_from,
        effective_to=row.effective_to,
        status=row.status,
        required_fact_fields=row.required_fact_fields,
        condition=row.condition,
        result=row.result,
        source_clause_ids=row.source_clause_ids,
    )

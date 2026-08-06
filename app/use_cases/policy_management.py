"""V2 规则管理与案件确定性评估用例。"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from domain.errors import PolicyRuleNotFound
from domain.policies import PolicyEvaluationReport, PolicyRule
from domain.policy_engine import PolicyRuleEngine

if TYPE_CHECKING:
    from app.use_cases.case_management import CaseManagementUseCase
    from app.use_cases.workspace_management import WorkspaceManagementUseCase
    from domain.ports import CaseFactRepoPort, PolicyRuleRepoPort


class PolicyManagementUseCase:
    def __init__(
        self,
        *,
        rule_repo: PolicyRuleRepoPort,
        fact_repo: CaseFactRepoPort,
        case_management: CaseManagementUseCase,
        workspace_management: WorkspaceManagementUseCase,
        engine: PolicyRuleEngine | None = None,
    ) -> None:
        self._rules = rule_repo
        self._facts = fact_repo
        self._case_management = case_management
        self._workspace_management = workspace_management
        self._engine = engine or PolicyRuleEngine()

    def create_rule(
        self,
        workspace_id: str,
        actor_id: str,
        rule: PolicyRule,
    ) -> PolicyRule:
        self._workspace_management.require_role(
            workspace_id,
            actor_id,
            {"admin"},
            action="创建规则",
        )
        if rule.workspace_id != workspace_id:
            raise ValueError("规则 workspace_id 与权限域不一致")
        if rule.status != "draft":
            raise ValueError("新建规则必须是 draft 状态")
        self._engine.validate_rule(rule)
        self._rules.create(rule)
        return rule

    def publish_rule(
        self,
        workspace_id: str,
        actor_id: str,
        *,
        rule_id: str,
        ruleset_version: str,
    ) -> PolicyRule:
        self._workspace_management.require_role(
            workspace_id,
            actor_id,
            {"admin"},
            action="发布规则",
        )
        rule = self._rules.get(workspace_id, rule_id, ruleset_version)
        if rule is None:
            raise PolicyRuleNotFound(f"{rule_id}@{ruleset_version}")
        self._engine.validate_rule(rule)
        published = cast("PolicyRule", rule.model_copy(update={"status": "published"}))
        self._rules.update_status(published)
        return published

    def list_rules(
        self,
        workspace_id: str,
        actor_id: str,
        *,
        ruleset_version: str | None = None,
        jurisdiction: str | None = None,
        status: str | None = None,
    ) -> list[PolicyRule]:
        self._workspace_management.require_membership(workspace_id, actor_id)
        return cast(
            "list[PolicyRule]",
            self._rules.list_rules(
                workspace_id=workspace_id,
                ruleset_version=ruleset_version,
                jurisdiction=jurisdiction,
                status=status,
            ),
        )

    def evaluate_case(
        self,
        case_id: str,
        actor_id: str,
        *,
        ruleset_version: str,
    ) -> PolicyEvaluationReport:
        case = self._case_management.get_case(case_id, actor_id)
        if case.assessment_date is None:
            raise ValueError("案件必须设置 assessment_date 才能运行规则评估")
        rules = self._rules.list_rules(
            workspace_id=case.workspace_id,
            ruleset_version=ruleset_version,
            jurisdiction=case.jurisdiction,
            status="published",
        )
        facts = self._facts.list_for_case(
            case.case_id,
            statuses={"confirmed"},
        )
        return self._engine.evaluate(
            rules=rules,
            facts=facts,
            assessment_date=case.assessment_date,
            jurisdiction=case.jurisdiction,
            ruleset_version=ruleset_version,
        )

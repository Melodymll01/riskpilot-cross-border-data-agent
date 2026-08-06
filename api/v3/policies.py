"""V3 规则资源与案件评估路由。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, Query, status

from api.v2.deps import make_require_owner
from api.v3.schemas import (
    CreatePolicyRuleRequest,
    EvaluatePolicyRequest,
    PolicyEvaluationOut,
    PolicyEvaluationReportOut,
    PolicyRuleListResponse,
    PolicyRuleOut,
)
from domain.policies import PolicyRule

if TYPE_CHECKING:
    from app.container import AppContainer
    from domain.policies import PolicyEvaluation, PolicyEvaluationReport


def _to_rule_out(rule: PolicyRule) -> PolicyRuleOut:
    return PolicyRuleOut(**rule.model_dump())


def _to_evaluation_out(evaluation: PolicyEvaluation) -> PolicyEvaluationOut:
    return PolicyEvaluationOut(**evaluation.model_dump())


def _to_report_out(report: PolicyEvaluationReport) -> PolicyEvaluationReportOut:
    return PolicyEvaluationReportOut(
        ruleset_version=report.ruleset_version,
        jurisdiction=report.jurisdiction,
        assessment_date=report.assessment_date,
        evaluations=[_to_evaluation_out(item) for item in report.evaluations],
        missing_fact_fields=report.missing_fact_fields,
    )


def build_policy_routes(container: AppContainer) -> APIRouter:
    router = APIRouter(tags=["v3-policies"])
    require_owner = make_require_owner(container)

    @router.post(
        "/workspaces/{workspace_id}/policy-rules",
        response_model=PolicyRuleOut,
        status_code=status.HTTP_201_CREATED,
        summary="创建 draft 规则（Workspace admin）",
    )
    def create_rule(
        workspace_id: str,
        body: CreatePolicyRuleRequest,
        actor_id: str = Depends(require_owner),
    ) -> PolicyRuleOut:
        rule = PolicyRule(
            workspace_id=workspace_id,
            status="draft",
            **body.model_dump(),
        )
        return _to_rule_out(container.policy_management.create_rule(workspace_id, actor_id, rule))

    @router.get(
        "/workspaces/{workspace_id}/policy-rules",
        response_model=PolicyRuleListResponse,
        summary="列出 Workspace 可见规则",
    )
    def list_rules(
        workspace_id: str,
        ruleset_version: str | None = None,
        jurisdiction: str | None = None,
        rule_status: str | None = Query(default=None, alias="status"),
        actor_id: str = Depends(require_owner),
    ) -> PolicyRuleListResponse:
        rules = container.policy_management.list_rules(
            workspace_id,
            actor_id,
            ruleset_version=ruleset_version,
            jurisdiction=jurisdiction,
            status=rule_status,
        )
        return PolicyRuleListResponse(rules=[_to_rule_out(rule) for rule in rules])

    @router.post(
        "/workspaces/{workspace_id}/policy-rules/{rule_id}/{ruleset_version}/publish",
        response_model=PolicyRuleOut,
        summary="发布规则（Workspace admin）",
    )
    def publish_rule(
        workspace_id: str,
        rule_id: str,
        ruleset_version: str,
        actor_id: str = Depends(require_owner),
    ) -> PolicyRuleOut:
        rule = container.policy_management.publish_rule(
            workspace_id,
            actor_id,
            rule_id=rule_id,
            ruleset_version=ruleset_version,
        )
        return _to_rule_out(rule)

    @router.post(
        "/cases/{case_id}/policy-evaluations",
        response_model=PolicyEvaluationReportOut,
        summary="使用 confirmed 事实运行确定性规则评估",
    )
    def evaluate_case(
        case_id: str,
        body: EvaluatePolicyRequest,
        actor_id: str = Depends(require_owner),
    ) -> PolicyEvaluationReportOut:
        report = container.policy_management.evaluate_case(
            case_id,
            actor_id,
            ruleset_version=body.ruleset_version,
        )
        return _to_report_out(report)

    return router

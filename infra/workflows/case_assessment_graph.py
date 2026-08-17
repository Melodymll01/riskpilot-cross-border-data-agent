"""核心 Case Assessment LangGraph 定义。"""

from __future__ import annotations

from contextlib import nullcontext
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from domain.agent_workflow import (
    AgentBudget,
    AgentRuntimeContext,
    EvidencePlan,
    EvidencePlanRequest,
    ToolExecutionResult,
)
from observability_context import observability_context

if TYPE_CHECKING:
    from domain.ports import (
        CaseAssessmentToolPort,
        EvidencePlannerPort,
        MetricsPort,
        TracePort,
    )

GRAPH_STAGES = (
    "load_case",
    "authorize",
    "inspect_documents",
    "build_evidence_plan",
    "retrieve_case_evidence",
    "retrieve_regulations",
    "extract_fact_candidates",
    "detect_missing_facts",
    "detect_fact_conflicts",
    "human_fact_confirmation",
    "select_policy_snapshot",
    "evaluate_deterministic_rules",
    "draft_findings",
    "verify_claim_citations",
    "human_review",
    "finalize_assessment",
)

DEFAULT_TOOL_NAMES = (
    "retrieve_case_evidence",
    "retrieve_regulations",
    "extract_fact_candidates",
    "evaluate_deterministic_rules",
    "verify_claim_citations",
)


class CaseAssessmentState(TypedDict, total=False):
    run_id: str
    case_id: str
    workspace_id: str
    actor_id: str
    actor_role: str
    ruleset_version: str
    ready_document_ids: list[str]
    pending_document_ids: list[str]
    required_fact_fields: list[str]
    missing_fact_fields: list[str]
    evidence_plan: dict[str, Any]
    evidence_query_count: int
    case_evidence_ids: list[str]
    regulation_rule_ids: list[str]
    candidate_fact_ids: list[str]
    conflict_field_names: list[str]
    policy_missing_fact_fields: list[str]
    assessment_id: str
    citations_valid: bool
    review_decision: str
    refusal_reason: str
    budget: dict[str, Any]
    tool_trace: list[dict[str, Any]]


@dataclass(frozen=True)
class GraphDependencies:
    planner: EvidencePlannerPort
    tools: CaseAssessmentToolPort | None
    budget: AgentBudget
    trace: TracePort | None = None
    metrics: MetricsPort | None = None
    model_name: str = "unconfigured"
    input_cost_per_1m_tokens: float = 0.0
    output_cost_per_1m_tokens: float = 0.0


def build_case_assessment_graph(saver: Any, dependencies: GraphDependencies) -> Any:
    builder = StateGraph(CaseAssessmentState)
    builder.add_node("load_case", _node("load_case", _load_case, dependencies))
    builder.add_node("authorize", _node("authorize", _authorize, dependencies))
    builder.add_node(
        "inspect_documents",
        _node("inspect_documents", _inspect_documents, dependencies),
    )
    builder.add_node(
        "build_evidence_plan",
        _node(
            "build_evidence_plan",
            lambda state: _build_evidence_plan(state, dependencies),
            dependencies,
        ),
    )
    builder.add_node(
        "retrieve_case_evidence",
        _node(
            "retrieve_case_evidence",
            lambda state: _retrieve_case_evidence(state, dependencies),
            dependencies,
        ),
    )
    builder.add_node(
        "retrieve_regulations",
        _node(
            "retrieve_regulations",
            lambda state: _retrieve_regulations(state, dependencies),
            dependencies,
        ),
    )
    builder.add_node(
        "extract_fact_candidates",
        _node(
            "extract_fact_candidates",
            lambda state: _extract_fact_candidates(state, dependencies),
            dependencies,
        ),
    )
    builder.add_node(
        "detect_missing_facts",
        _node("detect_missing_facts", _detect_missing_facts, dependencies),
    )
    builder.add_node(
        "detect_fact_conflicts",
        _node("detect_fact_conflicts", _detect_fact_conflicts, dependencies),
    )
    builder.add_node(
        "human_fact_confirmation",
        _node("human_fact_confirmation", _human_fact_confirmation, dependencies),
    )
    builder.add_node(
        "select_policy_snapshot",
        _node("select_policy_snapshot", _select_policy_snapshot, dependencies),
    )
    builder.add_node(
        "evaluate_deterministic_rules",
        _node(
            "evaluate_deterministic_rules",
            lambda state: _evaluate_deterministic_rules(state, dependencies),
            dependencies,
        ),
    )
    builder.add_node(
        "draft_findings",
        _node("draft_findings", _draft_findings, dependencies),
    )
    builder.add_node(
        "verify_claim_citations",
        _node(
            "verify_claim_citations",
            lambda state: _verify_claim_citations(state, dependencies),
            dependencies,
        ),
    )
    builder.add_node("human_review", _node("human_review", _human_review, dependencies))
    builder.add_node(
        "finalize_assessment",
        _node("finalize_assessment", _finalize_assessment, dependencies),
    )
    builder.add_edge(START, "load_case")
    for source, target in zip(GRAPH_STAGES[:4], GRAPH_STAGES[1:5], strict=False):
        builder.add_edge(source, target)
    builder.add_conditional_edges(
        "retrieve_case_evidence",
        _route_after_case_evidence,
        {
            "retrieve_case_evidence": "retrieve_case_evidence",
            "retrieve_regulations": "retrieve_regulations",
        },
    )
    for source, target in zip(GRAPH_STAGES[5:], GRAPH_STAGES[6:], strict=False):
        builder.add_edge(source, target)
    builder.add_edge("finalize_assessment", END)
    return builder.compile(checkpointer=saver, name="riskpilot_case_assessment")


def _node(
    name: str,
    invoke: Any,
    dependencies: GraphDependencies,
) -> Any:
    def wrapped(state: CaseAssessmentState) -> dict[str, Any]:
        started = __import__("time").perf_counter()
        span_manager = (
            dependencies.trace.span(
                f"riskpilot.graph.{name}",
                metadata={
                    "run_id": state.get("run_id", ""),
                    "workspace_id": state.get("workspace_id", ""),
                    "case_id": state.get("case_id", ""),
                    "langgraph_node": name,
                },
            )
            if dependencies.trace is not None
            else nullcontext(None)
        )
        with (
            observability_context(
                run_id=state.get("run_id"),
                workspace_id=state.get("workspace_id"),
                case_id=state.get("case_id"),
                node=name,
            ),
            span_manager as span,
        ):
            try:
                result = invoke(state)
            except Exception as exc:
                if span is not None:
                    span.add_metadata(
                        {
                            "status": "failed",
                            "error_type": type(exc).__name__,
                            "duration_ms": (__import__("time").perf_counter() - started) * 1000,
                        }
                    )
                raise
            if span is not None:
                span.add_metadata(
                    {
                        "status": "completed",
                        "duration_ms": (__import__("time").perf_counter() - started) * 1000,
                    }
                )
            return result

    return wrapped


def _load_case(state: CaseAssessmentState) -> dict[str, Any]:
    _require(state, "run_id", "case_id", "workspace_id", "actor_id", "ruleset_version")
    return {}


def _authorize(state: CaseAssessmentState) -> dict[str, Any]:
    _require(state, "workspace_id", "actor_id", "actor_role")
    if state["actor_role"] not in {"editor", "reviewer", "admin"}:
        raise PermissionError("当前角色不能运行 Case Assessment Agent")
    return {}


def _inspect_documents(state: CaseAssessmentState) -> dict[str, Any]:
    ready = list(state.get("ready_document_ids", []))
    pending = list(state.get("pending_document_ids", []))
    if not ready or pending:
        response = interrupt(
            {
                "kind": "documents_required",
                "ready_document_ids": ready,
                "pending_document_ids": pending,
            }
        )
        if response.get("action") != "retry":
            raise ValueError("documents_required 仅接受 action=retry")
    return {}


def _build_evidence_plan(
    state: CaseAssessmentState,
    dependencies: GraphDependencies,
) -> dict[str, Any]:
    definitions = dependencies.tools.definitions() if dependencies.tools is not None else []
    available_tools = [definition.name for definition in definitions] or list(DEFAULT_TOOL_NAMES)
    planned = dependencies.planner.build_plan(
        EvidencePlanRequest(
            ruleset_version=state["ruleset_version"],
            ready_document_count=len(state.get("ready_document_ids", [])),
            required_fact_fields=list(state.get("required_fact_fields", [])),
            available_tools=available_tools,
        )
    )
    plan = planned.plan
    budget = _budget(state, dependencies)
    if budget.token_usage + planned.token_usage > budget.max_tokens:
        raise ValueError("EvidencePlan 已超过 Agent token budget，安全停止")
    budget = budget.consume_tokens(
        planned.token_usage,
        input_tokens=planned.input_tokens,
        output_tokens=planned.output_tokens,
    )
    if dependencies.metrics is not None and planned.token_usage:
        dependencies.metrics.record_llm_usage(
            operation="build_evidence_plan",
            model=dependencies.model_name,
            input_tokens=planned.input_tokens,
            output_tokens=planned.output_tokens,
            cost=_estimated_cost(
                input_tokens=planned.input_tokens,
                output_tokens=planned.output_tokens,
                dependencies=dependencies,
            ),
        )
    return {
        "evidence_plan": plan.model_dump(mode="json"),
        "evidence_query_count": 0,
        "budget": budget.model_dump(),
    }


def _retrieve_case_evidence(
    state: CaseAssessmentState,
    dependencies: GraphDependencies,
) -> dict[str, Any]:
    plan = EvidencePlan.model_validate(state["evidence_plan"])
    if "retrieve_case_evidence" not in plan.planned_tools:
        return {
            "evidence_query_count": len(plan.investigation_questions),
            "case_evidence_ids": [],
        }
    query_count = state.get("evidence_query_count", 0)
    budget = _budget(state, dependencies)
    if query_count >= len(plan.investigation_questions) or budget.exhausted:
        return {}
    budget = budget.consume_loop()
    query = plan.investigation_questions[query_count]
    result, budget = _execute_tool(
        state,
        dependencies,
        "retrieve_case_evidence",
        {"query": query, "top_k": 10},
        budget=budget,
    )
    if result is None:
        return {
            "evidence_query_count": query_count + 1,
            "case_evidence_ids": _unique_strings(
                *state.get("case_evidence_ids", []),
                *state.get("ready_document_ids", []),
            ),
            "budget": budget.model_dump(),
        }
    return {
        "evidence_query_count": query_count + 1,
        "case_evidence_ids": _unique_strings(
            *state.get("case_evidence_ids", []),
            *_string_output(result, "evidence_ids"),
        ),
        "budget": budget.model_dump(),
        "tool_trace": _append_trace(state, "retrieve_case_evidence", result),
    }


def _route_after_case_evidence(
    state: CaseAssessmentState,
) -> str:
    plan = EvidencePlan.model_validate(state["evidence_plan"])
    budget = AgentBudget.model_validate(state["budget"])
    if (
        "retrieve_case_evidence" in plan.planned_tools
        and state.get("evidence_query_count", 0) < len(plan.investigation_questions)
        and not budget.exhausted
    ):
        return "retrieve_case_evidence"
    return "retrieve_regulations"


def _retrieve_regulations(
    state: CaseAssessmentState,
    dependencies: GraphDependencies,
) -> dict[str, Any]:
    result, budget = _execute_tool(
        state,
        dependencies,
        "retrieve_regulations",
        {"ruleset_version": state["ruleset_version"]},
    )
    if result is None:
        return {
            "regulation_rule_ids": [f"protocol:{state['ruleset_version']}:snapshot"],
            "budget": budget.model_dump(),
        }
    return {
        "regulation_rule_ids": _string_output(result, "rule_ids"),
        "budget": budget.model_dump(),
        "tool_trace": _append_trace(state, "retrieve_regulations", result),
    }


def _extract_fact_candidates(
    state: CaseAssessmentState,
    dependencies: GraphDependencies,
) -> dict[str, Any]:
    missing = list(state.get("missing_fact_fields", []))
    if not missing:
        return {}
    plan = EvidencePlan.model_validate(state["evidence_plan"])
    if "extract_fact_candidates" not in plan.planned_tools:
        return {
            "refusal_reason": (
                "EvidencePlan 未授权事实提取工具，拒绝猜测缺失事实；请人工补充或确认。"
            )
        }
    current_budget = _budget(state, dependencies)
    if current_budget.token_usage >= current_budget.max_tokens:
        raise ValueError("Agent token budget 已耗尽，拒绝调用事实提议模型")
    result, budget = _execute_tool(
        state,
        dependencies,
        "extract_fact_candidates",
        {
            "field_names": missing,
            "document_ids": list(state.get("ready_document_ids", [])) or None,
        },
        budget=current_budget,
    )
    if result is None:
        return {"budget": budget.model_dump()}
    budget = budget.consume_tokens(
        result.token_usage,
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
    )
    candidate_ids = _string_output(result, "fact_ids")
    if not candidate_ids:
        refusal = "当前材料未能形成可验证事实候选，拒绝猜测事实；请补充材料或人工事实。"
    else:
        refusal = ""
    return {
        "candidate_fact_ids": candidate_ids,
        "conflict_field_names": _unique_strings(
            *state.get("conflict_field_names", []),
            *_string_output(result, "conflict_field_names"),
        ),
        "refusal_reason": refusal,
        "budget": budget.model_dump(),
        "tool_trace": _append_trace(state, "extract_fact_candidates", result),
    }


def _detect_missing_facts(state: CaseAssessmentState) -> dict[str, Any]:
    return {
        "missing_fact_fields": list(state.get("missing_fact_fields", [])),
    }


def _detect_fact_conflicts(state: CaseAssessmentState) -> dict[str, Any]:
    conflicts = list(state.get("conflict_field_names", []))
    if conflicts:
        response = interrupt(
            {
                "kind": "fact_conflict_review",
                "conflict_field_names": conflicts,
                "candidate_fact_ids": list(state.get("candidate_fact_ids", [])),
            }
        )
        if response.get("action") != "retry":
            raise ValueError("fact_conflict_review 仅接受 action=retry")
    return {}


def _human_fact_confirmation(state: CaseAssessmentState) -> dict[str, Any]:
    missing = list(state.get("missing_fact_fields", []))
    if missing:
        response = interrupt(
            {
                "kind": "fact_confirmation",
                "missing_fact_fields": missing,
                "candidate_fact_ids": list(state.get("candidate_fact_ids", [])),
                "safe_refusal_reason": state.get("refusal_reason", ""),
            }
        )
        if response.get("action") != "retry":
            raise ValueError("fact_confirmation 仅接受 action=retry")
    return {}


def _select_policy_snapshot(state: CaseAssessmentState) -> dict[str, Any]:
    _require(state, "ruleset_version", "regulation_rule_ids")
    return {}


def _evaluate_deterministic_rules(
    state: CaseAssessmentState,
    dependencies: GraphDependencies,
) -> dict[str, Any]:
    result, budget = _execute_tool(
        state,
        dependencies,
        "evaluate_deterministic_rules",
        {"ruleset_version": state["ruleset_version"]},
    )
    if result is None:
        return {"budget": budget.model_dump()}
    missing = _string_output(result, "missing_fact_fields")
    if missing:
        raise ValueError("确定性规则评估仍存在未确认事实")
    return {
        "policy_missing_fact_fields": missing,
        "budget": budget.model_dump(),
        "tool_trace": _append_trace(state, "evaluate_deterministic_rules", result),
    }


def _draft_findings(state: CaseAssessmentState) -> dict[str, Any]:
    response = interrupt(
        {
            "kind": "assessment_generation",
            "case_id": state["case_id"],
            "ruleset_version": state["ruleset_version"],
        }
    )
    assessment_id = response.get("assessment_id")
    if not isinstance(assessment_id, str) or not assessment_id:
        raise ValueError("assessment_generation 必须返回 assessment_id")
    return {"assessment_id": assessment_id}


def _verify_claim_citations(
    state: CaseAssessmentState,
    dependencies: GraphDependencies,
) -> dict[str, Any]:
    result, budget = _execute_tool(
        state,
        dependencies,
        "verify_claim_citations",
        {"assessment_id": state["assessment_id"]},
    )
    if result is None:
        return {"citations_valid": True, "budget": budget.model_dump()}
    valid = bool(result.output.get("valid"))
    if not valid:
        if dependencies.metrics is not None:
            dependencies.metrics.record_citation_failure(workflow="case_assessment")
        raise ValueError("Assessment Claim-Citation 校验失败")
    return {
        "citations_valid": valid,
        "budget": budget.model_dump(),
        "tool_trace": _append_trace(state, "verify_claim_citations", result),
    }


def _human_review(state: CaseAssessmentState) -> dict[str, Any]:
    if not state.get("citations_valid"):
        raise ValueError("引用校验未通过，不能进入 Reviewer")
    response = interrupt(
        {
            "kind": "assessment_review",
            "assessment_id": state["assessment_id"],
        }
    )
    decision = response.get("decision")
    if decision not in {"approved", "rejected"}:
        raise ValueError("assessment_review 仅接受 approved 或 rejected")
    return {"review_decision": decision}


def _finalize_assessment(state: CaseAssessmentState) -> dict[str, Any]:
    _require(state, "assessment_id", "review_decision", "citations_valid")
    return {}


def _execute_tool(
    state: CaseAssessmentState,
    dependencies: GraphDependencies,
    name: str,
    arguments: dict[str, Any],
    *,
    budget: AgentBudget | None = None,
) -> tuple[ToolExecutionResult | None, AgentBudget]:
    current_budget = budget or _budget(state, dependencies)
    if current_budget.tool_calls >= current_budget.max_tool_calls:
        raise ValueError("Agent budget 已耗尽，安全停止")
    current_budget = current_budget.consume_tool()
    if dependencies.tools is None:
        return None, current_budget
    context = AgentRuntimeContext(
        run_id=state["run_id"],
        workspace_id=state["workspace_id"],
        case_id=state["case_id"],
        actor_id=state["actor_id"],
        actor_role=state["actor_role"],
        workflow_stage=name,
        remaining_token_budget=max(0, current_budget.max_tokens - current_budget.token_usage),
    )
    return dependencies.tools.execute(name, arguments, context=context), current_budget


def _budget(
    state: CaseAssessmentState,
    dependencies: GraphDependencies,
) -> AgentBudget:
    value = state.get("budget")
    return AgentBudget.model_validate(value) if value else dependencies.budget


def _append_trace(
    state: CaseAssessmentState,
    stage: str,
    result: ToolExecutionResult,
) -> list[dict[str, Any]]:
    return [
        *list(state.get("tool_trace", [])),
        {
            "stage": stage,
            "tool_name": result.tool_name,
            "arguments": result.arguments,
            "result_summary": result.result_summary,
            "duration_ms": result.duration_ms,
            "retry_count": result.retry_count,
            "input_tokens": result.input_tokens,
            "output_tokens": result.output_tokens,
            "token_usage": result.token_usage,
            "output": result.output,
        },
    ]


def _string_output(result: ToolExecutionResult, key: str) -> list[str]:
    value = result.output.get(key, [])
    if not isinstance(value, list):
        raise ValueError(f"工具 {result.tool_name} 输出 {key} 必须是数组")
    return [str(item) for item in value]


def _unique_strings(*values: str) -> list[str]:
    return list(dict.fromkeys(values))


def _require(state: CaseAssessmentState, *fields: str) -> None:
    missing = [field for field in fields if not state.get(field)]
    if missing:
        raise ValueError("LangGraph state 缺少字段: " + ", ".join(missing))


def _estimated_cost(
    *,
    input_tokens: int,
    output_tokens: int,
    dependencies: GraphDependencies,
) -> float:
    return (
        input_tokens * dependencies.input_cost_per_1m_tokens
        + output_tokens * dependencies.output_cost_per_1m_tokens
    ) / 1_000_000

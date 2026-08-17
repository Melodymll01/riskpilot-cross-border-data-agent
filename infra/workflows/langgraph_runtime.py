"""LangGraph 案件评估工作流适配器。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from langgraph.types import Command, Interrupt

from domain.agent_workflow import AgentBudget
from domain.runs import (
    CaseDocumentReadiness,
    WorkflowExecutionResult,
    WorkflowInterrupt,
    WorkflowInterruptKind,
)
from infra.agents import DeterministicEvidencePlanner
from infra.observability import NoopMetricsAdapter, NoopTraceAdapter
from infra.workflows.case_assessment_graph import (
    GRAPH_STAGES,
    CaseAssessmentState,
    GraphDependencies,
    build_case_assessment_graph,
)
from infra.workflows.checkpoint_store import CheckpointBackend, CheckpointStore

if TYPE_CHECKING:
    from domain.ports import (
        CaseAssessmentToolPort,
        EvidencePlannerPort,
        MetricsPort,
        TracePort,
    )

_GRAPH_STAGES = GRAPH_STAGES


class LangGraphWorkflowRuntime:
    """使用 LangGraph 原生 checkpoint 和 interrupt/Command 执行案件评估。"""

    def __init__(
        self,
        checkpoint_db_path: str,
        *,
        checkpoint_backend: CheckpointBackend = "sqlite",
        database_url: str | None = None,
        trace: TracePort | None = None,
        planner: EvidencePlannerPort | None = None,
        tools: CaseAssessmentToolPort | None = None,
        budget: AgentBudget | None = None,
        metrics: MetricsPort | None = None,
        model_name: str = "unconfigured",
        input_cost_per_1m_tokens: float = 0.0,
        output_cost_per_1m_tokens: float = 0.0,
    ) -> None:
        self._checkpoint_store = CheckpointStore(
            backend=checkpoint_backend,
            sqlite_path=checkpoint_db_path,
            database_url=database_url,
        )
        self._default_budget = budget or AgentBudget()
        self._dependencies = GraphDependencies(
            planner=planner or DeterministicEvidencePlanner(),
            tools=tools,
            budget=self._default_budget,
            trace=trace,
            metrics=metrics,
            model_name=model_name,
            input_cost_per_1m_tokens=input_cost_per_1m_tokens,
            output_cost_per_1m_tokens=output_cost_per_1m_tokens,
        )
        self._compiled_graph: Any | None = None
        self._trace = trace or NoopTraceAdapter()
        self._metrics = metrics or NoopMetricsAdapter()

    def close(self) -> None:
        self._checkpoint_store.close()
        self._compiled_graph = None

    def initialize(self) -> None:
        self._graph()

    def _graph(self) -> Any:
        if self._compiled_graph is None:
            self._compiled_graph = build_case_assessment_graph(
                self._checkpoint_store.saver,
                self._dependencies,
            )
        return self._compiled_graph

    def inspect_case_assessment(
        self,
        *,
        thread_id: str,
    ) -> WorkflowExecutionResult | None:
        config = _config(thread_id)
        snapshot = self._graph().get_state(config)
        if not snapshot.values and not snapshot.next:
            return None
        return self._result(config, [])

    def start_case_assessment(
        self,
        *,
        thread_id: str,
        case_id: str,
        workspace_id: str,
        actor_id: str,
        ruleset_version: str,
        document_readiness: CaseDocumentReadiness,
        missing_fact_fields: list[str],
        conflict_field_names: list[str] | None = None,
        run_id: str | None = None,
        actor_role: str = "editor",
        required_fact_fields: list[str] | None = None,
        max_loop_count: int | None = None,
        max_tool_calls: int | None = None,
        max_tokens: int | None = None,
    ) -> WorkflowExecutionResult:
        with self._trace.span(
            "riskpilot.case_assessment.start",
            metadata={
                "actor_id": actor_id,
                "case_id": case_id,
                "framework": "langgraph",
                "missing_fact_count": len(missing_fact_fields),
                "pending_document_count": len(document_readiness.pending_document_ids),
                "ready_document_count": len(document_readiness.ready_document_ids),
                "ruleset_version": ruleset_version,
                "thread_id": thread_id,
                "workflow": "case_assessment",
                "workspace_id": workspace_id,
            },
        ) as span:
            started = __import__("time").perf_counter()
            try:
                result = self._start_case_assessment(
                    thread_id=thread_id,
                    case_id=case_id,
                    workspace_id=workspace_id,
                    actor_id=actor_id,
                    ruleset_version=ruleset_version,
                    document_readiness=document_readiness,
                    missing_fact_fields=missing_fact_fields,
                    conflict_field_names=conflict_field_names,
                    run_id=run_id,
                    actor_role=actor_role,
                    required_fact_fields=required_fact_fields,
                    max_loop_count=max_loop_count,
                    max_tool_calls=max_tool_calls,
                    max_tokens=max_tokens,
                )
            except Exception as exc:
                span.add_metadata({"error_type": type(exc).__name__, "status": "failed"})
                self._metrics.observe_agent_run(
                    workflow="case_assessment",
                    status="failed",
                    duration_seconds=__import__("time").perf_counter() - started,
                    token_usage=0,
                    cost=0.0,
                    refused=False,
                )
                raise
            span.add_metadata(_result_metadata(result))
            budget_state = result.state.get("budget", {})
            token_usage = (
                int(budget_state.get("token_usage", 0)) if isinstance(budget_state, dict) else 0
            )
            self._metrics.observe_agent_run(
                workflow="case_assessment",
                status=result.status,
                duration_seconds=__import__("time").perf_counter() - started,
                token_usage=token_usage,
                cost=0.0,
                refused=bool(result.state.get("refusal_reason")),
            )
            return result

    def _start_case_assessment(
        self,
        *,
        thread_id: str,
        case_id: str,
        workspace_id: str,
        actor_id: str,
        ruleset_version: str,
        document_readiness: CaseDocumentReadiness,
        missing_fact_fields: list[str],
        conflict_field_names: list[str] | None,
        run_id: str | None,
        actor_role: str,
        required_fact_fields: list[str] | None,
        max_loop_count: int | None,
        max_tool_calls: int | None,
        max_tokens: int | None,
    ) -> WorkflowExecutionResult:
        if not thread_id.strip():
            raise ValueError("thread_id 必填")
        if len(missing_fact_fields) != len(set(missing_fact_fields)):
            raise ValueError("missing_fact_fields 不能重复")
        config = _config(thread_id)
        graph = self._graph()
        existing = graph.get_state(config)
        if existing.values or existing.next:
            raise ValueError(f"LangGraph thread {thread_id!r} 已存在")
        state: CaseAssessmentState = {
            "run_id": run_id or thread_id,
            "case_id": case_id,
            "workspace_id": workspace_id,
            "actor_id": actor_id,
            "actor_role": actor_role,
            "ruleset_version": ruleset_version,
            "ready_document_ids": list(document_readiness.ready_document_ids),
            "pending_document_ids": list(document_readiness.pending_document_ids),
            "required_fact_fields": list(required_fact_fields or missing_fact_fields),
            "missing_fact_fields": list(missing_fact_fields),
            "conflict_field_names": list(conflict_field_names or []),
            "budget": self._default_budget.model_copy(
                update={
                    "max_loop_count": (
                        self._default_budget.max_loop_count
                        if max_loop_count is None
                        else max_loop_count
                    ),
                    "max_tool_calls": (
                        self._default_budget.max_tool_calls
                        if max_tool_calls is None
                        else max_tool_calls
                    ),
                    "max_tokens": (
                        self._default_budget.max_tokens if max_tokens is None else max_tokens
                    ),
                    "loop_count": 0,
                    "tool_calls": 0,
                    "token_usage": 0,
                }
            ).model_dump(),
            "tool_trace": [],
            "node_trace": [],
        }
        completed_stages = _consume_updates(
            graph.stream(state, config=config, stream_mode="updates")
        )
        return self._result(config, completed_stages)

    def resume_case_assessment(
        self,
        *,
        thread_id: str,
        resume_value: dict[str, Any],
        state_update: dict[str, Any] | None = None,
        actor_id: str | None = None,
        actor_role: str | None = None,
    ) -> WorkflowExecutionResult:
        with self._trace.span(
            "riskpilot.case_assessment.resume",
            metadata={
                "framework": "langgraph",
                "resumed": True,
                "thread_id": thread_id,
                "workflow": "case_assessment",
            },
        ) as span:
            started = __import__("time").perf_counter()
            try:
                result = self._resume_case_assessment(
                    thread_id=thread_id,
                    resume_value=resume_value,
                    state_update=state_update,
                    actor_id=actor_id,
                    actor_role=actor_role,
                )
            except Exception as exc:
                span.add_metadata({"error_type": type(exc).__name__, "status": "failed"})
                self._metrics.observe_agent_run(
                    workflow="case_assessment",
                    status="failed",
                    duration_seconds=__import__("time").perf_counter() - started,
                    token_usage=0,
                    cost=0.0,
                    refused=False,
                )
                raise
            span.add_metadata(_result_metadata(result))
            budget_state = result.state.get("budget", {})
            token_usage = (
                int(budget_state.get("token_usage", 0)) if isinstance(budget_state, dict) else 0
            )
            self._metrics.observe_agent_run(
                workflow="case_assessment",
                status=result.status,
                duration_seconds=__import__("time").perf_counter() - started,
                token_usage=token_usage,
                cost=0.0,
                refused=bool(result.state.get("refusal_reason")),
            )
            return result

    def _resume_case_assessment(
        self,
        *,
        thread_id: str,
        resume_value: dict[str, Any],
        state_update: dict[str, Any] | None = None,
        actor_id: str | None,
        actor_role: str | None,
    ) -> WorkflowExecutionResult:
        config = _config(thread_id)
        graph = self._graph()
        snapshot = graph.get_state(config)
        active_interrupt = _active_interrupt(snapshot.tasks)
        if active_interrupt is None:
            raise ValueError(f"LangGraph thread {thread_id!r} 当前没有可恢复中断")
        kind = _interrupt_kind(active_interrupt)
        sanitized = _validate_resume_value(kind, resume_value)
        sanitized_update = _validate_state_update(kind, state_update or {})
        if (actor_id is None) != (actor_role is None):
            raise ValueError("恢复执行身份必须同时提供 actor_id 和 actor_role")
        if actor_id is not None and actor_role is not None:
            if actor_role not in {"editor", "reviewer", "admin"}:
                raise ValueError("恢复执行身份角色非法")
            sanitized_update.update(
                {
                    "actor_id": actor_id,
                    "actor_role": actor_role,
                }
            )
        completed_stages = _consume_updates(
            graph.stream(
                Command(resume=sanitized, update=sanitized_update or None),
                config=config,
                stream_mode="updates",
            )
        )
        return self._result(config, completed_stages)

    def _result(
        self,
        config: dict[str, dict[str, str]],
        completed_stages: list[str],
    ) -> WorkflowExecutionResult:
        snapshot = self._graph().get_state(config)
        configurable = snapshot.config.get("configurable", {})
        checkpoint_id = configurable.get("checkpoint_id")
        if not isinstance(checkpoint_id, str) or not checkpoint_id:
            raise RuntimeError("LangGraph 未返回 checkpoint_id")
        active_interrupt = _active_interrupt(snapshot.tasks)
        if active_interrupt is not None:
            workflow_interrupt = _to_workflow_interrupt(active_interrupt)
            return WorkflowExecutionResult(
                status="interrupted",
                checkpoint_id=checkpoint_id,
                stage=_stage_for_interrupt(workflow_interrupt.kind),
                state=_safe_state(snapshot.values),
                completed_stages=completed_stages,
                interrupt=workflow_interrupt,
            )
        if snapshot.next:
            raise RuntimeError("LangGraph 执行未完成且未产生 interrupt")
        return WorkflowExecutionResult(
            status="completed",
            checkpoint_id=checkpoint_id,
            stage="complete",
            state=_safe_state(snapshot.values),
            completed_stages=completed_stages,
        )


def _result_metadata(result: WorkflowExecutionResult) -> dict[str, Any]:
    return {
        "completed": result.status == "completed",
        "completed_stage_count": len(result.completed_stages),
        "interrupt_kind": result.interrupt.kind if result.interrupt else "",
        "interrupted": result.status == "interrupted",
        "stage": result.stage,
        "status": result.status,
    }


def _config(thread_id: str) -> dict[str, dict[str, str]]:
    return {"configurable": {"thread_id": thread_id}}


def _consume_updates(chunks: Any) -> list[str]:
    completed: list[str] = []
    for chunk in chunks:
        if not isinstance(chunk, dict):
            continue
        for node_name in chunk:
            if node_name in _GRAPH_STAGES and node_name not in completed:
                completed.append(node_name)
    return completed


def _active_interrupt(tasks: Any) -> Interrupt | None:
    for task in tasks:
        interrupts = getattr(task, "interrupts", ())
        if interrupts:
            return cast("Interrupt", interrupts[0])
    return None


def _interrupt_kind(value: Interrupt) -> WorkflowInterruptKind:
    payload = value.value
    if not isinstance(payload, dict):
        raise RuntimeError("LangGraph interrupt payload 必须是对象")
    kind = payload.get("kind")
    if kind not in {
        "documents_required",
        "fact_confirmation",
        "fact_conflict_review",
        "assessment_generation",
        "assessment_review",
    }:
        raise RuntimeError(f"未知 LangGraph interrupt kind: {kind!r}")
    return cast("WorkflowInterruptKind", kind)


def _to_workflow_interrupt(value: Interrupt) -> WorkflowInterrupt:
    kind = _interrupt_kind(value)
    payload = dict(value.value)
    payload.pop("kind", None)
    return WorkflowInterrupt(kind=kind, payload=payload)


def _validate_resume_value(
    kind: WorkflowInterruptKind,
    value: dict[str, Any],
) -> dict[str, Any]:
    allowed: dict[str, set[str]] = {
        "documents_required": {"action"},
        "fact_confirmation": {"action"},
        "fact_conflict_review": {"action"},
        "assessment_generation": {"assessment_id"},
        "assessment_review": {"decision"},
    }
    unknown = set(value) - allowed[kind]
    if unknown:
        fields = ", ".join(sorted(unknown))
        raise ValueError(f"{kind} 恢复参数包含非法字段: {fields}")
    if kind in {"documents_required", "fact_confirmation", "fact_conflict_review"}:
        if value != {"action": "retry"}:
            raise ValueError(f"{kind} 仅接受 action=retry")
    elif kind == "assessment_generation":
        assessment_id = value.get("assessment_id")
        if not isinstance(assessment_id, str) or not assessment_id.strip():
            raise ValueError("assessment_generation 必须提供 assessment_id")
    elif value.get("decision") not in {"approved", "rejected"}:
        raise ValueError("assessment_review 仅接受 approved 或 rejected")
    return dict(value)


def _validate_state_update(
    kind: WorkflowInterruptKind,
    value: dict[str, Any],
) -> dict[str, Any]:
    allowed: dict[WorkflowInterruptKind, set[str]] = {
        "documents_required": {"ready_document_ids", "pending_document_ids"},
        "fact_confirmation": {"missing_fact_fields", "candidate_fact_ids"},
        "fact_conflict_review": {"conflict_field_names", "missing_fact_fields"},
        "assessment_generation": set(),
        "assessment_review": set(),
    }
    unknown = set(value) - allowed[kind]
    if unknown:
        fields = ", ".join(sorted(unknown))
        raise ValueError(f"{kind} 状态更新包含非法字段: {fields}")
    if kind == "documents_required":
        readiness = CaseDocumentReadiness(
            ready_document_ids=_string_list(value.get("ready_document_ids", [])),
            pending_document_ids=_string_list(value.get("pending_document_ids", [])),
        )
        if readiness.blocked:
            raise ValueError("documents_required 恢复前必须至少有一个 ready 文档且无 pending 文档")
        return readiness.model_dump()
    if kind == "fact_confirmation":
        missing_fields = _string_list(value.get("missing_fact_fields", []))
        if len(missing_fields) != len(set(missing_fields)):
            raise ValueError("missing_fact_fields 不能重复")
        if missing_fields:
            raise ValueError("fact_confirmation 恢复前 missing_fact_fields 必须为空")
        candidate_ids = _string_list(value.get("candidate_fact_ids", []))
        return {
            "missing_fact_fields": missing_fields,
            "candidate_fact_ids": candidate_ids,
        }
    if kind == "fact_conflict_review":
        conflicts = _string_list(value.get("conflict_field_names", []))
        if conflicts:
            raise ValueError("fact_conflict_review 恢复前 conflict_field_names 必须为空")
        return {
            "conflict_field_names": conflicts,
            "missing_fact_fields": _string_list(value.get("missing_fact_fields", [])),
        }
    if value:
        raise ValueError(f"{kind} 不接受状态更新")
    return {}


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item.strip() for item in value
    ):
        raise ValueError("状态更新字段必须是非空字符串数组")
    return list(value)


def _safe_state(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise RuntimeError("LangGraph state 必须是对象")
    allowed = {
        "case_id",
        "run_id",
        "workspace_id",
        "actor_id",
        "actor_role",
        "ruleset_version",
        "ready_document_ids",
        "pending_document_ids",
        "required_fact_fields",
        "missing_fact_fields",
        "evidence_plan",
        "evidence_query_count",
        "case_evidence_ids",
        "regulation_rule_ids",
        "candidate_fact_ids",
        "conflict_field_names",
        "policy_missing_fact_fields",
        "assessment_id",
        "citations_valid",
        "review_decision",
        "refusal_reason",
        "budget",
        "tool_trace",
        "node_trace",
    }
    safe: dict[str, Any] = {}
    for key in allowed:
        if key in value:
            safe[key] = value[key]
    return safe


def _stage_for_interrupt(kind: WorkflowInterruptKind) -> str:
    stages: dict[WorkflowInterruptKind, str] = {
        "documents_required": "inspect_documents",
        "fact_confirmation": "human_fact_confirmation",
        "fact_conflict_review": "detect_fact_conflicts",
        "assessment_generation": "draft_findings",
        "assessment_review": "human_review",
    }
    return stages[kind]

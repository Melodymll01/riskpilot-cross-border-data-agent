"""LangGraph 案件评估工作流适配器。"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypedDict, cast

from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, Interrupt, interrupt

from domain.runs import (
    CaseDocumentReadiness,
    WorkflowExecutionResult,
    WorkflowInterrupt,
    WorkflowInterruptKind,
)
from infra.observability import NoopTraceAdapter

if TYPE_CHECKING:
    from domain.ports import TracePort

_GRAPH_STAGES = (
    "load_case",
    "authorize",
    "validate_documents",
    "detect_missing_facts",
    "select_policy_snapshot",
    "evaluate_policy_rules",
    "draft_assessment",
    "human_review",
    "complete",
)


class _CaseAssessmentState(TypedDict, total=False):
    case_id: str
    workspace_id: str
    actor_id: str
    ruleset_version: str
    ready_document_ids: list[str]
    pending_document_ids: list[str]
    missing_fact_fields: list[str]
    assessment_id: str
    review_decision: str


class LangGraphWorkflowRuntime:
    """使用 LangGraph 原生 checkpoint 和 interrupt/Command 执行案件评估。"""

    def __init__(
        self,
        checkpoint_db_path: str,
        *,
        trace: TracePort | None = None,
    ) -> None:
        if checkpoint_db_path == ":memory:":
            connection_target = checkpoint_db_path
        else:
            path = Path(checkpoint_db_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            connection_target = str(path)
        self._connection = sqlite3.connect(connection_target, check_same_thread=False)
        self._saver = SqliteSaver(
            self._connection,
            serde=JsonPlusSerializer(pickle_fallback=False),
        )
        self._saver.setup()
        self._graph = _build_case_assessment_graph(self._saver)
        self._trace = trace or NoopTraceAdapter()

    def inspect_case_assessment(
        self,
        *,
        thread_id: str,
    ) -> WorkflowExecutionResult | None:
        config = _config(thread_id)
        snapshot = self._graph.get_state(config)
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
    ) -> WorkflowExecutionResult:
        with self._trace.span(
            "riskpilot.case_assessment.start",
            metadata={
                "actor_id": actor_id,
                "case_id": case_id,
                "framework": "langgraph",
                "missing_fact_count": len(missing_fact_fields),
                "pending_document_count": len(
                    document_readiness.pending_document_ids
                ),
                "ready_document_count": len(document_readiness.ready_document_ids),
                "ruleset_version": ruleset_version,
                "thread_id": thread_id,
                "workflow": "case_assessment",
                "workspace_id": workspace_id,
            },
        ) as span:
            try:
                result = self._start_case_assessment(
                    thread_id=thread_id,
                    case_id=case_id,
                    workspace_id=workspace_id,
                    actor_id=actor_id,
                    ruleset_version=ruleset_version,
                    document_readiness=document_readiness,
                    missing_fact_fields=missing_fact_fields,
                )
            except Exception as exc:
                span.add_metadata(
                    {"error_type": type(exc).__name__, "status": "failed"}
                )
                raise
            span.add_metadata(_result_metadata(result))
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
    ) -> WorkflowExecutionResult:
        if not thread_id.strip():
            raise ValueError("thread_id 必填")
        if len(missing_fact_fields) != len(set(missing_fact_fields)):
            raise ValueError("missing_fact_fields 不能重复")
        config = _config(thread_id)
        existing = self._graph.get_state(config)
        if existing.values or existing.next:
            raise ValueError(f"LangGraph thread {thread_id!r} 已存在")
        state: _CaseAssessmentState = {
            "case_id": case_id,
            "workspace_id": workspace_id,
            "actor_id": actor_id,
            "ruleset_version": ruleset_version,
            "ready_document_ids": list(document_readiness.ready_document_ids),
            "pending_document_ids": list(document_readiness.pending_document_ids),
            "missing_fact_fields": list(missing_fact_fields),
        }
        completed_stages = _consume_updates(
            self._graph.stream(state, config=config, stream_mode="updates")
        )
        return self._result(config, completed_stages)

    def resume_case_assessment(
        self,
        *,
        thread_id: str,
        resume_value: dict[str, Any],
        state_update: dict[str, Any] | None = None,
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
            try:
                result = self._resume_case_assessment(
                    thread_id=thread_id,
                    resume_value=resume_value,
                    state_update=state_update,
                )
            except Exception as exc:
                span.add_metadata(
                    {"error_type": type(exc).__name__, "status": "failed"}
                )
                raise
            span.add_metadata(_result_metadata(result))
            return result

    def _resume_case_assessment(
        self,
        *,
        thread_id: str,
        resume_value: dict[str, Any],
        state_update: dict[str, Any] | None = None,
    ) -> WorkflowExecutionResult:
        config = _config(thread_id)
        snapshot = self._graph.get_state(config)
        active_interrupt = _active_interrupt(snapshot.tasks)
        if active_interrupt is None:
            raise ValueError(f"LangGraph thread {thread_id!r} 当前没有可恢复中断")
        kind = _interrupt_kind(active_interrupt)
        sanitized = _validate_resume_value(kind, resume_value)
        sanitized_update = _validate_state_update(kind, state_update or {})
        completed_stages = _consume_updates(
            self._graph.stream(
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
        snapshot = self._graph.get_state(config)
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


def _build_case_assessment_graph(saver: SqliteSaver) -> Any:
    builder = StateGraph(_CaseAssessmentState)
    builder.add_node("load_case", _load_case)
    builder.add_node("authorize", _authorize)
    builder.add_node("validate_documents", _validate_documents)
    builder.add_node("detect_missing_facts", _detect_missing_facts)
    builder.add_node("select_policy_snapshot", _select_policy_snapshot)
    builder.add_node("evaluate_policy_rules", _evaluate_policy_rules)
    builder.add_node("draft_assessment", _draft_assessment)
    builder.add_node("human_review", _human_review)
    builder.add_node("complete", _complete)
    builder.add_edge(START, "load_case")
    for source, target in zip(_GRAPH_STAGES, _GRAPH_STAGES[1:], strict=False):
        builder.add_edge(source, target)
    builder.add_edge("complete", END)
    return builder.compile(checkpointer=saver, name="riskpilot_case_assessment")


def _result_metadata(result: WorkflowExecutionResult) -> dict[str, Any]:
    return {
        "completed": result.status == "completed",
        "completed_stage_count": len(result.completed_stages),
        "interrupt_kind": result.interrupt.kind if result.interrupt else "",
        "interrupted": result.status == "interrupted",
        "stage": result.stage,
        "status": result.status,
    }


def _load_case(state: _CaseAssessmentState) -> dict[str, Any]:
    _require_state_fields(state, "case_id", "workspace_id", "actor_id", "ruleset_version")
    return {}


def _authorize(state: _CaseAssessmentState) -> dict[str, Any]:
    _require_state_fields(state, "workspace_id", "actor_id")
    return {}


def _validate_documents(state: _CaseAssessmentState) -> dict[str, Any]:
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


def _detect_missing_facts(state: _CaseAssessmentState) -> dict[str, Any]:
    missing = list(state.get("missing_fact_fields", []))
    if missing:
        response = interrupt(
            {
                "kind": "fact_confirmation",
                "missing_fact_fields": missing,
            }
        )
        if response.get("action") != "retry":
            raise ValueError("fact_confirmation 仅接受 action=retry")
    return {}


def _select_policy_snapshot(state: _CaseAssessmentState) -> dict[str, Any]:
    _require_state_fields(state, "ruleset_version")
    return {}


def _evaluate_policy_rules(state: _CaseAssessmentState) -> dict[str, Any]:
    _require_state_fields(state, "case_id", "ruleset_version")
    return {}


def _draft_assessment(state: _CaseAssessmentState) -> dict[str, Any]:
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


def _human_review(state: _CaseAssessmentState) -> dict[str, Any]:
    assessment_id = state.get("assessment_id")
    if not assessment_id:
        raise ValueError("human_review 缺少 assessment_id")
    response = interrupt(
        {
            "kind": "assessment_review",
            "assessment_id": assessment_id,
        }
    )
    decision = response.get("decision")
    if decision not in {"approved", "rejected"}:
        raise ValueError("assessment_review 仅接受 approved 或 rejected")
    return {"review_decision": decision}


def _complete(state: _CaseAssessmentState) -> dict[str, Any]:
    _require_state_fields(state, "assessment_id", "review_decision")
    return {}


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
        "assessment_generation": {"assessment_id"},
        "assessment_review": {"decision"},
    }
    unknown = set(value) - allowed[kind]
    if unknown:
        fields = ", ".join(sorted(unknown))
        raise ValueError(f"{kind} 恢复参数包含非法字段: {fields}")
    if kind in {"documents_required", "fact_confirmation"}:
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
        "fact_confirmation": {"missing_fact_fields"},
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
        return {"missing_fact_fields": missing_fields}
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
        "workspace_id",
        "actor_id",
        "ruleset_version",
        "ready_document_ids",
        "pending_document_ids",
        "missing_fact_fields",
        "assessment_id",
        "review_decision",
    }
    safe: dict[str, Any] = {}
    for key in allowed:
        if key in value:
            safe[key] = value[key]
    return safe


def _stage_for_interrupt(kind: WorkflowInterruptKind) -> str:
    stages: dict[WorkflowInterruptKind, str] = {
        "documents_required": "validate_documents",
        "fact_confirmation": "detect_missing_facts",
        "assessment_generation": "draft_assessment",
        "assessment_review": "human_review",
    }
    return stages[kind]


def _require_state_fields(state: _CaseAssessmentState, *fields: str) -> None:
    missing = [field for field in fields if not state.get(field)]
    if missing:
        raise ValueError(f"LangGraph state 缺少字段: {', '.join(missing)}")

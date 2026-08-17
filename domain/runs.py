"""V2 Agent Run、轻量检查点与可审计事件模型。"""

from __future__ import annotations

import json
import time
from typing import Any, ClassVar, Literal

from pydantic import Field, model_validator

from domain.errors import InvalidAgentRunTransition
from domain.models import BaseDomainModel

WorkflowType = Literal["case_assessment", "deep_research"]
AgentRunStatus = Literal[
    "queued",
    "running",
    "waiting_for_user",
    "waiting_for_review",
    "retrying",
    "completed",
    "failed",
    "cancelled",
]
WorkflowExecutionStatus = Literal["interrupted", "completed"]
WorkflowInterruptKind = Literal[
    "documents_required",
    "fact_confirmation",
    "fact_conflict_review",
    "assessment_generation",
    "assessment_review",
]
RunEventType = Literal[
    "run_started",
    "stage_started",
    "stage_progress",
    "stage_completed",
    "tool_started",
    "tool_completed",
    "evidence_found",
    "facts_proposed",
    "fact_confirmation_required",
    "conflict_detected",
    "human_input_required",
    "human_review_required",
    "artifact_ready",
    "run_paused",
    "run_resumed",
    "run_retrying",
    "run_failed",
    "run_completed",
    "run_cancelled",
]

_FORBIDDEN_TRACE_KEYS = {
    "access_token",
    "api_key",
    "authorization",
    "chain_of_thought",
    "credential",
    "credentials",
    "password",
    "raw_completion",
    "raw_prompt",
    "refresh_token",
    "reasoning",
    "secret",
    "thought",
}
_MAX_CHECKPOINT_BYTES = 64 * 1024
_MAX_EVENT_PAYLOAD_BYTES = 16 * 1024


class AgentRun(BaseDomainModel):
    """一次可恢复工作流运行；领域对象本身不依赖具体编排框架。"""

    _ALLOWED_TRANSITIONS: ClassVar[dict[str, frozenset[str]]] = {
        "queued": frozenset({"running", "cancelled"}),
        "running": frozenset(
            {
                "waiting_for_user",
                "waiting_for_review",
                "retrying",
                "completed",
                "failed",
                "cancelled",
            }
        ),
        "waiting_for_user": frozenset({"running", "waiting_for_review", "failed", "cancelled"}),
        "waiting_for_review": frozenset({"running", "completed", "failed", "cancelled"}),
        "retrying": frozenset(
            {
                "running",
                "waiting_for_user",
                "waiting_for_review",
                "completed",
                "failed",
                "cancelled",
            }
        ),
        "completed": frozenset(),
        "failed": frozenset({"retrying"}),
        "cancelled": frozenset(),
    }

    run_id: str = Field(min_length=1)
    workspace_id: str = Field(min_length=1)
    case_id: str = Field(min_length=1)
    workflow_type: WorkflowType
    status: AgentRunStatus = "queued"
    thread_id: str = Field(min_length=1)
    checkpoint_id: str | None = None
    current_stage: str = Field(default="queued", min_length=1, max_length=100)
    model_config_snapshot: dict[str, Any] = Field(default_factory=dict)
    token_usage: int = Field(default=0, ge=0)
    cost: float = Field(default=0.0, ge=0.0)
    retry_count: int = Field(default=0, ge=0)
    revision: int = Field(default=1, ge=1)
    created_by: str = Field(min_length=1)
    error_code: str | None = None
    error_message: str | None = Field(default=None, max_length=2000)
    created_at: float
    updated_at: float
    started_at: float | None = None
    completed_at: float | None = None

    @model_validator(mode="after")
    def validate_run(self) -> AgentRun:
        if self.updated_at < self.created_at:
            raise ValueError("updated_at 不能早于 created_at")
        if self.started_at is not None and self.started_at < self.created_at:
            raise ValueError("started_at 不能早于 created_at")
        if self.completed_at is not None:
            lower_bound = self.started_at if self.started_at is not None else self.created_at
            if self.completed_at < lower_bound:
                raise ValueError("completed_at 不能早于运行开始时间")
        if self.status == "queued" and self.started_at is not None:
            raise ValueError("queued Run 不能记录 started_at")
        if self.status not in {"queued", "cancelled"} and self.started_at is None:
            raise ValueError("已开始的 Run 必须记录 started_at")
        terminal = self.status in {"completed", "failed", "cancelled"}
        if terminal != (self.completed_at is not None):
            raise ValueError("只有终态 Run 必须记录 completed_at")
        if self.status == "failed" and not self.error_code:
            raise ValueError("failed Run 必须记录 error_code")
        if self.status != "failed" and self.error_code is not None:
            raise ValueError("非 failed Run 不能保留 error_code")
        _validate_safe_json(
            self.model_config_snapshot,
            field_name="model_config_snapshot",
            max_bytes=_MAX_CHECKPOINT_BYTES,
        )
        return self

    def start(
        self,
        *,
        checkpoint_id: str,
        stage: str,
        at: float | None = None,
    ) -> AgentRun:
        transition_time = time.time() if at is None else at
        return self._transition(
            "running",
            at=transition_time,
            checkpoint_id=checkpoint_id,
            current_stage=stage,
            started_at=transition_time,
            completed_at=None,
            error_code=None,
            error_message=None,
        )

    def advance(
        self,
        *,
        checkpoint_id: str,
        stage: str,
        token_usage: int | None = None,
        cost: float | None = None,
        at: float | None = None,
    ) -> AgentRun:
        if self.status != "running":
            raise InvalidAgentRunTransition(self.run_id, self.status, "running")
        update_time = self._validate_update_time(at)
        next_token_usage = self.token_usage if token_usage is None else token_usage
        next_cost = self.cost if cost is None else cost
        if next_token_usage < self.token_usage:
            raise ValueError("token_usage 不能倒退")
        if next_cost < self.cost:
            raise ValueError("cost 不能倒退")
        return self.model_copy(
            update={
                "checkpoint_id": checkpoint_id,
                "current_stage": stage,
                "token_usage": next_token_usage,
                "cost": next_cost,
                "revision": self.revision + 1,
                "updated_at": update_time,
            }
        )

    def pause_for_user(
        self,
        *,
        checkpoint_id: str,
        stage: str,
        at: float | None = None,
    ) -> AgentRun:
        return self._transition(
            "waiting_for_user",
            at=at,
            force_update=True,
            checkpoint_id=checkpoint_id,
            current_stage=stage,
        )

    def pause_for_review(
        self,
        *,
        checkpoint_id: str,
        stage: str,
        at: float | None = None,
    ) -> AgentRun:
        return self._transition(
            "waiting_for_review",
            at=at,
            force_update=True,
            checkpoint_id=checkpoint_id,
            current_stage=stage,
        )

    def resume(
        self,
        *,
        checkpoint_id: str,
        stage: str,
        at: float | None = None,
    ) -> AgentRun:
        return self._transition(
            "running",
            at=at,
            checkpoint_id=checkpoint_id,
            current_stage=stage,
            completed_at=None,
            error_code=None,
            error_message=None,
        )

    def mark_retrying(
        self,
        *,
        checkpoint_id: str,
        stage: str,
        at: float | None = None,
    ) -> AgentRun:
        return self._transition(
            "retrying",
            at=at,
            checkpoint_id=checkpoint_id,
            current_stage=stage,
            retry_count=self.retry_count + 1,
            completed_at=None,
            error_code=None,
            error_message=None,
        )

    def complete(
        self,
        *,
        checkpoint_id: str,
        stage: str = "complete",
        at: float | None = None,
    ) -> AgentRun:
        transition_time = time.time() if at is None else at
        return self._transition(
            "completed",
            at=transition_time,
            checkpoint_id=checkpoint_id,
            current_stage=stage,
            completed_at=transition_time,
            error_code=None,
            error_message=None,
        )

    def fail(
        self,
        *,
        checkpoint_id: str,
        stage: str,
        error_code: str,
        error_message: str = "",
        at: float | None = None,
    ) -> AgentRun:
        if not error_code.strip():
            raise ValueError("error_code 必填")
        transition_time = time.time() if at is None else at
        return self._transition(
            "failed",
            at=transition_time,
            checkpoint_id=checkpoint_id,
            current_stage=stage,
            completed_at=transition_time,
            error_code=error_code,
            error_message=error_message or None,
        )

    def cancel(
        self,
        *,
        checkpoint_id: str,
        stage: str,
        at: float | None = None,
    ) -> AgentRun:
        transition_time = time.time() if at is None else at
        return self._transition(
            "cancelled",
            at=transition_time,
            checkpoint_id=checkpoint_id,
            current_stage=stage,
            completed_at=transition_time,
            error_code=None,
            error_message=None,
        )

    def _transition(
        self,
        target: AgentRunStatus,
        *,
        at: float | None,
        force_update: bool = False,
        **updates: Any,
    ) -> AgentRun:
        if target == self.status and not force_update:
            return self
        if target != self.status and target not in self._ALLOWED_TRANSITIONS[self.status]:
            raise InvalidAgentRunTransition(self.run_id, self.status, target)
        transition_time = self._validate_update_time(at)
        return self.model_copy(
            update={
                "status": target,
                "revision": self.revision + 1,
                "updated_at": transition_time,
                **updates,
            }
        )

    def _validate_update_time(self, at: float | None) -> float:
        update_time = time.time() if at is None else at
        if update_time < self.updated_at:
            raise ValueError("Run 更新时间不能倒退")
        return update_time


class RunCheckpoint(BaseDomainModel):
    """框架无关的轻量恢复快照，不保存文档正文或模型思维链。"""

    checkpoint_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    thread_id: str = Field(min_length=1)
    version: int = Field(ge=1)
    stage: str = Field(min_length=1, max_length=100)
    state: dict[str, Any] = Field(default_factory=dict)
    created_at: float

    @model_validator(mode="after")
    def validate_checkpoint(self) -> RunCheckpoint:
        _validate_safe_json(
            self.state,
            field_name="checkpoint.state",
            max_bytes=_MAX_CHECKPOINT_BYTES,
        )
        return self


class RunEvent(BaseDomainModel):
    """对用户可见的工作流阶段事件；不暴露原始思维链。"""

    event_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    sequence: int = Field(ge=1)
    event_type: RunEventType
    stage: str | None = Field(default=None, min_length=1, max_length=100)
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: float

    @model_validator(mode="after")
    def validate_event(self) -> RunEvent:
        _validate_safe_json(
            self.payload,
            field_name="event.payload",
            max_bytes=_MAX_EVENT_PAYLOAD_BYTES,
        )
        return self


class CaseDocumentReadiness(BaseDomainModel):
    ready_document_ids: list[str] = Field(default_factory=list)
    pending_document_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_readiness(self) -> CaseDocumentReadiness:
        if len(self.ready_document_ids) != len(set(self.ready_document_ids)):
            raise ValueError("ready_document_ids 不能重复")
        if len(self.pending_document_ids) != len(set(self.pending_document_ids)):
            raise ValueError("pending_document_ids 不能重复")
        if set(self.ready_document_ids) & set(self.pending_document_ids):
            raise ValueError("同一文档不能同时处于 ready 和 pending")
        return self

    @property
    def blocked(self) -> bool:
        return not self.ready_document_ids or bool(self.pending_document_ids)


class WorkflowInterrupt(BaseDomainModel):
    kind: WorkflowInterruptKind
    payload: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_interrupt(self) -> WorkflowInterrupt:
        _validate_safe_json(
            self.payload,
            field_name="workflow_interrupt.payload",
            max_bytes=_MAX_EVENT_PAYLOAD_BYTES,
        )
        return self


class WorkflowExecutionResult(BaseDomainModel):
    status: WorkflowExecutionStatus
    checkpoint_id: str = Field(min_length=1)
    stage: str = Field(min_length=1, max_length=100)
    state: dict[str, Any] = Field(default_factory=dict)
    completed_stages: list[str] = Field(default_factory=list)
    interrupt: WorkflowInterrupt | None = None

    @model_validator(mode="after")
    def validate_execution_result(self) -> WorkflowExecutionResult:
        if (self.status == "interrupted") != (self.interrupt is not None):
            raise ValueError("只有 interrupted 执行结果必须携带 interrupt")
        if len(self.completed_stages) != len(set(self.completed_stages)):
            raise ValueError("completed_stages 不能重复")
        _validate_safe_json(
            self.state,
            field_name="workflow_execution.state",
            max_bytes=_MAX_CHECKPOINT_BYTES,
        )
        return self


def _validate_safe_json(value: dict[str, Any], *, field_name: str, max_bytes: int) -> None:
    forbidden = _find_forbidden_keys(value)
    if forbidden:
        keys = ", ".join(sorted(forbidden))
        raise ValueError(f"{field_name} 不允许保存敏感或原始推理字段: {keys}")
    try:
        encoded = json.dumps(value, ensure_ascii=False, allow_nan=False).encode()
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} 必须是有效 JSON") from exc
    if len(encoded) > max_bytes:
        raise ValueError(f"{field_name} 超过 {max_bytes} 字节限制")


def _find_forbidden_keys(value: Any) -> set[str]:
    if isinstance(value, dict):
        found = {str(key).lower() for key in value if str(key).lower() in _FORBIDDEN_TRACE_KEYS}
        for child in value.values():
            found.update(_find_forbidden_keys(child))
        return found
    if isinstance(value, list):
        list_found: set[str] = set()
        for child in value:
            list_found.update(_find_forbidden_keys(child))
        return list_found
    return set()

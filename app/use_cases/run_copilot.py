"""Conversation orchestration with persistent attachment selection and run identity."""

from __future__ import annotations

import contextlib
import time
import uuid
from collections.abc import Iterator
from typing import TYPE_CHECKING

from app.runtime_control import ControlledEventStream, RunControl, RunControlRegistry
from app.use_cases.personal_memory_qa import PersonalMemoryQAUseCase
from domain.agent import AgentEvent
from domain.errors import ConversationRunConflict, ConversationRunStopped, TaskNotFound
from domain.models import Citation, ConversationRun, Message

if TYPE_CHECKING:
    from app.use_cases.account_memory import AccountMemoryUseCase
    from app.use_cases.document_management import DocumentManagementUseCase
    from app.use_cases.task_management import TaskManagementUseCase
    from domain.ports import (
        ConversationRunRepoPort,
        CopilotAgentPort,
        MemoryJobSchedulerPort,
        TracePort,
    )


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:20]}"


class RunCopilotUseCase:
    def __init__(
        self,
        *,
        agent: CopilotAgentPort,
        task_management: TaskManagementUseCase,
        run_repo: ConversationRunRepoPort,
        memory_scheduler: MemoryJobSchedulerPort | None = None,
        documents: DocumentManagementUseCase | None = None,
        run_timeout_seconds: float = 120.0,
        model_call_timeout_seconds: float = 45.0,
        tool_call_timeout_seconds: float = 20.0,
        max_model_calls: int = 12,
        max_tool_calls: int = 6,
        control_poll_seconds: float = 0.05,
        trace: TracePort | None = None,
        account_memory: AccountMemoryUseCase | None = None,
    ) -> None:
        self._agent = agent
        self._task_uc = task_management
        self._runs = run_repo
        self._memory_scheduler = memory_scheduler
        self._documents = documents
        self._run_timeout_seconds = run_timeout_seconds
        self._max_model_calls = max_model_calls
        self._max_tool_calls = max_tool_calls
        self._trace = trace
        self._account_memory = account_memory
        self._personal_memory_qa = (
            PersonalMemoryQAUseCase(account_memory) if account_memory is not None else None
        )
        self._runs.reconcile_stale(now=time.time())
        self._controls = RunControlRegistry(
            run_repo,
            model_call_timeout_seconds=model_call_timeout_seconds,
            tool_call_timeout_seconds=tool_call_timeout_seconds,
            poll_seconds=control_poll_seconds,
        )

    def stream(
        self,
        *,
        owner_id: str,
        task_id: str | None,
        user_message: str,
        attachment_doc_ids: list[str] | None = None,
    ) -> Iterator[AgentEvent]:
        if not owner_id:
            raise ValueError("owner_id 必填")
        if not user_message:
            raise ValueError("user_message 不能为空")
        if task_id is not None and self._task_uc.get_task(task_id, owner_id) is None:
            raise TaskNotFound(task_id)

        explicit_selection = None
        if attachment_doc_ids is not None:
            explicit_selection = list(dict.fromkeys(attachment_doc_ids))
            self._validate_attachments(owner_id, explicit_selection)

        created_task_id: str | None = None
        if task_id is None:
            title = (user_message[:30] + "…") if len(user_message) > 30 else user_message
            task = self._task_uc.create_task(owner_id, title=title, user_goal=user_message)
            task_id = task.task_id
            created_task_id = task_id

        if explicit_selection is not None:
            self._task_uc.set_attachment_ids(task_id, owner_id, explicit_selection)
        selected_attachments = self._task_uc.list_attachment_ids(task_id, owner_id)
        self._validate_attachments(owner_id, selected_attachments)

        input_message = self._task_uc.append_user_message(task_id, owner_id, user_message)
        now = time.time()
        run = ConversationRun(
            run_id=_new_id("run"),
            thread_id=_new_id("thread"),
            owner_id=owner_id,
            task_id=task_id,
            question=user_message,
            input_message_id=input_message.msg_id,
            attachment_doc_ids=selected_attachments,
            max_duration_seconds=self._run_timeout_seconds,
            remaining_seconds=self._run_timeout_seconds,
            deadline_at=now + self._run_timeout_seconds,
            max_model_calls=self._max_model_calls,
            max_tool_calls=self._max_tool_calls,
            created_at=now,
            updated_at=now,
        )
        self._runs.create(run)
        control = self._controls.activate(run)
        iterator = self._stream_created(
            owner_id=owner_id,
            run=run,
            control=control,
            created_task_id=created_task_id,
        )
        return ControlledEventStream(
            iterator,
            lambda: self._disconnect_stream(owner_id=owner_id, run_id=run.run_id),
            run_id=run.run_id,
        )

    def _stream_created(
        self,
        *,
        owner_id: str,
        run: ConversationRun,
        control: RunControl,
        created_task_id: str | None,
    ) -> Iterator[AgentEvent]:
        try:
            control.mark_started()
            if created_task_id is not None:
                yield AgentEvent.task_created(created_task_id)
            yield AgentEvent.run_started(run.run_id)
            try:
                control.check()
            except ConversationRunStopped as stopped:
                yield AgentEvent.run_status(
                    run.run_id,
                    stopped.status,
                    stopped.status,
                    stopped.reason,
                )
                return
            if self._account_memory is not None:
                account_memory = self._account_memory
                try:
                    notice = self._account_memory.handle_message(
                        owner_id,
                        run.task_id,
                        run.input_message_id,
                        run.question,
                    )
                except (ValueError, PermissionError, LookupError) as exc:
                    notice = str(exc)
                citations: list[Citation] = []
                reason = "memory_command"
                personal = None
                if notice is None and self._personal_memory_qa is not None:
                    personal = self._personal_memory_qa.answer(
                        owner_id, run.question, run.run_id, task_id=run.task_id
                    )
                    if personal is not None:
                        notice = personal.text
                        citations = personal.citations
                        reason = personal.reason
                if notice is not None:
                    control.check()
                    message = Message(
                        msg_id=_new_id("msg"),
                        task_id=run.task_id,
                        role="assistant",
                        content=notice,
                        citations=citations,
                    )
                    terminal = run.model_copy(
                        update={
                            "status": "completed",
                            "stage": "completed",
                            "answer_message_id": message.msg_id,
                            "end_reason": reason,
                            "verified_citation_ids": [item.citation_id for item in citations],
                            "verified_context_message_ids": list(
                                dict.fromkeys(
                                    item.source_message_id
                                    for item in (personal.context_memories if personal else [])
                                    if item.source_message_id is not None
                                )
                            ),
                            "finished_at": time.time(),
                            "updated_at": time.time(),
                        }
                    )

                    def validate_direct_context() -> None:
                        account_memory.validate_resume(owner_id, run.run_id)
                        if personal is not None and self._personal_memory_qa is not None:
                            self._personal_memory_qa.validate_context(personal)

                    self._runs.commit_terminal(
                        terminal,
                        message,
                        validate_context=validate_direct_context,
                    )
                    yield AgentEvent.run_status(run.run_id, "completed", "completed", reason)
                    yield AgentEvent.answer(
                        notice, [item.model_dump() for item in citations], msg_id=message.msg_id
                    )
                    return
            yield from self._agent.run(
                owner_id=owner_id,
                task_id=run.task_id,
                user_message=run.question,
                attachment_doc_ids=run.attachment_doc_ids,
                run_id=run.run_id,
                thread_id=run.thread_id,
                input_message_id=run.input_message_id,
                runtime_control=control,
            )
        finally:
            self._finish_abandoned(owner_id=owner_id, run_id=run.run_id, control=control)
            self._controls.release(run.run_id, control)
            self._schedule_memory(owner_id=owner_id, task_id=run.task_id)

    def resume(self, *, owner_id: str, run_id: str) -> Iterator[AgentEvent]:
        run = self._runs.get(run_id, owner_id)
        if run is None:
            raise TaskNotFound(run_id)
        if run.status != "interrupted":
            raise ConversationRunConflict(run_id)
        if self._task_uc.get_task(run.task_id, owner_id) is None:
            raise TaskNotFound(run.task_id)
        self._validate_attachments(owner_id, run.attachment_doc_ids)
        if self._account_memory is not None:
            try:
                self._account_memory.validate_resume(owner_id, run_id)
            except (ValueError, PermissionError):
                self.cancel(owner_id=owner_id, run_id=run_id, reason="memory_context_changed")
                raise
        claimed = self._runs.claim_resume(run_id, owner_id, updated_at=time.time())
        if claimed is None:
            if self._runs.get(run_id, owner_id) is None:
                raise TaskNotFound(run_id)
            raise ConversationRunConflict(run_id)
        control = self._controls.activate(claimed)
        return ControlledEventStream(
            self._resume_claimed(owner_id=owner_id, run=claimed, control=control),
            lambda: self._disconnect_stream(owner_id=owner_id, run_id=claimed.run_id),
            run_id=claimed.run_id,
        )

    def _resume_claimed(
        self,
        *,
        owner_id: str,
        run: ConversationRun,
        control: RunControl,
    ) -> Iterator[AgentEvent]:
        try:
            control.mark_started()
            yield AgentEvent.run_resumed(run.run_id)
            yield from self._agent.resume(
                owner_id=owner_id,
                task_id=run.task_id,
                run_id=run.run_id,
                thread_id=run.thread_id,
                user_message=run.question,
                attachment_doc_ids=run.attachment_doc_ids,
                prior_evidence=run.evidence,
                runtime_control=control,
            )
        finally:
            self._finish_abandoned(owner_id=owner_id, run_id=run.run_id, control=control)
            self._controls.release(run.run_id, control)
            self._schedule_memory(owner_id=owner_id, task_id=run.task_id)

    def cancel(
        self,
        *,
        owner_id: str,
        run_id: str,
        reason: str = "user_cancelled",
    ) -> ConversationRun:
        trace_context = (
            self._trace.span(
                "riskpilot.copilot.cancel",
                metadata={"run_id": run_id, "owner_id": owner_id, "end_reason": reason},
            )
            if self._trace is not None
            else contextlib.nullcontext(None)
        )
        with trace_context as span:
            run = self._runs.request_cancel(
                run_id,
                owner_id,
                reason=reason,
                now=time.time(),
            )
            if run is None:
                if span is not None:
                    span.add_metadata({"status": "not_found"})
                raise TaskNotFound(run_id)
            control = (
                self._controls.signal_cancel(run_id, reason)
                if run.status in {"cancel_requested", "cancelled"}
                else None
            )
            if run.status == "cancel_requested" and (control is None or not control.started):
                if control is not None:
                    run = control.finish(status="cancelled", reason=reason)
                    self._controls.release(run_id, control)
                else:
                    finished = self._runs.finish_active(
                        run_id,
                        owner_id,
                        status="cancelled",
                        reason=reason,
                        error_type=None,
                        now=time.time(),
                    )
                    if finished is not None:
                        run = finished
            if span is not None:
                span.add_metadata({"status": run.status, "end_reason": run.end_reason or reason})
            return run

    def get_run(self, *, owner_id: str, run_id: str) -> ConversationRun | None:
        return self._reconcile(self._runs.get(run_id, owner_id))

    def get_latest_run(self, *, owner_id: str, task_id: str) -> ConversationRun | None:
        return self._reconcile(self._runs.get_latest_for_task(task_id, owner_id))

    def _disconnect_stream(self, *, owner_id: str, run_id: str) -> None:
        with contextlib.suppress(Exception):
            run = self._runs.get(run_id, owner_id)
            if run is None or run.status not in {"running", "cancel_requested"}:
                return
            self.cancel(owner_id=owner_id, run_id=run_id, reason="client_disconnected")

    def _reconcile(self, run: ConversationRun | None) -> ConversationRun | None:
        if run is None:
            return None
        if run.status == "cancel_requested":
            return self._runs.finish_active(
                run.run_id,
                run.owner_id,
                status="cancelled",
                reason=run.end_reason or "user_cancelled",
                error_type=None,
                now=time.time(),
            )
        if run.status == "running" and run.deadline_at is not None:
            now = time.time()
            if now >= run.deadline_at:
                return self._runs.finish_active(
                    run.run_id,
                    run.owner_id,
                    status="timed_out",
                    reason="run_timeout",
                    error_type="RunTimeout",
                    now=now,
                )
        return run

    def _finish_abandoned(
        self,
        *,
        owner_id: str,
        run_id: str,
        control: RunControl,
    ) -> None:
        current = self._runs.get(run_id, owner_id)
        if current is None:
            return
        if current.status == "cancel_requested":
            control.finish(
                status="cancelled",
                reason=current.end_reason or "user_cancelled",
            )
        elif current.status == "running":
            control.finish(
                status="failed",
                reason="execution_abandoned",
                error_type="ExecutionAbandoned",
            )

    def _validate_attachments(self, owner_id: str, document_ids: list[str]) -> None:
        if document_ids and self._documents is None:
            raise ValueError("附件处理尚未装配")
        for document_id in document_ids:
            assert self._documents is not None
            detail = self._documents.get_detail(document_id, owner_id)
            if detail.document.status != "ready":
                raise ValueError(f"附件 {document_id!r} 尚未处理完成")

    def _schedule_memory(self, *, owner_id: str, task_id: str) -> None:
        if self._memory_scheduler is None:
            return
        with contextlib.suppress(Exception):
            self._memory_scheduler.schedule_summarization(owner_id, task_id)

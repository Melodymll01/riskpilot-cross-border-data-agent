"""ConversationRunRepoPort in-memory fake."""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import TYPE_CHECKING

from domain.errors import ConversationRunConflict
from domain.models import ConversationRun, Message

if TYPE_CHECKING:
    from domain.ports import TaskRepoPort


class InMemoryConversationRunRepo:
    def __init__(self, task_repo: TaskRepoPort | None = None) -> None:
        self.runs: dict[str, ConversationRun] = {}
        self._task_repo = task_repo
        self._lock = threading.Lock()

    def create(self, run: ConversationRun) -> None:
        if run.run_id in self.runs:
            raise ValueError("run_id 已存在")
        self.runs[run.run_id] = run

    def get(self, run_id: str, owner_id: str) -> ConversationRun | None:
        run = self.runs.get(run_id)
        return run if run is not None and run.owner_id == owner_id else None

    def update(self, run: ConversationRun, *, expected_status: str | None = None) -> None:
        with self._lock:
            current = self.runs.get(run.run_id)
            if current is None:
                raise ValueError("run 不存在")
            if expected_status is not None and current.status != expected_status:
                raise ConversationRunConflict(run.run_id)
            self.runs[run.run_id] = run

    def claim_resume(
        self,
        run_id: str,
        owner_id: str,
        *,
        updated_at: float,
    ) -> ConversationRun | None:
        with self._lock:
            run = self.get(run_id, owner_id)
            if (
                run is None
                or run.status != "interrupted"
                or run.answer_message_id is not None
                or run.remaining_seconds <= 0
            ):
                return None
            claimed = run.model_copy(
                update={
                    "status": "running",
                    "stage": "agent",
                    "error_type": None,
                    "end_reason": None,
                    "deadline_at": updated_at + run.remaining_seconds,
                    "updated_at": updated_at,
                }
            )
            self.runs[run_id] = claimed
            return claimed

    def commit_terminal(
        self,
        run: ConversationRun,
        message: Message,
        *,
        validate_context: Callable[[], None] | None = None,
    ) -> None:
        with self._lock:
            if validate_context is not None:
                validate_context()
            current = self.get(run.run_id, run.owner_id)
            if (
                current is None
                or current.status != "running"
                or current.answer_message_id is not None
                or (current.deadline_at is not None and run.updated_at >= current.deadline_at)
            ):
                raise ConversationRunConflict(run.run_id)
            if self._task_repo is None:
                raise RuntimeError("in-memory run repo 未绑定 task repo")
            self._task_repo.append_message(message)
            remaining = current.remaining_seconds
            if current.deadline_at is not None:
                remaining = max(0.0, current.deadline_at - run.updated_at)
            self.runs[run.run_id] = run.model_copy(
                update={"remaining_seconds": remaining, "deadline_at": None}
            )

    def reserve_call(
        self,
        run_id: str,
        owner_id: str,
        *,
        kind: str,
        stage: str,
        now: float,
    ) -> ConversationRun | None:
        with self._lock:
            run = self.get(run_id, owner_id)
            if run is None or run.status != "running":
                return None
            if run.deadline_at is not None and now >= run.deadline_at:
                return None
            if kind == "model":
                if run.model_call_count >= run.max_model_calls:
                    return None
                updates = {"model_call_count": run.model_call_count + 1}
            elif kind == "tool":
                if run.tool_call_count >= run.max_tool_calls:
                    return None
                updates = {"tool_call_count": run.tool_call_count + 1}
            else:
                raise ValueError("未知调用类型")
            updated = run.model_copy(update={**updates, "stage": stage, "updated_at": now})
            self.runs[run_id] = updated
            return updated

    def record_model_usage(
        self,
        run_id: str,
        owner_id: str,
        *,
        input_tokens: int | None,
        output_tokens: int | None,
        now: float,
    ) -> ConversationRun | None:
        with self._lock:
            run = self.get(run_id, owner_id)
            if run is None or run.status not in {"running", "cancel_requested"}:
                return None
            if input_tokens is None or output_tokens is None:
                return run
            updated = run.model_copy(
                update={
                    "input_tokens": (run.input_tokens or 0) + input_tokens,
                    "output_tokens": (run.output_tokens or 0) + output_tokens,
                    "usage_reported_model_calls": run.usage_reported_model_calls + 1,
                    "updated_at": now,
                }
            )
            self.runs[run_id] = updated
            return updated

    def record_retry(
        self,
        run_id: str,
        owner_id: str,
        *,
        kind: str,
        now: float,
    ) -> ConversationRun | None:
        with self._lock:
            run = self.get(run_id, owner_id)
            if run is None or run.status != "running":
                return None
            if kind == "model":
                updates = {"model_retry_count": run.model_retry_count + 1}
            elif kind == "tool":
                updates = {"tool_retry_count": run.tool_retry_count + 1}
            else:
                raise ValueError("未知重试类型")
            updated = run.model_copy(update={**updates, "updated_at": now})
            self.runs[run_id] = updated
            return updated

    def request_cancel(
        self,
        run_id: str,
        owner_id: str,
        *,
        reason: str,
        now: float,
    ) -> ConversationRun | None:
        with self._lock:
            run = self.get(run_id, owner_id)
            if run is None:
                return None
            if run.status not in {"running", "interrupted"}:
                return run
            if run.status == "interrupted":
                updated = self._finish(
                    run,
                    status="cancelled",
                    reason=reason,
                    error_type=None,
                    now=now,
                )
            else:
                updated = run.model_copy(
                    update={
                        "status": "cancel_requested",
                        "end_reason": reason,
                        "cancel_requested_at": run.cancel_requested_at or now,
                        "updated_at": now,
                    }
                )
            self.runs[run_id] = updated
            return updated

    def finish_active(
        self,
        run_id: str,
        owner_id: str,
        *,
        status: str,
        reason: str,
        error_type: str | None,
        now: float,
    ) -> ConversationRun | None:
        with self._lock:
            run = self.get(run_id, owner_id)
            if run is None:
                return None
            if run.status not in {"running", "cancel_requested"} or run.answer_message_id:
                return run
            updated = self._finish(
                run,
                status=status,
                reason=reason,
                error_type=error_type,
                now=now,
            )
            self.runs[run_id] = updated
            return updated

    def pause(
        self,
        run_id: str,
        owner_id: str,
        *,
        now: float,
    ) -> ConversationRun | None:
        with self._lock:
            run = self.get(run_id, owner_id)
            if run is None:
                return None
            if (
                run.status != "running"
                or run.answer_message_id
                or (run.deadline_at is not None and now >= run.deadline_at)
            ):
                return run
            remaining = run.remaining_seconds
            if run.deadline_at is not None:
                remaining = max(0.0, run.deadline_at - now)
            updated = run.model_copy(
                update={
                    "status": "interrupted",
                    "stage": "agent",
                    "remaining_seconds": remaining,
                    "deadline_at": None,
                    "updated_at": now,
                }
            )
            self.runs[run_id] = updated
            return updated

    def reconcile_stale(self, *, now: float) -> int:
        changed = 0
        with self._lock:
            for run_id, run in list(self.runs.items()):
                if run.status == "cancel_requested":
                    self.runs[run_id] = self._finish(
                        run,
                        status="cancelled",
                        reason=run.end_reason or "user_cancelled",
                        error_type=None,
                        now=now,
                    )
                    changed += 1
                elif run.status == "running":
                    self.runs[run_id] = self._finish(
                        run,
                        status="failed",
                        reason="runtime_restarted",
                        error_type="RuntimeRestarted",
                        now=now,
                    )
                    changed += 1
        return changed

    def get_latest_for_task(self, task_id: str, owner_id: str) -> ConversationRun | None:
        candidates = [
            run for run in self.runs.values() if run.task_id == task_id and run.owner_id == owner_id
        ]
        return max(candidates, key=lambda run: run.updated_at, default=None)

    @staticmethod
    def _finish(
        run: ConversationRun,
        *,
        status: str,
        reason: str,
        error_type: str | None,
        now: float,
    ) -> ConversationRun:
        remaining = run.remaining_seconds
        if run.deadline_at is not None:
            remaining = max(0.0, run.deadline_at - now)
        return run.model_copy(
            update={
                "status": status,
                "stage": status,
                "remaining_seconds": remaining,
                "deadline_at": None,
                "end_reason": reason,
                "error_type": error_type,
                "finished_at": now,
                "updated_at": now,
            }
        )

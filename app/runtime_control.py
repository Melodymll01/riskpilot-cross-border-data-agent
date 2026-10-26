"""Per-run cooperative cancellation, deadlines, and persisted call budgets."""

from __future__ import annotations

import contextvars
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from typing import TYPE_CHECKING, Literal, TypeVar

from domain.errors import ConversationRunStopped

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from domain.agent import AgentEvent
    from domain.models import ConversationRun
    from domain.ports import ConversationRunRepoPort

T = TypeVar("T")


class RunControl:
    """One run's shared control plane.

    All counters and terminal transitions live in SQLite. The local event only
    shortens cancellation latency; every check also reads persisted state so a
    cancel request from another request thread/process remains authoritative.
    """

    def __init__(
        self,
        *,
        run_id: str,
        owner_id: str,
        run_repo: ConversationRunRepoPort,
        model_call_timeout_seconds: float,
        tool_call_timeout_seconds: float,
        poll_seconds: float,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._run_id = run_id
        self._owner_id = owner_id
        self._runs = run_repo
        self._model_timeout = model_call_timeout_seconds
        self._tool_timeout = tool_call_timeout_seconds
        self._poll_seconds = poll_seconds
        self._clock = clock
        self._cancel_event = threading.Event()
        self._cancel_reason = "user_cancelled"
        self._started = False
        self._graph_executor = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="run-graph",
        )

    @property
    def run_id(self) -> str:
        return self._run_id

    @property
    def started(self) -> bool:
        return self._started

    def mark_started(self) -> None:
        self._started = True

    def signal_cancel(self, reason: str) -> None:
        self._cancel_reason = reason
        self._cancel_event.set()

    def check(self, *, stage: str | None = None) -> ConversationRun:
        del stage
        run = self._require_run()
        if self._cancel_event.is_set() or run.status == "cancel_requested":
            stopped = self.finish(status="cancelled", reason=run.end_reason or self._cancel_reason)
            raise ConversationRunStopped(
                self._run_id,
                status=stopped.status,
                reason=stopped.end_reason or self._cancel_reason,
            )
        if run.status != "running":
            raise ConversationRunStopped(
                self._run_id,
                status=run.status,
                reason=run.end_reason or run.status,
            )
        now = self._clock()
        if run.deadline_at is not None and now >= run.deadline_at:
            stopped = self.finish(
                status="timed_out",
                reason="run_timeout",
                error_type="RunTimeout",
            )
            raise ConversationRunStopped(
                self._run_id,
                status=stopped.status,
                reason=stopped.end_reason or "run_timeout",
            )
        return run

    def before_model_call(self, *, operation: str, stage: str) -> ConversationRun:
        del operation
        return self._reserve(kind="model", stage=stage)

    def before_tool_call(self, *, operation: str) -> ConversationRun:
        del operation
        return self._reserve(kind="tool", stage="tool")

    def call_model(
        self,
        *,
        operation: str,
        stage: str,
        invoke: Callable[[], T],
        retry: bool = False,
    ) -> T:
        self.before_model_call(operation=operation, stage=stage)
        if retry:
            self.record_retry(kind="model")
        return self._run_interruptibly(
            invoke,
            timeout_seconds=self._model_timeout,
            timeout_reason=f"{operation}_timeout",
        )

    def call_tool(
        self,
        *,
        operation: str,
        invoke: Callable[[], T],
        retry: bool = False,
    ) -> T:
        self.before_tool_call(operation=operation)
        if retry:
            self.record_retry(kind="tool")
        return self._run_interruptibly(
            invoke,
            timeout_seconds=max(self._tool_timeout, self._model_timeout * 2)
            if operation == "assess_risk"
            else self._tool_timeout,
            timeout_reason=f"{operation}_timeout",
        )

    def wait_for_graph_event(self, invoke: Callable[[], T]) -> T:
        context = contextvars.copy_context()
        future = self._graph_executor.submit(context.run, invoke)
        return self._wait_future(
            future,
            timeout_seconds=None,
            timeout_reason="graph_execution_timeout",
        )

    def wait(self, seconds: float) -> None:
        deadline = time.monotonic() + max(0.0, seconds)
        while time.monotonic() < deadline:
            self.check()
            self._cancel_event.wait(min(self._poll_seconds, deadline - time.monotonic()))
        self.check()

    def close(self) -> None:
        self._graph_executor.shutdown(wait=False, cancel_futures=True)

    def record_model_usage(
        self,
        *,
        input_tokens: int | None,
        output_tokens: int | None,
    ) -> None:
        self._runs.record_model_usage(
            self._run_id,
            self._owner_id,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            now=self._clock(),
        )

    def record_retry(self, *, kind: Literal["model", "tool"]) -> None:
        updated = self._runs.record_retry(
            self._run_id,
            self._owner_id,
            kind=kind,
            now=self._clock(),
        )
        if updated is None:
            self.check()

    def interrupt(self) -> ConversationRun:
        paused = self._runs.pause(self._run_id, self._owner_id, now=self._clock())
        if paused is None or paused.status != "interrupted":
            self.check()
            raise ConversationRunStopped(
                self._run_id,
                status="failed",
                reason="interrupt_transition_failed",
            )
        return paused

    def finish(
        self,
        *,
        status: Literal["cancelled", "timed_out", "budget_exhausted", "failed"],
        reason: str,
        error_type: str | None = None,
    ) -> ConversationRun:
        finished = self._runs.finish_active(
            self._run_id,
            self._owner_id,
            status=status,
            reason=reason,
            error_type=error_type,
            now=self._clock(),
        )
        if finished is None:
            raise ConversationRunStopped(self._run_id, status=status, reason=reason)
        return finished

    def _reserve(self, *, kind: Literal["model", "tool"], stage: str) -> ConversationRun:
        self.check()
        if stage not in {"agent", "tool", "generation", "verification"}:
            raise ValueError("Unsupported active conversation stage")
        now = self._clock()
        reserved = self._runs.reserve_call(
            self._run_id,
            self._owner_id,
            kind=kind,
            stage=stage,
            now=now,
        )
        if reserved is not None:
            return reserved
        current = self._require_run()
        if current.status != "running" or (
            current.deadline_at is not None and now >= current.deadline_at
        ):
            return self.check()
        limit = current.max_model_calls if kind == "model" else current.max_tool_calls
        used = current.model_call_count if kind == "model" else current.tool_call_count
        if used >= limit:
            reason = f"{kind}_call_limit"
            stopped = self.finish(
                status="budget_exhausted",
                reason=reason,
                error_type="RunBudgetExceeded",
            )
            raise ConversationRunStopped(
                self._run_id,
                status=stopped.status,
                reason=reason,
            )
        failed = self.finish(
            status="failed",
            reason="budget_reservation_failed",
            error_type="RunReservationError",
        )
        raise ConversationRunStopped(
            self._run_id,
            status=failed.status,
            reason=failed.end_reason or "budget_reservation_failed",
        )

    def _run_interruptibly(
        self,
        invoke: Callable[[], T],
        *,
        timeout_seconds: float,
        timeout_reason: str,
    ) -> T:
        self.check()
        executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="run-control")
        context = contextvars.copy_context()
        future: Future[T] = executor.submit(context.run, invoke)
        try:
            return self._wait_future(
                future,
                timeout_seconds=timeout_seconds,
                timeout_reason=timeout_reason,
            )
        finally:
            future.cancel()
            executor.shutdown(wait=False, cancel_futures=True)

    def _wait_future(
        self,
        future: Future[T],
        *,
        timeout_seconds: float | None,
        timeout_reason: str,
    ) -> T:
        operation_deadline = (
            time.monotonic() + timeout_seconds if timeout_seconds is not None else None
        )
        while True:
            self.check()
            wait_for = (
                min(self._poll_seconds, max(0.0, operation_deadline - time.monotonic()))
                if operation_deadline is not None
                else self._poll_seconds
            )
            if wait_for <= 0:
                stopped = self.finish(
                    status="timed_out",
                    reason=timeout_reason,
                    error_type="OperationTimeout",
                )
                self._cancel_event.set()
                raise ConversationRunStopped(
                    self._run_id,
                    status=stopped.status,
                    reason=timeout_reason,
                )
            try:
                result = future.result(timeout=wait_for)
            except FutureTimeout as exc:
                if future.done():
                    stopped = self.finish(
                        status="timed_out",
                        reason=timeout_reason,
                        error_type=type(exc).__name__,
                    )
                    self._cancel_event.set()
                    raise ConversationRunStopped(
                        self._run_id,
                        status=stopped.status,
                        reason=timeout_reason,
                    ) from exc
                continue
            except Exception as exc:
                if "timeout" in type(exc).__name__.lower():
                    stopped = self.finish(
                        status="timed_out",
                        reason=timeout_reason,
                        error_type=type(exc).__name__,
                    )
                    self._cancel_event.set()
                    raise ConversationRunStopped(
                        self._run_id,
                        status=stopped.status,
                        reason=timeout_reason,
                    ) from exc
                raise
            self.check()
            return result

    def _require_run(self) -> ConversationRun:
        run = self._runs.get(self._run_id, self._owner_id)
        if run is None:
            raise ConversationRunStopped(
                self._run_id,
                status="failed",
                reason="run_not_found",
            )
        return run


class RunControlRegistry:
    def __init__(
        self,
        run_repo: ConversationRunRepoPort,
        *,
        model_call_timeout_seconds: float,
        tool_call_timeout_seconds: float,
        poll_seconds: float,
    ) -> None:
        self._runs = run_repo
        self._model_timeout = model_call_timeout_seconds
        self._tool_timeout = tool_call_timeout_seconds
        self._poll_seconds = poll_seconds
        self._active: dict[str, RunControl] = {}
        self._lock = threading.Lock()

    def activate(self, run: ConversationRun) -> RunControl:
        control = RunControl(
            run_id=run.run_id,
            owner_id=run.owner_id,
            run_repo=self._runs,
            model_call_timeout_seconds=self._model_timeout,
            tool_call_timeout_seconds=self._tool_timeout,
            poll_seconds=self._poll_seconds,
        )
        with self._lock:
            self._active[run.run_id] = control
        return control

    def signal_cancel(self, run_id: str, reason: str) -> RunControl | None:
        with self._lock:
            control = self._active.get(run_id)
        if control is not None:
            control.signal_cancel(reason)
        return control

    def release(self, run_id: str, control: RunControl) -> None:
        with self._lock:
            if self._active.get(run_id) is control:
                self._active.pop(run_id, None)
        control.close()


class ControlledEventStream:
    """Iterator wrapper with separate disconnect notification and resource release.

    ``disconnect()`` is a business signal: the HTTP consumer disappeared while
    the source was still active. ``close()`` only releases the wrapped iterator.
    Keeping those operations separate prevents a normal SSE EOF from cancelling
    an already completed or intentionally interrupted run.
    """

    def __init__(
        self,
        iterator: Iterator[AgentEvent],
        disconnect: Callable[[], None],
        *,
        run_id: str,
    ) -> None:
        self._iterator = iterator
        self._disconnect = disconnect
        self._closed = False
        self._disconnect_notified = False
        self._released = False
        self.run_id = run_id

    def __iter__(self) -> ControlledEventStream:
        return self

    def __next__(self) -> AgentEvent:
        if self._closed:
            raise StopIteration
        try:
            return next(self._iterator)
        except StopIteration:
            self._closed = True
            raise

    def disconnect(self) -> None:
        """Notify the application once that the consumer disconnected early."""
        if self._disconnect_notified:
            return
        self._disconnect_notified = True
        self._closed = True
        self._disconnect()

    def close(self) -> None:
        """Release iterator resources without changing persisted run state."""
        if self._released:
            return
        self._released = True
        self._closed = True
        close = getattr(self._iterator, "close", None)
        if callable(close):
            close()

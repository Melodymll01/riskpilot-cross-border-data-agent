"""Attempt-local budgets, cooperative cancellation, and bounded calls."""

from __future__ import annotations

import contextvars
import threading
import time
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass
from typing import Literal, TypeVar

from domain.errors import RuntimeStopped
from domain.runtime import RunBudget, UsagePayload

T = TypeVar("T")

RuntimeStatus = Literal[
    "running",
    "interrupted",
    "completed",
    "cancelled",
    "timed_out",
    "budget_exhausted",
    "failed",
]


@dataclass(frozen=True)
class RuntimeState:
    run_id: str
    status: RuntimeStatus
    model_call_count: int
    tool_call_count: int
    input_tokens: int | None
    output_tokens: int | None
    end_reason: str | None


class RunControl:
    """Local execution control derived only from Go's immutable budget snapshot."""

    def __init__(
        self,
        *,
        run_id: str,
        budget: RunBudget,
        model_call_timeout_seconds: float,
        tool_call_timeout_seconds: float,
        poll_seconds: float,
        initial_model_calls: int = 0,
        initial_tool_calls: int = 0,
        initial_input_tokens: int | None = None,
        initial_output_tokens: int | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._run_id = run_id
        self._budget = budget
        self._model_timeout = model_call_timeout_seconds
        self._tool_timeout = tool_call_timeout_seconds
        self._poll_seconds = poll_seconds
        self._clock = clock
        self._cancel_event = threading.Event()
        self._cancel_reason = "cancelled_by_control_plane"
        self._status: RuntimeStatus = "running"
        self._end_reason: str | None = None
        if (
            initial_model_calls > budget.max_model_calls
            or initial_tool_calls > budget.max_tool_calls
        ):
            raise ValueError("checkpoint usage exceeds immutable run budget")
        self._model_calls = initial_model_calls
        self._tool_calls = initial_tool_calls
        self._input_tokens = initial_input_tokens
        self._output_tokens = initial_output_tokens
        self._lock = threading.Lock()
        self._graph_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="run-graph")

    @property
    def run_id(self) -> str:
        return self._run_id

    @property
    def status(self) -> RuntimeStatus:
        with self._lock:
            return self._status

    def signal_cancel(self, reason: str) -> None:
        with self._lock:
            if self._status in {
                "completed",
                "cancelled",
                "timed_out",
                "budget_exhausted",
                "failed",
            }:
                return
            self._cancel_reason = reason
            self._cancel_event.set()

    def check(self, *, stage: str | None = None) -> RuntimeState:
        del stage
        with self._lock:
            if self._cancel_event.is_set():
                self._status = "cancelled"
                self._end_reason = self._cancel_reason
            elif self._status == "running" and self._clock() >= self._budget.deadline_at:
                self._status = "timed_out"
                self._end_reason = "run_deadline_exceeded"
                self._cancel_event.set()
            state = self._state_unlocked()
        if state.status != "running":
            raise RuntimeStopped(
                self._run_id,
                status=state.status,
                reason=state.end_reason or state.status,
            )
        return state

    def before_model_call(self, *, operation: str, stage: str) -> RuntimeState:
        del operation, stage
        return self._reserve("model")

    def before_tool_call(self, *, operation: str) -> RuntimeState:
        del operation
        return self._reserve("tool")

    def call_model(
        self,
        *,
        operation: str,
        stage: str,
        invoke: Callable[[], T],
        retry: bool = False,
    ) -> T:
        del retry
        self.before_model_call(operation=operation, stage=stage)
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
        del retry
        self.before_tool_call(operation=operation)
        return self._run_interruptibly(
            invoke,
            timeout_seconds=self._tool_timeout,
            timeout_reason=f"{operation}_timeout",
        )

    def wait_for_graph_event(self, invoke: Callable[[], T]) -> T:
        context = contextvars.copy_context()
        future = self._graph_executor.submit(context.run, invoke)
        return self._wait_future(future, timeout_seconds=None, timeout_reason="graph_timeout")

    def wait(self, seconds: float) -> None:
        deadline = time.monotonic() + max(0.0, seconds)
        while time.monotonic() < deadline:
            self.check()
            self._cancel_event.wait(min(self._poll_seconds, deadline - time.monotonic()))
        self.check()

    def record_model_usage(
        self,
        *,
        input_tokens: int | None,
        output_tokens: int | None,
    ) -> None:
        with self._lock:
            if input_tokens is not None:
                self._input_tokens = (self._input_tokens or 0) + max(0, input_tokens)
            if output_tokens is not None:
                self._output_tokens = (self._output_tokens or 0) + max(0, output_tokens)

    def record_retry(self, *, kind: Literal["model", "tool"]) -> None:
        del kind

    def interrupt(self) -> RuntimeState:
        self.check()
        with self._lock:
            self._status = "interrupted"
            self._end_reason = "checkpoint_interrupted"
            return self._state_unlocked()

    def resume(self) -> RuntimeState:
        with self._lock:
            if self._status != "interrupted":
                raise RuntimeStopped(
                    self._run_id,
                    status=self._status,
                    reason=self._end_reason or "run_not_interrupted",
                )
            self._status = "running"
            self._end_reason = None
        return self.check()

    def complete(self) -> RuntimeState:
        self.check()
        with self._lock:
            self._status = "completed"
            self._end_reason = "result_proposed"
            return self._state_unlocked()

    def finish(
        self,
        *,
        status: Literal["cancelled", "timed_out", "budget_exhausted", "failed"],
        reason: str,
        error_type: str | None = None,
    ) -> RuntimeState:
        del error_type
        with self._lock:
            if self._status == "completed":
                return self._state_unlocked()
            self._status = status
            self._end_reason = reason
            self._cancel_event.set()
            return self._state_unlocked()

    def usage(self) -> UsagePayload:
        with self._lock:
            return UsagePayload(
                model_calls=self._model_calls,
                tool_calls=self._tool_calls,
                input_tokens=self._input_tokens,
                output_tokens=self._output_tokens,
            )

    def close(self) -> None:
        self._graph_executor.shutdown(wait=False, cancel_futures=True)

    def _reserve(self, kind: Literal["model", "tool"]) -> RuntimeState:
        self.check()
        with self._lock:
            used = self._model_calls if kind == "model" else self._tool_calls
            limit = self._budget.max_model_calls if kind == "model" else self._budget.max_tool_calls
            if used >= limit:
                self._status = "budget_exhausted"
                self._end_reason = f"{kind}_call_limit"
                self._cancel_event.set()
                state = self._state_unlocked()
            else:
                if kind == "model":
                    self._model_calls += 1
                else:
                    self._tool_calls += 1
                return self._state_unlocked()
        raise RuntimeStopped(
            self._run_id,
            status=state.status,
            reason=state.end_reason or "budget_exhausted",
        )

    def _run_interruptibly(
        self,
        invoke: Callable[[], T],
        *,
        timeout_seconds: float,
        timeout_reason: str,
    ) -> T:
        self.check()
        executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="run-operation")
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
        deadline = time.monotonic() + timeout_seconds if timeout_seconds is not None else None
        while True:
            self.check()
            wait_for = self._poll_seconds
            if deadline is not None:
                wait_for = min(wait_for, max(0.0, deadline - time.monotonic()))
                if wait_for <= 0:
                    self.finish(status="timed_out", reason=timeout_reason)
                    raise RuntimeStopped(
                        self._run_id,
                        status="timed_out",
                        reason=timeout_reason,
                    )
            try:
                result = future.result(timeout=wait_for)
            except FutureTimeout:
                continue
            self.check()
            return result

    def _state_unlocked(self) -> RuntimeState:
        return RuntimeState(
            run_id=self._run_id,
            status=self._status,
            model_call_count=self._model_calls,
            tool_call_count=self._tool_calls,
            input_tokens=self._input_tokens,
            output_tokens=self._output_tokens,
            end_reason=self._end_reason,
        )

"""Application service for frozen internal-v1 run commands."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from app.runtime_control import RunControl
from domain.errors import RuntimeConflict, RuntimeStopped
from domain.runtime import RunEvent, RunExecute, RuntimeSignal, snapshot_fingerprint
from infra.agents.langchain_copilot import AgentAttemptState, RestoredAgentAttempt

if TYPE_CHECKING:
    from collections.abc import Iterator

    from infra.agents.langchain_copilot import LangChainComplianceAgent


@dataclass
class _Session:
    snapshot: RunExecute
    fingerprint: str
    control: RunControl
    agent_state: AgentAttemptState
    next_event_seq: int = 1
    running: bool = False
    events: list[RunEvent] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def event(self, signal: RuntimeSignal) -> RunEvent:
        with self.lock:
            event = RunEvent(
                run_id=self.snapshot.run_id,
                attempt_id=self.snapshot.attempt_id,
                event_seq=self.next_event_seq,
                event_type=signal.event_type,
                payload=signal.payload,
            )
            self.next_event_seq += 1
            self.events.append(event)
            return event


class InternalRuntimeService:
    def __init__(
        self,
        agent: LangChainComplianceAgent,
        *,
        model_call_timeout_seconds: float,
        tool_call_timeout_seconds: float,
        poll_seconds: float,
    ) -> None:
        self._agent = agent
        self._model_timeout = model_call_timeout_seconds
        self._tool_timeout = tool_call_timeout_seconds
        self._poll_seconds = poll_seconds
        self._sessions: dict[str, _Session] = {}
        self._lock = threading.Lock()

    def execute(self, snapshot: RunExecute) -> Iterator[RunEvent]:
        fingerprint = snapshot_fingerprint(snapshot)
        restored = self._agent.restore(snapshot) if snapshot.resume else None
        with self._lock:
            current = self._sessions.get(snapshot.run_id)
            if current is not None:
                if current.running:
                    raise RuntimeConflict("attempt is already executing")
                if snapshot.attempt_id < current.snapshot.attempt_id:
                    raise RuntimeConflict("stale attempt_id")
                if snapshot.attempt_id == current.snapshot.attempt_id:
                    if current.fingerprint != fingerprint:
                        raise RuntimeConflict("attempt snapshot changed")
                    if snapshot.resume:
                        raise RuntimeConflict("resume requires a new attempt_id")
                    if current.control.status == "interrupted":
                        raise RuntimeConflict("interrupted attempt requires resume=true")
                    return iter(list(current.events))
                current.control.signal_cancel("superseded_attempt")
            if snapshot.resume and restored is None:
                raise RuntimeConflict("resume checkpoint reconstruction failed")
            session = self._new_session(snapshot, fingerprint, restored=restored)
            session.running = True
            self._sessions[snapshot.run_id] = session
        return self._stream(session, resume=snapshot.resume)

    def cancel(self, run_id: str, attempt_id: int, reason: str) -> None:
        session = self._require(run_id, attempt_id)
        session.control.signal_cancel(reason)

    def owner_for(self, run_id: str, attempt_id: int) -> str:
        return self._require(run_id, attempt_id).snapshot.owner_id

    def close(self) -> None:
        with self._lock:
            sessions = list(self._sessions.values())
            self._sessions.clear()
        for session in sessions:
            session.control.signal_cancel("runtime_shutdown")
            session.control.close()

    def _new_session(
        self,
        snapshot: RunExecute,
        fingerprint: str,
        *,
        restored: RestoredAgentAttempt | None = None,
    ) -> _Session:
        control = RunControl(
            run_id=snapshot.run_id,
            budget=snapshot.budget,
            model_call_timeout_seconds=self._model_timeout,
            tool_call_timeout_seconds=self._tool_timeout,
            poll_seconds=self._poll_seconds,
            initial_model_calls=restored.model_calls if restored else 0,
            initial_tool_calls=restored.tool_calls if restored else 0,
            initial_input_tokens=restored.input_tokens if restored else None,
            initial_output_tokens=restored.output_tokens if restored else None,
        )
        return _Session(
            snapshot=snapshot,
            fingerprint=fingerprint,
            control=control,
            agent_state=restored.state if restored else AgentAttemptState(snapshot=snapshot),
            next_event_seq=1,
        )

    def _stream(self, session: _Session, *, resume: bool) -> Iterator[RunEvent]:
        try:
            try:
                for signal in self._agent.execute(
                    session.agent_state,
                    session.control,
                    resume=resume,
                ):
                    if signal.event_type != "result_proposal":
                        yield session.event(signal)
                        continue
                    try:
                        session.control.check(stage="verification")
                    except RuntimeStopped as stopped:
                        if stopped.status == "cancelled":
                            return
                        raise
                    yield session.event(
                        RuntimeSignal("usage", session.control.usage().model_dump())
                    )
                    session.control.check(stage="verification")
                    if signal.payload.get("status") == "completed":
                        session.control.complete()
                    else:
                        session.control.finish(
                            status="failed",
                            reason=str(signal.payload.get("end_reason") or "runtime_failed"),
                            error_type=(
                                str(signal.payload["error_type"])
                                if signal.payload.get("error_type") is not None
                                else None
                            ),
                        )
                    yield session.event(signal)
                    return
            except RuntimeStopped as stopped:
                if stopped.status == "cancelled":
                    return
                raise
        finally:
            with session.lock:
                session.running = False

    def _require(self, run_id: str, attempt_id: int) -> _Session:
        with self._lock:
            session = self._sessions.get(run_id)
        if session is None:
            raise RuntimeConflict("run snapshot is not available in this runtime")
        if session.snapshot.attempt_id != attempt_id:
            raise RuntimeConflict("attempt_id is not current")
        return session

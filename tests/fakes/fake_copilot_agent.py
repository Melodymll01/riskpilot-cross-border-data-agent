"""CopilotAgentPort Fake。"""

from __future__ import annotations

from domain.agent import AgentEvent


class FakeCopilotAgent:
    def __init__(self, answer: str = "done") -> None:
        self.answer = answer
        self.calls: list[dict[str, str]] = []

    def run(
        self,
        *,
        owner_id: str,
        task_id: str,
        user_message: str,
        attachment_doc_ids: list[str],
        run_id: str,
        thread_id: str,
        input_message_id: str,
        runtime_control,
    ):
        del runtime_control
        self.calls.append(
            {
                "owner_id": owner_id,
                "task_id": task_id,
                "user_message": user_message,
                "attachment_doc_ids": attachment_doc_ids,
                "run_id": run_id,
                "thread_id": thread_id,
                "input_message_id": input_message_id,
            }
        )
        yield AgentEvent.answer(self.answer)

    def resume(
        self,
        *,
        owner_id: str,
        task_id: str,
        run_id: str,
        thread_id: str,
        user_message: str,
        attachment_doc_ids: list[str],
        prior_evidence: list[dict[str, object]],
        runtime_control,
    ):
        del prior_evidence, runtime_control
        self.calls.append(
            {
                "owner_id": owner_id,
                "task_id": task_id,
                "user_message": user_message,
                "attachment_doc_ids": attachment_doc_ids,
                "run_id": run_id,
                "thread_id": thread_id,
                "input_message_id": "resume",
            }
        )
        yield AgentEvent.answer(self.answer)

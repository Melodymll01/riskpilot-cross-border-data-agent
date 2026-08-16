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
    ):
        self.calls.append(
            {
                "owner_id": owner_id,
                "task_id": task_id,
                "user_message": user_message,
            }
        )
        yield AgentEvent.answer(self.answer)

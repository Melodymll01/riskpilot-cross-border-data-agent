"""支持 LangChain tool calling 的离线 ChatModel Fake。"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.runnables import Runnable
from langchain_core.tools import BaseTool


class FakeToolCallingModel(BaseChatModel):
    responses: list[AIMessage]
    index: int = 0
    calls: list[list[BaseMessage]] = []
    generation_kwargs: list[dict[str, Any]] = []
    bound_tools: list[str] = []

    @property
    def _llm_type(self) -> str:
        return "riskpilot-fake-tool-calling"

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        self.calls.append(list(messages))
        self.generation_kwargs.append(dict(kwargs))
        response = self.responses[min(self.index, len(self.responses) - 1)]
        self.index += 1
        return ChatResult(generations=[ChatGeneration(message=response)])

    def bind_tools(
        self,
        tools: Sequence[dict[str, Any] | type | BaseTool],
        *,
        tool_choice: str | None = None,
        **kwargs: Any,
    ) -> Runnable:
        self.bound_tools = [
            tool.name if isinstance(tool, BaseTool) else str(tool)
            for tool in tools
        ]
        return self


def final_answer_model(answer: str = "done") -> FakeToolCallingModel:
    return FakeToolCallingModel(responses=[AIMessage(content=answer)])

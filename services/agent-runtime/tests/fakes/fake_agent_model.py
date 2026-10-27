"""支持 LangChain tool calling 的离线 ChatModel Fake。"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
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
            tool.name if isinstance(tool, BaseTool) else str(tool) for tool in tools
        ]
        return self


class FakeStreamingToolCallingModel(BaseChatModel):
    """Controllable model that emits genuine LangChain message chunks."""

    responses: list[list[AIMessageChunk]]
    index: int = 0
    calls: list[list[BaseMessage]] = []
    completed_calls: int = 0
    bound_tools: list[str] = []
    pause_call_index: int | None = None
    pause_after_chunk_index: int | None = None
    release_event: Any = None
    call_started_event: Any = None
    pause_before_first_chunk: bool = False

    @property
    def _llm_type(self) -> str:
        return "riskpilot-fake-streaming-tool-calling"

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        chunks = list(self._stream(messages, stop=stop, run_manager=run_manager, **kwargs))
        combined = sum((chunk.message for chunk in chunks[1:]), chunks[0].message)
        return ChatResult(
            generations=[
                ChatGeneration(
                    message=AIMessage(
                        content=combined.content,
                        tool_calls=combined.tool_calls,
                        usage_metadata=combined.usage_metadata,
                    )
                )
            ]
        )

    def _stream(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> Iterator[ChatGenerationChunk]:
        del stop, run_manager, kwargs
        self.calls.append(list(messages))
        scripted = self.responses[min(self.index, len(self.responses) - 1)]
        call_index = self.index
        self.index += 1
        if self.call_started_event is not None:
            self.call_started_event.set()
        if (
            self.pause_before_first_chunk
            and call_index == self.pause_call_index
            and self.release_event is not None
        ):
            self.release_event.wait(timeout=5)
        for chunk_index, chunk in enumerate(scripted):
            yield ChatGenerationChunk(message=chunk)
            if (
                call_index == self.pause_call_index
                and chunk_index == self.pause_after_chunk_index
                and self.release_event is not None
            ):
                self.release_event.wait(timeout=5)
        self.completed_calls += 1

    def bind_tools(
        self,
        tools: Sequence[dict[str, Any] | type | BaseTool],
        *,
        tool_choice: str | None = None,
        **kwargs: Any,
    ) -> Runnable:
        del tool_choice, kwargs
        self.bound_tools = [
            tool.name if isinstance(tool, BaseTool) else str(tool) for tool in tools
        ]
        return self


def final_answer_model(answer: str = "done") -> FakeToolCallingModel:
    return FakeToolCallingModel(responses=[AIMessage(content=answer)])

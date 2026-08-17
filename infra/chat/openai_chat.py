"""基于 LangChain ``BaseChatModel`` 的 ``ChatPort`` 实现。"""

from __future__ import annotations

from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, convert_to_messages

from domain.models import ChatResponse


class OpenAIChatAdapter:
    """实现 `ChatPort`。"""

    def __init__(self, model: BaseChatModel) -> None:
        self._model = model

    def chat(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.2,
        max_tokens: int | None = None,
        json_mode: bool = False,
    ) -> str:
        return self.chat_with_usage(
            messages,
            temperature=temperature,
            max_tokens=max_tokens,
            json_mode=json_mode,
        ).content

    def chat_with_usage(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.2,
        max_tokens: int | None = None,
        json_mode: bool = False,
    ) -> ChatResponse:
        kwargs: dict[str, Any] = {"temperature": temperature}
        if max_tokens is not None:
            kwargs["max_completion_tokens"] = max_tokens
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        response = self._model.invoke(
            convert_to_messages(messages),
            **kwargs,
        )
        return ChatResponse(
            content=_message_text(response).strip(),
            **_token_usage(response),
        )


def _message_text(message: BaseMessage) -> str:
    if isinstance(message.content, str):
        return message.content
    parts: list[str] = []
    for item in message.content:
        if isinstance(item, str):
            parts.append(item)
        elif isinstance(item, dict) and item.get("type") == "text":
            parts.append(str(item.get("text") or ""))
    return "\n".join(part for part in parts if part)


def _token_usage(message: BaseMessage) -> dict[str, int]:
    if not isinstance(message, AIMessage) or message.usage_metadata is None:
        return {"input_tokens": 0, "output_tokens": 0, "token_usage": 0}
    input_value = message.usage_metadata.get("input_tokens", 0)
    output_value = message.usage_metadata.get("output_tokens", 0)
    total_value = message.usage_metadata.get("total_tokens", 0)
    input_tokens = input_value if isinstance(input_value, int) and input_value >= 0 else 0
    output_tokens = output_value if isinstance(output_value, int) and output_value >= 0 else 0
    total_tokens = total_value if isinstance(total_value, int) and total_value >= 0 else 0
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "token_usage": max(total_tokens, input_tokens + output_tokens),
    }

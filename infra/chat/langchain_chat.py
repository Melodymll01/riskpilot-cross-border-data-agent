"""基于 LangChain ``BaseChatModel`` 的 ``ChatPort`` 实现。"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, convert_to_messages
from openai import LengthFinishReasonError

from domain.models import ChatResponse

if TYPE_CHECKING:
    from domain.ports import RunControlPort

logger = logging.getLogger(__name__)


class LangChainChatAdapter:
    """通过统一 ChatModel 实现 ChatPort，适用于 API 和 Traex。"""

    def __init__(
        self,
        model: BaseChatModel,
        *,
        json_extra_body: dict[str, Any] | None = None,
    ) -> None:
        self._model = model
        self._json_extra_body = dict(json_extra_body or {})

    def chat(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.2,
        max_tokens: int | None = None,
        json_mode: bool = False,
        runtime_control: RunControlPort | None = None,
        operation: str = "chat",
    ) -> str:
        return self.chat_with_usage(
            messages,
            temperature=temperature,
            max_tokens=max_tokens,
            json_mode=json_mode,
            runtime_control=runtime_control,
            operation=operation,
        ).content

    def chat_with_usage(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.2,
        max_tokens: int | None = None,
        json_mode: bool = False,
        runtime_control: RunControlPort | None = None,
        operation: str = "chat",
    ) -> ChatResponse:
        kwargs: dict[str, Any] = {"temperature": temperature}
        if max_tokens is not None:
            kwargs["max_completion_tokens"] = max_tokens
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
            if self._json_extra_body:
                kwargs["extra_body"] = dict(self._json_extra_body)
        if getattr(self._model, "accepts_runtime_control", False):
            kwargs["runtime_control"] = runtime_control

        def invoke() -> BaseMessage:
            return self._model.invoke(convert_to_messages(messages), **kwargs)

        try:
            response = (
                runtime_control.call_model(
                    operation=operation,
                    stage=_operation_stage(operation),
                    invoke=invoke,
                )
                if runtime_control is not None
                else invoke()
            )
        except LengthFinishReasonError as exc:
            logger.warning("结构化模型输出达到长度上限 operation=%s", operation)
            raise RuntimeError("结构化模型输出达到长度上限") from exc
        usage = _token_usage(response)
        result = ChatResponse(
            content=_message_text(response).strip(),
            **usage,
        )
        if runtime_control is not None:
            runtime_control.record_model_usage(
                input_tokens=result.input_tokens if result.usage_available else None,
                output_tokens=result.output_tokens if result.usage_available else None,
            )
        return result


def _operation_stage(operation: str) -> str:
    if operation == "query_rewrite":
        return "tool"
    return (
        operation if operation in {"agent", "tool", "generation", "verification"} else "generation"
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
        return {
            "input_tokens": 0,
            "output_tokens": 0,
            "token_usage": 0,
            "usage_available": False,
        }
    input_value = message.usage_metadata["input_tokens"]
    output_value = message.usage_metadata["output_tokens"]
    total_value = message.usage_metadata.get("total_tokens", 0)
    input_tokens = max(0, input_value)
    output_tokens = max(0, output_value)
    total_tokens = total_value if isinstance(total_value, int) and total_value >= 0 else 0
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "token_usage": max(total_tokens, input_tokens + output_tokens),
        "usage_available": True,
    }

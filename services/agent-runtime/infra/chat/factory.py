"""One LangChain model initialization point for all chat callers."""

from __future__ import annotations

from typing import TYPE_CHECKING

from langchain.chat_models import init_chat_model
from langchain_core.language_models.chat_models import BaseChatModel
from pydantic import SecretStr

from config import RuntimeConfigurationError

if TYPE_CHECKING:
    from config import Settings


def build_chat_model(
    settings: Settings,
    *,
    temperature: float = 0.1,
    max_tokens: int | None = None,
) -> BaseChatModel:
    """Build the selected OpenAI-compatible profile through LangChain."""
    errors = settings.chat_configuration_errors()
    if errors:
        raise RuntimeConfigurationError("RiskPilot 模型配置无效：\n- " + "\n- ".join(errors))
    output_limit = settings.chat_max_tokens if max_tokens is None else max_tokens
    return init_chat_model(
        model=settings.effective_chat_model,
        model_provider="openai",
        api_key=SecretStr(settings.effective_chat_api_key),
        base_url=settings.effective_chat_base_url,
        temperature=temperature,
        max_completion_tokens=output_limit,
        max_retries=0,
        timeout=settings.conversation_model_call_timeout_seconds,
        streaming=True,
        stream_usage=True,
    )

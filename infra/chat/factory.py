"""One provider selection point for Agent, retrieval and ChatPort callers."""

from __future__ import annotations

from typing import TYPE_CHECKING

from langchain_core.language_models.chat_models import BaseChatModel

from config import RuntimeConfigurationError

if TYPE_CHECKING:
    from config import Settings


def build_chat_model(
    settings: Settings,
    *,
    temperature: float = 0.1,
    max_tokens: int | None = None,
) -> BaseChatModel:
    """Select exactly the configured transport; never fall back between providers."""
    errors = settings.chat_configuration_errors()
    if errors:
        raise RuntimeConfigurationError("RiskPilot 模型配置无效：\n- " + "\n- ".join(errors))
    output_limit = settings.chat_max_tokens if max_tokens is None else max_tokens
    if settings.llm_provider == "traex":
        from infra.chat.traex_model import TraexChatModel

        return TraexChatModel(
            model_name=settings.traex_model,
            cli_path=settings.traex_bin,
            timeout_seconds=settings.traex_timeout_seconds,
            temperature=temperature,
            max_tokens=output_limit,
        )

    from infra.chat.openai_model import build_openai_chat_model

    return build_openai_chat_model(
        model=settings.effective_chat_model,
        api_key=settings.effective_chat_api_key,
        base_url=settings.effective_chat_base_url,
        temperature=temperature,
        max_tokens=output_limit,
        timeout_seconds=settings.conversation_model_call_timeout_seconds,
    )

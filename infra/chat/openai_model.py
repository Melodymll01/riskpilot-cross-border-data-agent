"""API Key 接入的 OpenAI-compatible ChatModel。"""

from __future__ import annotations

from langchain_openai import ChatOpenAI
from pydantic import SecretStr


def build_openai_chat_model(
    *,
    model: str,
    api_key: str,
    base_url: str,
    temperature: float,
    max_tokens: int | None,
    timeout_seconds: float = 45.0,
) -> ChatOpenAI:
    """构造支持标准 tool calling 的 OpenAI-compatible ChatModel。"""
    return ChatOpenAI(
        model=model,
        api_key=SecretStr(api_key),
        base_url=base_url,
        temperature=temperature,
        max_completion_tokens=max_tokens,
        max_retries=0,
        timeout=timeout_seconds,
        streaming=True,
        stream_usage=True,
    )

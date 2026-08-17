"""LangChain ChatModel 工厂。"""

from __future__ import annotations

from langchain_openai import ChatOpenAI
from pydantic import SecretStr


def build_langchain_chat_model(
    *,
    model: str,
    api_key: str,
    base_url: str,
    temperature: float,
    max_tokens: int | None,
) -> ChatOpenAI:
    """构造支持标准 tool calling 的 OpenAI-compatible ChatModel。"""
    return ChatOpenAI(
        model=model,
        api_key=SecretStr(api_key),
        base_url=base_url,
        temperature=temperature,
        max_completion_tokens=max_tokens,
        max_retries=2,
        streaming=False,
    )

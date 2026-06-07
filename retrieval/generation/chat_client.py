"""统一 Chat 客户端：支持 OpenAI 兼容 API。

用法：
    from retrieval.generation.chat_client import ChatClient, RETRYABLE_ERRORS

    client = ChatClient()
    text = client.complete(messages, temperature=0.2, max_tokens=1500)
    for chunk in client.complete_stream(messages, max_tokens=3000):
        print(chunk, end="")
"""

import logging
from collections.abc import Generator

from openai import (
    APIConnectionError,
    APITimeoutError,
    BadRequestError,
    OpenAI,
    RateLimitError,
)

from config import settings

logger = logging.getLogger(__name__)

# 可重试的异常类型
RETRYABLE_ERRORS: tuple[type[Exception], ...] = (
    APITimeoutError, APIConnectionError, RateLimitError,
)


class ChatClient:
    """统一的 Chat 客户端，根据 llm_provider 自动选择后端。"""

    def __init__(self):
        self.provider = settings.llm_provider
        # Step 026b：统一走 effective_chat_*，让 .env 里可单独设
        # CHAT_API_KEY / CHAT_API_BASE 把 chat 通道指向另一家 provider
        # （例如 embedding 留智谱、chat 走百炼 GLM-5），不影响 embedding。
        self.client = OpenAI(
            api_key=settings.effective_chat_api_key,
            base_url=settings.effective_chat_base_url,
        )
        self.model = settings.effective_chat_model

    def complete(
        self,
        messages: list[dict[str, str]],
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        response_format: dict | None = None,
    ) -> str:
        """非流式补全，返回文本内容。

        ``response_format``：传 ``{"type": "json_object"}`` 可让兼容 OpenAI 的网关
        在模型层强制输出**语法合法**的 JSON（Step 026e Agent 决策协议用）。某些模型/
        网关不支持该参数，``_openai_complete`` 会自动降级重试一次（去掉该参数）。
        """
        model = model or self.model
        return self._openai_complete(
            messages, model, temperature, max_tokens, response_format
        )

    def complete_stream(
        self,
        messages: list[dict[str, str]],
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> Generator[str, None, None]:
        """流式补全，yield 文本片段。"""
        model = model or self.model
        yield from self._openai_stream(messages, model, temperature, max_tokens)

    # ── OpenAI 兼容实现 ──────────────────────────────────────────────────────

    def _openai_complete(self, messages, model, temperature, max_tokens, response_format=None):
        kwargs = {"model": model, "messages": messages}
        if temperature is not None:
            kwargs["temperature"] = temperature
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens
        if response_format is not None:
            kwargs["response_format"] = response_format
        try:
            response = self.client.chat.completions.create(**kwargs)
        except BadRequestError:
            # 某些模型/网关不支持 response_format（如 json_object）；降级重试一次。
            if response_format is None:
                raise
            logger.warning("response_format 不被支持，降级为普通补全重试一次")
            kwargs.pop("response_format", None)
            response = self.client.chat.completions.create(**kwargs)
        msg = response.choices[0].message
        # 智谱等模型可能将回答放在 reasoning_content 而非 content
        text = msg.content or getattr(msg, "reasoning_content", "") or ""
        return text.strip()

    def _openai_stream(self, messages, model, temperature, max_tokens):
        kwargs = {"model": model, "messages": messages, "stream": True}
        if temperature is not None:
            kwargs["temperature"] = temperature
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens
        stream = self.client.chat.completions.create(**kwargs)
        # 某些模型（如智谱 GLM）将回答放在 reasoning_content 而非 content。
        # 策略：先收集 reasoning_content；一旦出现 content 就丢弃 reasoning 并转为
        # yield content；若流结束仍无 content，则一次性 yield 全部 reasoning。
        reasoning_buffer = []
        content_started = False
        for chunk in stream:
            delta = chunk.choices[0].delta if chunk.choices else None
            if not delta:
                continue
            if delta.content:
                if not content_started:
                    content_started = True
                    reasoning_buffer.clear()
                yield delta.content
            elif getattr(delta, "reasoning_content", None):
                if not content_started:
                    reasoning_buffer.append(delta.reasoning_content)
        if not content_started and reasoning_buffer:
            yield "".join(reasoning_buffer)

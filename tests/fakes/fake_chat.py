"""`ChatPort` 内存 Fake：可预设响应序列，可断言收到的请求。"""

from __future__ import annotations

from domain.models import ChatResponse


class FakeChat:
    """按调用顺序返回预设响应；超出长度后循环复用最后一条。"""

    def __init__(
        self,
        responses: list[str] | None = None,
        *,
        token_usages: list[int] | None = None,
        input_token_usages: list[int] | None = None,
        output_token_usages: list[int] | None = None,
    ) -> None:
        self._responses = list(responses) if responses else ["fake-response"]
        self._token_usages = list(token_usages) if token_usages else [0]
        self._input_token_usages = list(input_token_usages) if input_token_usages else [0]
        self._output_token_usages = list(output_token_usages) if output_token_usages else [0]
        self.calls: list[dict] = []

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
        self.calls.append(
            {
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
                "json_mode": json_mode,
            }
        )
        idx = min(len(self.calls) - 1, len(self._responses) - 1)
        usage_idx = min(len(self.calls) - 1, len(self._token_usages) - 1)
        input_idx = min(len(self.calls) - 1, len(self._input_token_usages) - 1)
        output_idx = min(len(self.calls) - 1, len(self._output_token_usages) - 1)
        input_tokens = self._input_token_usages[input_idx]
        output_tokens = self._output_token_usages[output_idx]
        return ChatResponse(
            content=self._responses[idx],
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            token_usage=max(
                self._token_usages[usage_idx],
                input_tokens + output_tokens,
            ),
        )

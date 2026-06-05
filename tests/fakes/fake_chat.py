"""`ChatPort` 内存 Fake：可预设响应序列，可断言收到的请求。"""

from __future__ import annotations


class FakeChat:
    """按调用顺序返回预设响应；超出长度后循环复用最后一条。"""

    def __init__(self, responses: list[str] | None = None) -> None:
        self._responses = list(responses) if responses else ["fake-response"]
        self.calls: list[dict] = []

    def chat(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.2,
        max_tokens: int | None = None,
    ) -> str:
        self.calls.append(
            {
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
            }
        )
        idx = min(len(self.calls) - 1, len(self._responses) - 1)
        return self._responses[idx]

"""`ChatPort` 的 OpenAI 兼容实现。

包装现有 `retrieval/generation/chat_client.ChatClient`，把 `complete()`
适配成 `ChatPort.chat()` 的签名。

注入策略：
- 默认懒构造一个 `ChatClient`（继承 `config.settings`），
- 测试可注入 mock：`OpenAIChatAdapter(client=mock_client)`。
"""

from __future__ import annotations

from typing import Protocol


class _ChatClientLike(Protocol):
    """与 `retrieval.generation.chat_client.ChatClient.complete` 等价的鸭子接口。"""

    def complete(
        self,
        messages: list[dict[str, str]],
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        response_format: dict | None = None,
    ) -> str: ...


class OpenAIChatAdapter:
    """实现 `ChatPort`。"""

    def __init__(self, client: _ChatClientLike | None = None) -> None:
        if client is None:
            # 懒导入避免单测加载时拉起 OpenAI SDK
            from retrieval.generation.chat_client import ChatClient

            client = ChatClient()
        self._client = client

    def chat(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.2,
        max_tokens: int | None = None,
        json_mode: bool = False,
    ) -> str:
        # json_mode=True 时请求网关在模型层强制输出语法合法的 JSON（Agent 决策协议）。
        response_format = {"type": "json_object"} if json_mode else None
        return self._client.complete(
            messages,
            temperature=temperature,
            max_tokens=max_tokens,
            response_format=response_format,
        )

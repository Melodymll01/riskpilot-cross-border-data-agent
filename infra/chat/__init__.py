"""ChatPort 适配器：统一使用 LangChain BaseChatModel。"""

from infra.chat.factory import build_chat_model
from infra.chat.langchain_chat import LangChainChatAdapter

__all__ = ["LangChainChatAdapter", "build_chat_model"]

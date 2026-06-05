"""RunQueryUseCase：简化版 RAG 入口（无 Agent，过渡占位）。

Step 008 占位实现：retrieve → 拼 context → chat → 返回答复 + citations。
Step 009 PR-5b 引入 ``ComplianceCopilotAgent`` 后，本 use case 将被替换为
"调 Agent 流式输出"的薄壳。当前实现保留是为了让 API 层（Step 010）有可调用入口。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal, TypedDict

from domain.models import Citation

if TYPE_CHECKING:
    from domain.ports import ChatPort, RetrievePort


_DEFAULT_SYSTEM_PROMPT = (
    "你是合规咨询助手。请基于"
    "下面提供的"
    "检索片段回答用户问题；"
    "若资料不足，请直说不知道，不要编造。"
    "答复以中文输出，必要时引用条款编号。"
)


class QueryResult(TypedDict):
    answer: str
    citations: list[Citation]
    used_chunks: int


class RunQueryUseCase:
    def __init__(self, *, retriever: RetrievePort, chat: ChatPort) -> None:
        self._retriever = retriever
        self._chat = chat

    def answer(
        self,
        owner_id: str,
        query: str,
        *,
        top_k: int = 5,
        corpus: Literal["law", "user_docs"] = "law",
        temperature: float = 0.2,
    ) -> QueryResult:
        if not query:
            msg = "query 不能为空"
            raise ValueError(msg)
        chunks = self._retriever.retrieve(
            query, top_k=top_k, corpus=corpus, owner_id=owner_id
        )
        context_text = self._format_context(chunks)
        messages = [
            {"role": "system", "content": _DEFAULT_SYSTEM_PROMPT},
            {"role": "user", "content": f"【资料】\n{context_text}\n\n【问题】\n{query}"},
        ]
        answer_text = self._chat.chat(messages, temperature=temperature)
        return {
            "answer": answer_text,
            "citations": [self._to_citation(c) for c in chunks],
            "used_chunks": len(chunks),
        }

    @staticmethod
    def _format_context(chunks: list) -> str:
        if not chunks:
            return "（未检索到相关条款）"
        lines = []
        for idx, ch in enumerate(chunks, 1):
            lines.append(f"[{idx}] 《{ch.source_name}》{ch.title}\n{ch.text}")
        return "\n\n".join(lines)

    @staticmethod
    def _to_citation(chunk) -> Citation:
        return Citation(
            source_type=chunk.source_type,
            source_name=chunk.source_name,
            title=chunk.title or chunk.source_name,
            source_url=chunk.source_url,
            text_snippet=chunk.text[:200],
        )

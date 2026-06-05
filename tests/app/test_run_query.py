"""RunQueryUseCase 单测：FakeRetrieve + FakeChat。"""

from __future__ import annotations

import pytest

from app.use_cases.run_query import RunQueryUseCase
from domain.models import Chunk
from tests.fakes import FakeChat, FakeRetrieve


def _chunks() -> list[Chunk]:
    return [
        Chunk(
            chunk_id="c1",
            text="第三十八条：个人信息出境应当具备下列条件之一…",
            source_type="law",
            source_name="个人信息保护法",
            title="第三十八条",
            source_url=None,
            category="法规",
            score=0.9,
            metadata={},
        ),
        Chunk(
            chunk_id="c2",
            text="第四条：数据处理者向境外提供数据有下列情形之一…",
            source_type="law",
            source_name="数据出境安全评估办法",
            title="第四条",
            source_url="https://example.gov/004",
            category="法规",
            score=0.8,
            metadata={},
        ),
    ]


class TestAnswer:
    def test_happy_path(self) -> None:
        retriever = FakeRetrieve(chunks=_chunks())
        chat = FakeChat(responses=["综合上述条款，您应当通过安全评估或签订标准合同。"])
        uc = RunQueryUseCase(retriever=retriever, chat=chat)
        result = uc.answer("anon:a", "个人信息能否出境？")
        assert result["used_chunks"] == 2
        assert "评估" in result["answer"]
        assert len(result["citations"]) == 2
        assert result["citations"][0].source_name == "个人信息保护法"

        # 校验 retriever 接到了 owner_id
        call = retriever.calls[0]
        assert call["owner_id"] == "anon:a"
        assert call["top_k"] == 5
        assert call["corpus"] == "law"

        # 校验 chat 收到了 system + user 两条
        msg = chat.calls[0]["messages"]
        assert msg[0]["role"] == "system"
        assert msg[1]["role"] == "user"
        assert "个人信息能否出境" in msg[1]["content"]
        assert "第三十八条" in msg[1]["content"]

    def test_no_chunks_still_calls_chat(self) -> None:
        chat = FakeChat(responses=["资料不足，无法判断。"])
        uc = RunQueryUseCase(retriever=FakeRetrieve(chunks=[]), chat=chat)
        result = uc.answer("anon:a", "随机问题")
        assert result["used_chunks"] == 0
        assert result["citations"] == []
        assert "（未检索到相关条款）" in chat.calls[0]["messages"][1]["content"]

    def test_empty_query_raises(self) -> None:
        uc = RunQueryUseCase(retriever=FakeRetrieve(), chat=FakeChat())
        with pytest.raises(ValueError):
            uc.answer("anon:a", "")

    def test_corpus_user_docs_passthrough(self) -> None:
        retriever = FakeRetrieve(chunks=_chunks())
        uc = RunQueryUseCase(retriever=retriever, chat=FakeChat())
        uc.answer("anon:a", "我的文档说什么", corpus="user_docs", top_k=3)
        call = retriever.calls[0]
        assert call["corpus"] == "user_docs"
        assert call["top_k"] == 3

"""``MemoryAssembler`` 装配 + 预算 + 降级测试（S-030a）。

覆盖三类重点之"降级"（memory=None 返回空串）与"注入"（排版 + token 预算）。
"""

from __future__ import annotations

import pytest

from app.memory import MemoryAssembler
from domain.models import Message
from tests.fakes.fake_memory import FakeMemory

pytestmark = pytest.mark.unit


def _msg(role: str, content: str, ts: float) -> Message:
    return Message(
        msg_id=f"m_{ts}",
        task_id="t1",
        role=role,  # type: ignore[arg-type]
        content=content,
        created_at=ts,
    )


class TestDegradation:
    def test_memory_none_returns_empty(self) -> None:
        asm = MemoryAssembler(None, recent_n=6, token_budget=1500)
        assert asm.assemble(owner_id="o1", task_id="t1") == ""

    def test_recent_n_zero_returns_empty(self) -> None:
        mem = FakeMemory(messages={"t1": [_msg("user", "hi", 1.0)]})
        asm = MemoryAssembler(mem, recent_n=0, token_budget=1500)
        assert asm.assemble(owner_id="o1", task_id="t1") == ""

    def test_no_history_returns_empty(self) -> None:
        mem = FakeMemory(messages={})
        asm = MemoryAssembler(mem, recent_n=6, token_budget=1500)
        assert asm.assemble(owner_id="o1", task_id="t1") == ""

    def test_memory_failure_degrades_to_empty(self) -> None:
        class _Boom:
            def recent_messages(self, *a: object, **k: object) -> list[Message]:
                raise RuntimeError("boom")

        asm = MemoryAssembler(_Boom(), recent_n=6, token_budget=1500)  # type: ignore[arg-type]
        assert asm.assemble(owner_id="o1", task_id="t1") == ""


class TestInjectionFormat:
    def test_renders_header_and_roles(self) -> None:
        mem = FakeMemory(
            messages={
                "t1": [
                    _msg("user", "数据出境要评估吗", 1.0),
                    _msg("assistant", "需要做安全评估", 2.0),
                ]
            }
        )
        asm = MemoryAssembler(mem, recent_n=6, token_budget=1500)

        block = asm.assemble(owner_id="o1", task_id="t1")

        assert "【历史对话" in block
        assert "用户：数据出境要评估吗" in block
        assert "助手：需要做安全评估" in block

    def test_passes_owner_and_n_to_memory(self) -> None:
        mem = FakeMemory(messages={"t1": [_msg("user", "x", 1.0)]})
        asm = MemoryAssembler(mem, recent_n=4, token_budget=1500)

        asm.assemble(owner_id="anon:o1", task_id="t1")

        assert mem.recent_calls == [("anon:o1", "t1", 4)]


class TestTokenBudget:
    def test_drops_oldest_when_over_budget(self) -> None:
        # 三条各约 100 字符；预算只够最近两条 + header。
        long = "字" * 100
        mem = FakeMemory(
            messages={
                "t1": [
                    _msg("user", "OLD" + long, 1.0),
                    _msg("assistant", "MID" + long, 2.0),
                    _msg("user", "NEW" + long, 3.0),
                ]
            }
        )
        asm = MemoryAssembler(mem, recent_n=6, token_budget=230)

        block = asm.assemble(owner_id="o1", task_id="t1")

        assert "NEW" in block  # 最近的保住
        assert "OLD" not in block  # 最旧的被裁掉

    def test_keeps_at_least_most_recent_even_if_over(self) -> None:
        # 单条就超预算：仍保留最近一条，避免完全空。
        huge = "字" * 500
        mem = FakeMemory(messages={"t1": [_msg("user", huge, 1.0)]})
        asm = MemoryAssembler(mem, recent_n=6, token_budget=10)

        block = asm.assemble(owner_id="o1", task_id="t1")

        assert huge in block

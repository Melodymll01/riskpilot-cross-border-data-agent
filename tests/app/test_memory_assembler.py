"""``MemoryAssembler`` 装配 + 预算 + 降级测试（S-030a）。

覆盖三类重点之"降级"（memory=None 返回空串）与"注入"（排版 + token 预算）。
"""

from __future__ import annotations

import pytest

from app.memory import MemoryAssembler
from domain.models import Fact, MemorySettings, Message, SessionProfile
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


class TestSummaryInjection:
    def test_summary_and_recent_both_rendered(self) -> None:
        mem = FakeMemory(
            messages={"t1": [_msg("user", "最近问题", 2.0)]},
            summaries={"t1": "更早聊过数据出境评估"},
        )
        asm = MemoryAssembler(mem, recent_n=6, token_budget=1500)

        block = asm.assemble(owner_id="o1", task_id="t1")

        assert "【对话摘要" in block
        assert "更早聊过数据出境评估" in block
        assert "【历史对话" in block
        assert "用户：最近问题" in block
        # 摘要排在最近原文之前
        assert block.index("【对话摘要") < block.index("【历史对话")

    def test_summary_only_when_no_recent(self) -> None:
        mem = FakeMemory(messages={}, summaries={"t1": "纯摘要内容"})
        asm = MemoryAssembler(mem, recent_n=6, token_budget=1500)

        block = asm.assemble(owner_id="o1", task_id="t1")

        assert "【对话摘要" in block
        assert "纯摘要内容" in block
        assert "【历史对话" not in block

    def test_summary_takes_budget_priority(self) -> None:
        # 摘要先吃预算：摘要超预算时被截断，但仍优先保留。
        summary = "摘" * 200
        mem = FakeMemory(messages={}, summaries={"t1": summary})
        asm = MemoryAssembler(mem, recent_n=6, token_budget=30)

        block = asm.assemble(owner_id="o1", task_id="t1")

        assert "【对话摘要" in block  # 摘要保住
        assert "摘" in block
        # 整体不超太多：被预算截断，远小于原始 200 字
        assert block.count("摘") < 200

    def test_summary_failure_degrades_to_recent_only(self) -> None:
        class _SummaryBoom:
            def get_summary(self, *a: object, **k: object) -> str:
                raise RuntimeError("boom")

            def recent_messages(self, *a: object, **k: object) -> list[Message]:
                return [_msg("user", "仍可用的历史", 1.0)]

        asm = MemoryAssembler(_SummaryBoom(), recent_n=6, token_budget=1500)  # type: ignore[arg-type]

        block = asm.assemble(owner_id="o1", task_id="t1")

        assert "用户：仍可用的历史" in block
        assert "【对话摘要" not in block


def _fact(text: str, owner_id: str = "o1") -> Fact:
    return Fact(fact_id=f"f_{abs(hash(text)) % 99999}", owner_id=owner_id, text=text)


class TestFactInjection:
    def test_no_recall_when_recall_k_zero(self) -> None:
        mem = FakeMemory(
            messages={"t1": [_msg("user", "x", 1.0)]},
            facts={"o1": [_fact("用户在跨境电商行业")]},
        )
        asm = MemoryAssembler(mem, recent_n=6, token_budget=1500, recall_k=0)

        block = asm.assemble(owner_id="o1", task_id="t1", query="行业")

        assert "【相关长期记忆" not in block
        assert mem.recall_calls == []

    def test_no_recall_when_query_blank(self) -> None:
        mem = FakeMemory(
            messages={"t1": [_msg("user", "x", 1.0)]},
            facts={"o1": [_fact("用户在跨境电商行业")]},
        )
        asm = MemoryAssembler(mem, recent_n=6, token_budget=1500, recall_k=3)

        block = asm.assemble(owner_id="o1", task_id="t1", query="   ")

        assert "【相关长期记忆" not in block
        assert mem.recall_calls == []

    def test_facts_injected_and_passed_query(self) -> None:
        mem = FakeMemory(
            messages={"t1": [_msg("user", "最近问题", 2.0)]},
            facts={
                "anon:o1": [
                    _fact("用户在跨境电商行业", owner_id="anon:o1"),
                    _fact("用户偏好中文回答", owner_id="anon:o1"),
                ]
            },
        )
        asm = MemoryAssembler(mem, recent_n=6, token_budget=1500, recall_k=3)

        block = asm.assemble(owner_id="anon:o1", task_id="t1", query="数据出境")

        assert "【相关长期记忆" in block
        assert "用户在跨境电商行业" in block
        assert "用户偏好中文回答" in block
        assert mem.recall_calls == [("anon:o1", "数据出境", 3)]

    def test_facts_rank_first_before_summary_and_recent(self) -> None:
        mem = FakeMemory(
            messages={"t1": [_msg("user", "最近问题", 2.0)]},
            summaries={"t1": "更早聊过评估"},
            facts={"o1": [_fact("用户在跨境电商行业")]},
        )
        asm = MemoryAssembler(mem, recent_n=6, token_budget=1500, recall_k=3)

        block = asm.assemble(owner_id="o1", task_id="t1", query="数据出境")

        assert block.index("【相关长期记忆") < block.index("【对话摘要")
        assert block.index("【对话摘要") < block.index("【历史对话")

    def test_recall_failure_degrades(self) -> None:
        class _Boom:
            def recall_semantic(self, *a: object, **k: object) -> list[Fact]:
                raise RuntimeError("boom")

            def get_summary(self, *a: object, **k: object) -> None:
                return None

            def recent_messages(self, *a: object, **k: object) -> list[Message]:
                return [_msg("user", "仍可用", 1.0)]

        asm = MemoryAssembler(_Boom(), recent_n=6, token_budget=1500, recall_k=3)  # type: ignore[arg-type]

        block = asm.assemble(owner_id="o1", task_id="t1", query="q")

        assert "用户：仍可用" in block
        assert "【相关长期记忆" not in block


class TestProfileInjection:
    def test_not_injected_when_max_facts_zero(self) -> None:
        mem = FakeMemory(
            messages={"t1": [_msg("user", "x", 1.0)]},
            profiles={"o1": SessionProfile(owner_id="o1", facts={"语言": "中文"})},
        )
        asm = MemoryAssembler(mem, recent_n=6, token_budget=1500)  # profile_max_facts=0

        block = asm.assemble(owner_id="o1", task_id="t1")

        assert "【用户画像" not in block

    def test_profile_rendered_when_enabled(self) -> None:
        mem = FakeMemory(
            messages={"t1": [_msg("user", "x", 1.0)]},
            profiles={
                "o1": SessionProfile(
                    owner_id="o1", facts={"语言": "中文", "行业": "跨境电商"}
                )
            },
        )
        asm = MemoryAssembler(
            mem, recent_n=6, token_budget=1500, profile_max_facts=8
        )

        block = asm.assemble(owner_id="o1", task_id="t1")

        assert "【用户画像" in block
        assert "- 语言：中文" in block
        assert "- 行业：跨境电商" in block

    def test_profile_truncated_at_max_facts(self) -> None:
        mem = FakeMemory(
            messages={"t1": [_msg("user", "x", 1.0)]},
            profiles={
                "o1": SessionProfile(
                    owner_id="o1", facts={"a": "1", "b": "2", "c": "3"}
                )
            },
        )
        asm = MemoryAssembler(
            mem, recent_n=6, token_budget=1500, profile_max_facts=2
        )

        block = asm.assemble(owner_id="o1", task_id="t1")

        assert block.count("- ") == 2

    def test_empty_profile_not_injected(self) -> None:
        mem = FakeMemory(
            messages={"t1": [_msg("user", "x", 1.0)]},
            profiles={"o1": SessionProfile(owner_id="o1", facts={})},
        )
        asm = MemoryAssembler(
            mem, recent_n=6, token_budget=1500, profile_max_facts=8
        )

        block = asm.assemble(owner_id="o1", task_id="t1")

        assert "【用户画像" not in block

    def test_profile_ranks_after_summary_before_recent(self) -> None:
        mem = FakeMemory(
            messages={"t1": [_msg("user", "最近问题", 2.0)]},
            summaries={"t1": "更早聊过评估"},
            profiles={"o1": SessionProfile(owner_id="o1", facts={"语言": "中文"})},
        )
        asm = MemoryAssembler(
            mem, recent_n=6, token_budget=1500, profile_max_facts=8
        )

        block = asm.assemble(owner_id="o1", task_id="t1")

        assert block.index("【对话摘要") < block.index("【用户画像")
        assert block.index("【用户画像") < block.index("【历史对话")

    def test_profile_failure_degrades(self) -> None:
        class _Boom:
            def get_profile(self, *a: object, **k: object) -> SessionProfile:
                raise RuntimeError("boom")

            def get_summary(self, *a: object, **k: object) -> None:
                return None

            def recent_messages(self, *a: object, **k: object) -> list[Message]:
                return [_msg("user", "仍可用", 1.0)]

        asm = MemoryAssembler(
            _Boom(), recent_n=6, token_budget=1500, profile_max_facts=8  # type: ignore[arg-type]
        )

        block = asm.assemble(owner_id="o1", task_id="t1")

        assert "用户：仍可用" in block
        assert "【用户画像" not in block


class TestSettingsGating:
    """记忆注入门控（S-032，对齐 ChatGPT）：

    - 当前任务上下文（L1 最近原文 + L2 本任务摘要）**永远注入**，不受开关控制；
    - ``use_saved_memory`` 门控 L4 长期事实 + L3 用户画像（跨会话保存的记忆）。
    """

    def _mem(self) -> FakeMemory:
        return FakeMemory(
            messages={"t1": [_msg("user", "最近问题", 2.0)]},
            summaries={"t1": "更早聊过评估"},
            facts={"o1": [_fact("用户在跨境电商行业")]},
            profiles={"o1": SessionProfile(owner_id="o1", facts={"语言": "中文"})},
        )

    def _asm(self, mem: FakeMemory, store: object) -> MemoryAssembler:
        return MemoryAssembler(
            mem,
            recent_n=6,
            token_budget=1500,
            recall_k=3,
            profile_max_facts=8,
            settings_store=store,  # type: ignore[arg-type]
        )

    def test_no_store_defaults_all_on(self) -> None:
        mem = self._mem()
        asm = MemoryAssembler(
            mem, recent_n=6, token_budget=1500, recall_k=3, profile_max_facts=8
        )
        block = asm.assemble(owner_id="o1", task_id="t1", query="数据出境")
        assert "【相关长期记忆" in block
        assert "【用户画像" in block
        assert "【对话摘要" in block
        assert "【历史对话" in block

    def test_both_on(self) -> None:
        from tests.fakes.fake_memory_settings_store import InMemoryMemorySettingsStore

        store = InMemoryMemorySettingsStore()
        store.upsert(
            MemorySettings(owner_id="o1", use_saved_memory=True)
        )
        block = self._asm(self._mem(), store).assemble(
            owner_id="o1", task_id="t1", query="数据出境"
        )
        assert "【相关长期记忆" in block
        assert "【用户画像" in block
        assert "【对话摘要" in block
        assert "【历史对话" in block

    def test_use_saved_memory_off_drops_facts_and_profile(self) -> None:
        from tests.fakes.fake_memory_settings_store import InMemoryMemorySettingsStore

        store = InMemoryMemorySettingsStore()
        store.upsert(
            MemorySettings(owner_id="o1", use_saved_memory=False)
        )
        block = self._asm(self._mem(), store).assemble(
            owner_id="o1", task_id="t1", query="数据出境"
        )
        assert "【相关长期记忆" not in block
        assert "【用户画像" not in block
        assert "【对话摘要" in block
        assert "【历史对话" in block

    def test_use_saved_memory_off_keeps_current_context(self) -> None:
        # 关闭开关：不掉 L4/L3；当前任务上下文（L1/L2）仍注入，非空
        from tests.fakes.fake_memory_settings_store import InMemoryMemorySettingsStore

        store = InMemoryMemorySettingsStore()
        store.upsert(
            MemorySettings(owner_id="o1", use_saved_memory=False)
        )
        block = self._asm(self._mem(), store).assemble(
            owner_id="o1", task_id="t1", query="数据出境"
        )
        assert "【相关长期记忆" not in block
        assert "【用户画像" not in block
        assert "【对话摘要" in block
        assert "【历史对话" in block

    def test_settings_read_failure_fails_open(self) -> None:
        class _Boom:
            def get(self, owner_id: str) -> MemorySettings:
                raise RuntimeError("boom")

        block = self._asm(self._mem(), _Boom()).assemble(
            owner_id="o1", task_id="t1", query="数据出境"
        )
        # fail-open：读取异常退回默认开，记忆照常注入
        assert "【相关长期记忆" in block
        assert "【历史对话" in block



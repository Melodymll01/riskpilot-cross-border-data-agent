"""``TaskBackedMemory`` L1 短期 + L2 摘要 + TTL 测试（S-030a/b）。

覆盖：隔离（owner 校验不泄露）、L2 增量摘要 + watermark 幂等、TTL 逻辑遗忘。
"""

from __future__ import annotations

import time

import pytest

from domain.models import ConsolidationState, Fact, Message, Task, TaskSummary
from infra.memory import TaskBackedMemory
from tests.fakes.fake_chat import FakeChat
from tests.fakes.fake_embed import FakeEmbed
from tests.fakes.fake_fact_store import FakeConsolidationStateStore, FakeFactStore
from tests.fakes.fake_profile_store import InMemoryProfileStore
from tests.fakes.fake_repos import InMemoryTaskRepo
from tests.fakes.fake_summary_store import InMemorySummaryStore

pytestmark = pytest.mark.unit

# 所有相对时间戳都锚到"现在"，避免默认 30 天 TTL 把 1970 纪元的旧消息过滤掉。
_NOW = time.time()


def _seed_task(repo: InMemoryTaskRepo, *, task_id: str, owner_id: str) -> None:
    repo.create(
        Task(
            task_id=task_id,
            owner_id=owner_id,
            title="t",
            state="planning",
            user_goal="",
            collected_facts={},
            created_at=_NOW,
            updated_at=_NOW,
        )
    )


def _msg(task_id: str, role: str, content: str, ts: float) -> Message:
    return Message(
        msg_id=f"m_{ts}",
        task_id=task_id,
        role=role,  # type: ignore[arg-type]
        content=content,
        created_at=_NOW + ts,  # 锚到现在，落在 TTL 窗口内
    )


class TestRecentMessages:
    def test_returns_last_n_in_order(self) -> None:
        repo = InMemoryTaskRepo()
        _seed_task(repo, task_id="t1", owner_id="anon:o1")
        for i in range(5):
            repo.append_message(_msg("t1", "user", f"q{i}", 1000.0 + i))
        mem = TaskBackedMemory(repo)

        out = mem.recent_messages("anon:o1", "t1", 3)

        assert [m.content for m in out] == ["q2", "q3", "q4"]

    def test_fewer_than_n_returns_all(self) -> None:
        repo = InMemoryTaskRepo()
        _seed_task(repo, task_id="t1", owner_id="anon:o1")
        repo.append_message(_msg("t1", "user", "only", 1.0))
        mem = TaskBackedMemory(repo)

        out = mem.recent_messages("anon:o1", "t1", 10)

        assert [m.content for m in out] == ["only"]

    def test_empty_task_returns_empty(self) -> None:
        repo = InMemoryTaskRepo()
        _seed_task(repo, task_id="t1", owner_id="anon:o1")
        mem = TaskBackedMemory(repo)

        assert mem.recent_messages("anon:o1", "t1", 5) == []

    def test_n_zero_returns_empty(self) -> None:
        repo = InMemoryTaskRepo()
        _seed_task(repo, task_id="t1", owner_id="anon:o1")
        repo.append_message(_msg("t1", "user", "q", 1.0))
        mem = TaskBackedMemory(repo)

        assert mem.recent_messages("anon:o1", "t1", 0) == []


class TestOwnerIsolation:
    def test_other_owner_gets_empty_no_leak(self) -> None:
        repo = InMemoryTaskRepo()
        _seed_task(repo, task_id="t1", owner_id="anon:owner_a")
        repo.append_message(_msg("t1", "user", "secret", 1.0))
        mem = TaskBackedMemory(repo)

        # owner_b 读 owner_a 的 task：必须空，不得泄露 "secret"
        out = mem.recent_messages("anon:owner_b", "t1", 5)

        assert out == []

    def test_owner_sees_own_messages(self) -> None:
        repo = InMemoryTaskRepo()
        _seed_task(repo, task_id="t1", owner_id="anon:owner_a")
        repo.append_message(_msg("t1", "user", "mine", 1.0))
        mem = TaskBackedMemory(repo)

        out = mem.recent_messages("anon:owner_a", "t1", 5)

        assert [m.content for m in out] == ["mine"]

    def test_unknown_task_returns_empty(self) -> None:
        repo = InMemoryTaskRepo()
        mem = TaskBackedMemory(repo)

        assert mem.recent_messages("anon:o1", "does_not_exist", 5) == []


class TestL1Ttl:
    def test_expired_messages_filtered(self) -> None:
        repo = InMemoryTaskRepo()
        _seed_task(repo, task_id="t1", owner_id="anon:o1")
        old = Message(
            msg_id="old",
            task_id="t1",
            role="user",
            content="过期内容",
            created_at=_NOW - 40 * 86400,  # 40 天前 > 30 天 TTL
        )
        repo.append_message(old)
        repo.append_message(_msg("t1", "user", "新内容", 0.0))
        mem = TaskBackedMemory(repo, l1_ttl_days=30.0)

        out = mem.recent_messages("anon:o1", "t1", 5)

        assert [m.content for m in out] == ["新内容"]

    def test_ttl_zero_disables_filter(self) -> None:
        repo = InMemoryTaskRepo()
        _seed_task(repo, task_id="t1", owner_id="anon:o1")
        old = Message(
            msg_id="old",
            task_id="t1",
            role="user",
            content="远古内容",
            created_at=1000.0,
        )
        repo.append_message(old)
        mem = TaskBackedMemory(repo, l1_ttl_days=0.0)

        out = mem.recent_messages("anon:o1", "t1", 5)

        assert [m.content for m in out] == ["远古内容"]


class TestL2Summary:
    def _mem(
        self, repo: InMemoryTaskRepo, store: InMemorySummaryStore, chat: FakeChat
    ) -> TaskBackedMemory:
        return TaskBackedMemory(
            repo, summary_store=store, chat=chat, summary_threshold=3
        )

    def test_summarizes_when_backlog_reaches_threshold(self) -> None:
        repo = InMemoryTaskRepo()
        _seed_task(repo, task_id="t1", owner_id="anon:o1")
        for i in range(3):
            repo.append_message(_msg("t1", "user", f"q{i}", i))
        store = InMemorySummaryStore()
        chat = FakeChat(responses=["摘要：用户问了三个问题"])
        mem = self._mem(repo, store, chat)

        mem.maybe_summarize("anon:o1", "t1")

        assert mem.get_summary("anon:o1", "t1") == "摘要：用户问了三个问题"
        rec = store.get("t1", "anon:o1")
        assert rec is not None and rec.msg_watermark == 3

    def test_below_threshold_is_noop(self) -> None:
        repo = InMemoryTaskRepo()
        _seed_task(repo, task_id="t1", owner_id="anon:o1")
        repo.append_message(_msg("t1", "user", "q0", 0))
        store = InMemorySummaryStore()
        chat = FakeChat(responses=["不该被调用"])
        mem = self._mem(repo, store, chat)

        mem.maybe_summarize("anon:o1", "t1")

        assert store.get("t1", "anon:o1") is None
        assert chat.calls == []  # LLM 没被触发

    def test_watermark_idempotent_no_double_summary(self) -> None:
        repo = InMemoryTaskRepo()
        _seed_task(repo, task_id="t1", owner_id="anon:o1")
        for i in range(3):
            repo.append_message(_msg("t1", "user", f"q{i}", i))
        store = InMemorySummaryStore()
        chat = FakeChat(responses=["第一次摘要"])
        mem = self._mem(repo, store, chat)

        mem.maybe_summarize("anon:o1", "t1")  # 触发
        mem.maybe_summarize("anon:o1", "t1")  # backlog 已清空 → 空操作

        assert len(chat.calls) == 1  # 只摘要一次

    def test_incremental_refine_includes_old_summary(self) -> None:
        repo = InMemoryTaskRepo()
        _seed_task(repo, task_id="t1", owner_id="anon:o1")
        for i in range(3):
            repo.append_message(_msg("t1", "user", f"q{i}", i))
        store = InMemorySummaryStore()
        chat = FakeChat(responses=["旧摘要", "新摘要"])
        mem = self._mem(repo, store, chat)
        mem.maybe_summarize("anon:o1", "t1")  # 第一次 → 旧摘要
        for i in range(3, 6):
            repo.append_message(_msg("t1", "user", f"q{i}", i))

        mem.maybe_summarize("anon:o1", "t1")  # 第二次 → 增量精炼

        # 第二次的 user prompt 必须带上"旧摘要"
        second_prompt = chat.calls[1]["messages"][1]["content"]
        assert "旧摘要" in second_prompt
        assert mem.get_summary("anon:o1", "t1") == "新摘要"

    def test_no_store_or_chat_is_noop(self) -> None:
        repo = InMemoryTaskRepo()
        _seed_task(repo, task_id="t1", owner_id="anon:o1")
        for i in range(5):
            repo.append_message(_msg("t1", "user", f"q{i}", i))
        mem = TaskBackedMemory(repo)  # 纯 L1，无 store/chat

        mem.maybe_summarize("anon:o1", "t1")  # 不抛

        assert mem.get_summary("anon:o1", "t1") is None

    def test_other_owner_summary_not_leaked(self) -> None:
        repo = InMemoryTaskRepo()
        _seed_task(repo, task_id="t1", owner_id="anon:owner_a")
        for i in range(3):
            repo.append_message(_msg("t1", "user", f"q{i}", i))
        store = InMemorySummaryStore()
        chat = FakeChat(responses=["机密摘要"])
        mem = self._mem(repo, store, chat)
        mem.maybe_summarize("anon:owner_a", "t1")

        assert mem.get_summary("anon:owner_b", "t1") is None


class TestL2Ttl:
    def test_expired_summary_not_returned(self) -> None:
        repo = InMemoryTaskRepo()
        _seed_task(repo, task_id="t1", owner_id="anon:o1")
        store = InMemorySummaryStore()
        store.upsert(
            TaskSummary(
                task_id="t1",
                owner_id="anon:o1",
                summary="陈旧摘要",
                msg_watermark=3,
                updated_at=_NOW - 200 * 86400,  # 200 天前 > 180 天 TTL
            )
        )
        mem = TaskBackedMemory(repo, summary_store=store, l2_ttl_days=180.0)

        assert mem.get_summary("anon:o1", "t1") is None


class TestL5RecallHistory:
    """跨对话历史召回（参考历史聊天记录，Step 033）。"""

    def _seed(self, repo: InMemoryTaskRepo, task_id: str, owner: str, updated: float) -> None:
        repo.create(
            Task(
                task_id=task_id,
                owner_id=owner,
                title="t",
                state="planning",
                user_goal="",
                collected_facts={},
                created_at=_NOW,
                updated_at=updated,
            )
        )

    def test_returns_other_task_summaries_recency_ordered(self) -> None:
        repo = InMemoryTaskRepo()
        self._seed(repo, "cur", "anon:o1", _NOW)
        self._seed(repo, "old", "anon:o1", _NOW - 100)
        self._seed(repo, "new", "anon:o1", _NOW - 10)
        store = InMemorySummaryStore()
        store.upsert(TaskSummary(task_id="cur", owner_id="anon:o1", summary="当前对话"))
        store.upsert(TaskSummary(task_id="old", owner_id="anon:o1", summary="较旧对话"))
        store.upsert(TaskSummary(task_id="new", owner_id="anon:o1", summary="较新对话"))
        mem = TaskBackedMemory(repo, summary_store=store)

        out = mem.recall_history("anon:o1", "cur", 5)

        # 排除当前 task，按 updated_at 倒序（new 先于 old）
        assert [r.summary for r in out] == ["较新对话", "较旧对话"]

    def test_excludes_current_task(self) -> None:
        repo = InMemoryTaskRepo()
        self._seed(repo, "cur", "anon:o1", _NOW)
        store = InMemorySummaryStore()
        store.upsert(TaskSummary(task_id="cur", owner_id="anon:o1", summary="当前对话"))
        mem = TaskBackedMemory(repo, summary_store=store)

        assert mem.recall_history("anon:o1", "cur", 5) == []

    def test_respects_k_limit(self) -> None:
        repo = InMemoryTaskRepo()
        self._seed(repo, "cur", "anon:o1", _NOW)
        for i in range(4):
            self._seed(repo, f"o{i}", "anon:o1", _NOW - i - 1)
        store = InMemorySummaryStore()
        for i in range(4):
            store.upsert(
                TaskSummary(task_id=f"o{i}", owner_id="anon:o1", summary=f"对话{i}")
            )
        mem = TaskBackedMemory(repo, summary_store=store)

        out = mem.recall_history("anon:o1", "cur", 2)

        assert len(out) == 2

    def test_owner_isolation(self) -> None:
        repo = InMemoryTaskRepo()
        self._seed(repo, "cur", "anon:o1", _NOW)
        self._seed(repo, "other", "anon:o2", _NOW - 10)
        store = InMemorySummaryStore()
        store.upsert(TaskSummary(task_id="other", owner_id="anon:o2", summary="别人的对话"))
        mem = TaskBackedMemory(repo, summary_store=store)

        assert mem.recall_history("anon:o1", "cur", 5) == []

    def test_skips_expired_summaries(self) -> None:
        repo = InMemoryTaskRepo()
        self._seed(repo, "cur", "anon:o1", _NOW)
        self._seed(repo, "stale", "anon:o1", _NOW - 10)
        store = InMemorySummaryStore()
        store.upsert(
            TaskSummary(
                task_id="stale",
                owner_id="anon:o1",
                summary="陈旧对话",
                updated_at=_NOW - 200 * 86400,  # > 180 天 TTL
            )
        )
        mem = TaskBackedMemory(repo, summary_store=store, l2_ttl_days=180.0)

        assert mem.recall_history("anon:o1", "cur", 5) == []

    def test_no_store_returns_empty(self) -> None:
        repo = InMemoryTaskRepo()
        self._seed(repo, "cur", "anon:o1", _NOW)
        mem = TaskBackedMemory(repo)

        assert mem.recall_history("anon:o1", "cur", 5) == []

    def test_k_zero_returns_empty(self) -> None:
        repo = InMemoryTaskRepo()
        self._seed(repo, "cur", "anon:o1", _NOW)
        self._seed(repo, "other", "anon:o1", _NOW - 10)
        store = InMemorySummaryStore()
        store.upsert(TaskSummary(task_id="other", owner_id="anon:o1", summary="对话"))
        mem = TaskBackedMemory(repo, summary_store=store)

        assert mem.recall_history("anon:o1", "cur", 0) == []


class TestL3Profile:
    def test_no_store_returns_empty_profile(self) -> None:
        mem = TaskBackedMemory(InMemoryTaskRepo())  # 无 profile_store
        prof = mem.get_profile("anon:o1")
        assert prof.owner_id == "anon:o1"
        assert prof.facts == {}

    def test_update_then_get_merges(self) -> None:
        store = InMemoryProfileStore()
        mem = TaskBackedMemory(InMemoryTaskRepo(), profile_store=store)

        mem.update_profile("anon:o1", {"语言": "中文"})
        mem.update_profile("anon:o1", {"行业": "跨境电商", "语言": "英文"})

        prof = mem.get_profile("anon:o1")
        assert prof.facts == {"语言": "英文", "行业": "跨境电商"}

    def test_update_empty_is_noop(self) -> None:
        store = InMemoryProfileStore()
        mem = TaskBackedMemory(InMemoryTaskRepo(), profile_store=store)
        mem.update_profile("anon:o1", {})
        assert store.get("anon:o1") is None

    def test_no_store_update_silently_skips(self) -> None:
        mem = TaskBackedMemory(InMemoryTaskRepo())  # 无 profile_store
        mem.update_profile("anon:o1", {"k": "v"})  # 不抛
        assert mem.get_profile("anon:o1").facts == {}


class TestForget:
    @staticmethod
    def _fact(owner_id: str, text: str) -> Fact:
        return Fact(
            fact_id=f"f_{abs(hash(text)) % 99999}",
            owner_id=owner_id,
            text=text,
            created_at=_NOW,
        )

    def _mem(self) -> tuple[TaskBackedMemory, dict]:
        repo = InMemoryTaskRepo()
        summary = InMemorySummaryStore()
        profile = InMemoryProfileStore()
        facts = FakeFactStore()
        state = FakeConsolidationStateStore()
        embed = FakeEmbed()
        mem = TaskBackedMemory(
            repo,
            summary_store=summary,
            profile_store=profile,
            fact_store=facts,
            state_store=state,
        )
        return mem, {
            "repo": repo,
            "summary": summary,
            "profile": profile,
            "facts": facts,
            "state": state,
            "embed": embed,
        }

    def _seed_owner(self, mem: TaskBackedMemory, dep: dict, owner: str) -> None:
        _seed_task(dep["repo"], task_id=f"{owner}_t", owner_id=owner)
        dep["summary"].upsert(
            TaskSummary(task_id=f"{owner}_t", owner_id=owner, summary="s", msg_watermark=1)
        )
        dep["state"].upsert(
            ConsolidationState(task_id=f"{owner}_t", owner_id=owner, msg_watermark=1)
        )
        mem.update_profile(owner, {"语言": "中文"})
        f = self._fact(owner, f"{owner} 的事实")
        dep["facts"].add(f, dep["embed"].embed([f.text])[0])

    def test_memory_scope_clears_derived_keeps_tasks(self) -> None:
        mem, dep = self._mem()
        self._seed_owner(mem, dep, "anon:o1")

        result = mem.forget("anon:o1", scope="memory")

        assert result.scope == "memory"
        assert result.summaries_deleted == 1
        assert result.profile_deleted == 1
        assert result.facts_deleted == 1
        assert result.states_deleted == 1
        assert result.tasks_deleted == 0
        # L1 原始 task 保留
        assert dep["repo"].get("anon:o1_t", "anon:o1") is not None
        # 派生记忆清空
        assert mem.get_profile("anon:o1").facts == {}
        assert dep["facts"].count("anon:o1") == 0

    def test_all_scope_also_deletes_tasks(self) -> None:
        mem, dep = self._mem()
        self._seed_owner(mem, dep, "anon:o1")

        result = mem.forget("anon:o1", scope="all")

        assert result.scope == "all"
        assert result.tasks_deleted == 1
        assert dep["repo"].get("anon:o1_t", "anon:o1") is None

    def test_owner_isolation(self) -> None:
        mem, dep = self._mem()
        self._seed_owner(mem, dep, "anon:o1")
        self._seed_owner(mem, dep, "anon:o2")

        mem.forget("anon:o1", scope="all")

        # o2 完全不受影响
        assert dep["repo"].get("anon:o2_t", "anon:o2") is not None
        assert mem.get_profile("anon:o2").facts == {"语言": "中文"}
        assert dep["facts"].count("anon:o2") == 1

    def test_unknown_scope_falls_back_to_memory(self) -> None:
        mem, dep = self._mem()
        self._seed_owner(mem, dep, "anon:o1")

        result = mem.forget("anon:o1", scope="weird")

        assert result.scope == "memory"
        assert result.tasks_deleted == 0

    def test_missing_stores_count_zero(self) -> None:
        mem = TaskBackedMemory(InMemoryTaskRepo())  # 无任何派生 store
        result = mem.forget("anon:o1", scope="memory")
        assert result.total_deleted == 0


class TestL4RecallSemantic:
    @staticmethod
    def _fact(
        owner_id: str,
        text: str,
        *,
        superseded_by: str | None = None,
        created_offset: float = 0.0,
    ) -> Fact:
        return Fact(
            fact_id=f"f_{abs(hash(text)) % 99999}",
            owner_id=owner_id,
            text=text,
            superseded_by=superseded_by,
            created_at=_NOW + created_offset,
        )

    def test_no_fact_store_returns_empty(self) -> None:
        mem = TaskBackedMemory(InMemoryTaskRepo())  # 无 fact_store/embedder
        assert mem.recall_semantic("anon:o1", "q", 3) == []

    def test_blank_query_or_zero_k_returns_empty(self) -> None:
        store = FakeFactStore()
        embed = FakeEmbed()
        store.add(self._fact("anon:o1", "用户在跨境电商行业"), embed.embed(["x"])[0])
        mem = TaskBackedMemory(
            InMemoryTaskRepo(), fact_store=store, embedder=embed
        )

        assert mem.recall_semantic("anon:o1", "  ", 3) == []
        assert mem.recall_semantic("anon:o1", "q", 0) == []

    def test_returns_facts_for_owner(self) -> None:
        store = FakeFactStore()
        embed = FakeEmbed()
        fact = self._fact("anon:o1", "用户在跨境电商行业")
        store.add(fact, embed.embed([fact.text])[0])
        mem = TaskBackedMemory(
            InMemoryTaskRepo(), fact_store=store, embedder=embed
        )

        out = mem.recall_semantic("anon:o1", "行业是什么", 3)

        assert [f.text for f in out] == ["用户在跨境电商行业"]

    def test_superseded_fact_filtered(self) -> None:
        store = FakeFactStore()
        embed = FakeEmbed()
        fact = self._fact("anon:o1", "用户偏好英文", superseded_by="f_new")
        store.add(fact, embed.embed([fact.text])[0])
        mem = TaskBackedMemory(
            InMemoryTaskRepo(), fact_store=store, embedder=embed
        )

        assert mem.recall_semantic("anon:o1", "偏好", 3) == []

    def test_expired_fact_filtered_by_ttl(self) -> None:
        store = FakeFactStore()
        embed = FakeEmbed()
        old = self._fact("anon:o1", "很久以前的事实", created_offset=-400 * 86400)
        store.add(old, embed.embed([old.text])[0])
        mem = TaskBackedMemory(
            InMemoryTaskRepo(),
            fact_store=store,
            embedder=embed,
            l4_ttl_days=365.0,
        )

        assert mem.recall_semantic("anon:o1", "事实", 3) == []

    def test_other_owner_not_leaked(self) -> None:
        store = FakeFactStore()
        embed = FakeEmbed()
        fact = self._fact("anon:owner_a", "机密事实")
        store.add(fact, embed.embed([fact.text])[0])
        mem = TaskBackedMemory(
            InMemoryTaskRepo(), fact_store=store, embedder=embed
        )

        assert mem.recall_semantic("anon:owner_b", "事实", 3) == []


class TestL4ListFacts:
    @staticmethod
    def _fact(
        owner_id: str,
        text: str,
        *,
        superseded_by: str | None = None,
        created_offset: float = 0.0,
    ) -> Fact:
        return Fact(
            fact_id=f"f_{abs(hash(text)) % 99999}",
            owner_id=owner_id,
            text=text,
            superseded_by=superseded_by,
            created_at=_NOW + created_offset,
        )

    def test_no_fact_store_returns_empty(self) -> None:
        mem = TaskBackedMemory(InMemoryTaskRepo())
        assert mem.list_facts("anon:o1") == []

    def test_lists_active_facts_newest_first(self) -> None:
        store = FakeFactStore()
        embed = FakeEmbed()
        old = self._fact("anon:o1", "较早的事实", created_offset=-10.0)
        new = self._fact("anon:o1", "较新的事实", created_offset=-1.0)
        store.add(old, embed.embed([old.text])[0])
        store.add(new, embed.embed([new.text])[0])
        mem = TaskBackedMemory(InMemoryTaskRepo(), fact_store=store, embedder=embed)

        out = mem.list_facts("anon:o1")

        assert [f.text for f in out] == ["较新的事实", "较早的事实"]

    def test_superseded_filtered(self) -> None:
        store = FakeFactStore()
        embed = FakeEmbed()
        dead = self._fact("anon:o1", "被取代的事实", superseded_by="f_new")
        live = self._fact("anon:o1", "生效的事实")
        store.add(dead, embed.embed([dead.text])[0])
        store.add(live, embed.embed([live.text])[0])
        mem = TaskBackedMemory(InMemoryTaskRepo(), fact_store=store, embedder=embed)

        out = mem.list_facts("anon:o1")

        assert [f.text for f in out] == ["生效的事实"]

    def test_expired_filtered_by_ttl(self) -> None:
        store = FakeFactStore()
        embed = FakeEmbed()
        old = self._fact("anon:o1", "很久以前", created_offset=-400 * 86400)
        store.add(old, embed.embed([old.text])[0])
        mem = TaskBackedMemory(
            InMemoryTaskRepo(),
            fact_store=store,
            embedder=embed,
            l4_ttl_days=365.0,
        )

        assert mem.list_facts("anon:o1") == []

    def test_owner_isolation(self) -> None:
        store = FakeFactStore()
        embed = FakeEmbed()
        a = self._fact("anon:owner_a", "A 的事实")
        store.add(a, embed.embed([a.text])[0])
        mem = TaskBackedMemory(InMemoryTaskRepo(), fact_store=store, embedder=embed)

        assert mem.list_facts("anon:owner_b") == []

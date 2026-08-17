"""``ConsolidationWorker`` L4 固化管线测试（S-030c）。

覆盖：min_backlog 门控、提取→新增、显著性关、接地关、去重强化、冲突取代、
watermark 幂等、容量淘汰、owner 归属隔离、提取失败保留 watermark。
依赖全用 fake，确定性、离线。
"""

from __future__ import annotations

import json
import time

import pytest

from domain.models import Fact, Message, Task
from infra.memory.consolidation import ConsolidationWorker
from tests.fakes.fake_chat import FakeChat
from tests.fakes.fake_fact_store import FakeConsolidationStateStore, FakeFactStore
from tests.fakes.fake_repos import InMemoryTaskRepo

pytestmark = pytest.mark.unit

_NOW = time.time()

# 受控向量：用余弦相似度精确落在去重 / 冲突 / 新增三档。
_A_VEC = [1.0, 0.0, 0.0, 0.0]
_DEDUP_VEC = [1.0, 0.05, 0.0, 0.0]  # 与 _A_VEC cos≈0.999 ≥ 0.88 去重
_CONFLICT_VEC = [0.8, 0.6, 0.0, 0.0]  # 与 _A_VEC cos=0.8 落 [0.72,0.88) 冲突
_NEW_VEC = [0.0, 0.0, 1.0, 0.0]  # 与 _A_VEC cos=0 < 0.72 新增

_EXISTING_TEXT = "用户在跨境电商行业"
_DEDUP_TEXT = "用户从事跨境电商业务"
_CONFLICT_TEXT = "用户主营线下零售"
_NEW_TEXT = "用户偏好中文回答"


class _StubEmbed:
    """按文本映射到受控向量；未知文本回退到唯一正交向量。"""

    _MAP = {
        _EXISTING_TEXT: _A_VEC,
        _DEDUP_TEXT: _DEDUP_VEC,
        _CONFLICT_TEXT: _CONFLICT_VEC,
        _NEW_TEXT: _NEW_VEC,
    }

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._MAP.get(t, [0.0, 0.0, 0.0, 1.0]) for t in texts]


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


def _seed_messages(repo: InMemoryTaskRepo, task_id: str, n: int) -> None:
    for i in range(n):
        role = "user" if i % 2 == 0 else "assistant"
        content = f"这是第{i}条实质性消息内容"
        if i == 0:
            content = "；".join([content, _NEW_TEXT, _DEDUP_TEXT, _CONFLICT_TEXT])
        repo.append_message(
            Message(
                msg_id=f"m{i}",
                task_id=task_id,
                role=role,  # type: ignore[arg-type]
                content=content,
                created_at=_NOW + i,
            )
        )


def _facts_json(*candidates: dict) -> str:
    normalized: list[dict] = []
    for candidate in candidates:
        candidate_text = candidate.get("text")
        quote = (
            candidate_text
            if candidate_text in {_NEW_TEXT, _DEDUP_TEXT, _CONFLICT_TEXT}
            else "这是第0条实质性消息内容"
        )
        item = {
            "source_message_id": "m0",
            "quote": quote,
            "tags": [],
            **candidate,
        }
        item.pop("text", None)
        grounded = item.pop("grounded", None)
        if grounded is False:
            item["quote"] = "对话中不存在的伪造证据"
        normalized.append(item)
    return json.dumps({"facts": normalized})


def _worker(
    repo: InMemoryTaskRepo,
    chat: FakeChat,
    store: FakeFactStore,
    state: FakeConsolidationStateStore,
    *,
    min_backlog: int = 2,
    fact_cap: int = 500,
) -> ConsolidationWorker:
    return ConsolidationWorker(
        task_repo=repo,
        fact_store=store,
        embedder=_StubEmbed(),  # type: ignore[arg-type]
        chat=chat,
        state_store=state,
        min_backlog=min_backlog,
        salience_threshold=0.5,
        dedup_threshold=0.88,
        conflict_threshold=0.72,
        fact_cap_per_owner=fact_cap,
        decay_lambda=0.01,
    )


class TestGating:
    def test_below_min_backlog_noop(self) -> None:
        repo = InMemoryTaskRepo()
        _seed_task(repo, task_id="t1", owner_id="anon:o1")
        _seed_messages(repo, "t1", 1)
        chat = FakeChat()
        store = FakeFactStore()
        state = FakeConsolidationStateStore()
        w = _worker(repo, chat, store, state, min_backlog=5)

        w.consolidate("anon:o1", "t1")

        assert chat.calls == []
        assert store.count("anon:o1") == 0

    def test_owner_mismatch_noop(self) -> None:
        repo = InMemoryTaskRepo()
        _seed_task(repo, task_id="t1", owner_id="anon:owner_a")
        _seed_messages(repo, "t1", 4)
        chat = FakeChat()
        store = FakeFactStore()
        state = FakeConsolidationStateStore()
        w = _worker(repo, chat, store, state)

        w.consolidate("anon:owner_b", "t1")

        assert chat.calls == []
        assert store.count("anon:owner_a") == 0

    def test_extract_failure_keeps_watermark(self) -> None:
        repo = InMemoryTaskRepo()
        _seed_task(repo, task_id="t1", owner_id="anon:o1")
        _seed_messages(repo, "t1", 4)
        chat = FakeChat(responses=["not-json"])
        store = FakeFactStore()
        state = FakeConsolidationStateStore()
        w = _worker(repo, chat, store, state)

        w.consolidate("anon:o1", "t1")

        assert store.count("anon:o1") == 0
        assert state.get("t1", "anon:o1") is None  # 未推进，待重试


class TestExtractAndValidate:
    def test_adds_new_grounded_salient_fact(self) -> None:
        repo = InMemoryTaskRepo()
        _seed_task(repo, task_id="t1", owner_id="anon:o1")
        _seed_messages(repo, "t1", 4)
        chat = FakeChat(
            responses=[
                _facts_json(
                    {"text": _NEW_TEXT, "salience": 0.9, "grounded": True, "tags": ["偏好"]}
                )
            ]
        )
        store = FakeFactStore()
        state = FakeConsolidationStateStore()
        w = _worker(repo, chat, store, state)

        w.consolidate("anon:o1", "t1")

        facts = store.list_owner("anon:o1")
        assert [f.text for f in facts] == [_NEW_TEXT]
        assert facts[0].confidence == 0.5  # tentative 首次低置信
        assert facts[0].source_message_id == "m0"
        assert facts[0].source_quote == _NEW_TEXT
        # watermark 推进到消息总数
        st = state.get("t1", "anon:o1")
        assert st is not None and st.msg_watermark == 4

    def test_low_salience_dropped(self) -> None:
        repo = InMemoryTaskRepo()
        _seed_task(repo, task_id="t1", owner_id="anon:o1")
        _seed_messages(repo, "t1", 4)
        chat = FakeChat(
            responses=[_facts_json({"text": _NEW_TEXT, "salience": 0.2, "grounded": True})]
        )
        store = FakeFactStore()
        state = FakeConsolidationStateStore()
        w = _worker(repo, chat, store, state)

        w.consolidate("anon:o1", "t1")

        assert store.count("anon:o1") == 0

    def test_forged_quote_dropped(self) -> None:
        repo = InMemoryTaskRepo()
        _seed_task(repo, task_id="t1", owner_id="anon:o1")
        _seed_messages(repo, "t1", 4)
        chat = FakeChat(
            responses=[_facts_json({"text": _NEW_TEXT, "salience": 0.9, "grounded": False})]
        )
        store = FakeFactStore()
        state = FakeConsolidationStateStore()
        w = _worker(repo, chat, store, state)

        w.consolidate("anon:o1", "t1")

        assert store.count("anon:o1") == 0

    def test_assistant_content_is_not_sent_to_extractor(self) -> None:
        repo = InMemoryTaskRepo()
        _seed_task(repo, task_id="t1", owner_id="anon:o1")
        _seed_messages(repo, "t1", 4)
        chat = FakeChat(responses=[_facts_json()])
        store = FakeFactStore()
        state = FakeConsolidationStateStore()
        w = _worker(repo, chat, store, state)

        w.consolidate("anon:o1", "t1")

        prompt = chat.calls[0]["messages"][1]["content"]
        assert '"message_id": "m0"' in prompt
        assert '"message_id": "m2"' in prompt
        assert "这是第1条实质性消息内容" not in prompt
        assert "这是第3条实质性消息内容" not in prompt

    @pytest.mark.parametrize(
        "candidate",
        [
            {
                "text": _NEW_TEXT,
                "salience": 0.9,
                "source_message_id": "m_missing",
                "quote": "这是第0条实质性消息内容",
            },
            {
                "text": _NEW_TEXT,
                "salience": 0.9,
                "source_message_id": "m0",
                "quote": "这是模型改写后的内容",
            },
            {
                "text": _NEW_TEXT,
                "salience": True,
            },
            {
                "text": _NEW_TEXT,
                "salience": float("nan"),
            },
        ],
    )
    def test_invalid_candidate_protocol_is_dropped(self, candidate: dict) -> None:
        repo = InMemoryTaskRepo()
        _seed_task(repo, task_id="t1", owner_id="anon:o1")
        _seed_messages(repo, "t1", 4)
        chat = FakeChat(responses=[_facts_json(candidate)])
        store = FakeFactStore()
        state = FakeConsolidationStateStore()
        w = _worker(repo, chat, store, state)

        w.consolidate("anon:o1", "t1")

        assert store.count("anon:o1") == 0

    @pytest.mark.parametrize(
        "secret",
        [
            "我的 API Key 是 sk-abcdefghijklmnop",
            "password: CorrectHorseBatteryStaple",
            "我的手机号是 13800138000",
            "联系邮箱是 alice@example.com",
            "身份证号是 11010519491231002X",
        ],
    )
    def test_sensitive_secret_is_never_persisted(self, secret: str) -> None:
        repo = InMemoryTaskRepo()
        _seed_task(repo, task_id="t1", owner_id="anon:o1")
        repo.append_message(
            Message(
                msg_id="m_secret",
                task_id="t1",
                role="user",
                content=secret,
                created_at=_NOW,
            )
        )
        repo.append_message(
            Message(
                msg_id="m_followup",
                task_id="t1",
                role="user",
                content="请记住上面的内容",
                created_at=_NOW + 1,
            )
        )
        chat = FakeChat(
            responses=[
                _facts_json(
                    {
                        "text": secret,
                        "salience": 1.0,
                        "source_message_id": "m_secret",
                        "quote": secret,
                    }
                )
            ]
        )
        store = FakeFactStore()
        state = FakeConsolidationStateStore()
        w = _worker(repo, chat, store, state)

        w.consolidate("anon:o1", "t1")

        assert store.count("anon:o1") == 0
        prompt = chat.calls[0]["messages"][1]["content"]
        assert secret not in prompt

    def test_candidate_count_is_bounded(self) -> None:
        repo = InMemoryTaskRepo()
        _seed_task(repo, task_id="t1", owner_id="anon:o1")
        _seed_messages(repo, "t1", 4)
        candidates = [
            {
                "text": f"用户偏好第 {index} 种回答方式",
                "salience": 0.9,
            }
            for index in range(15)
        ]
        chat = FakeChat(responses=[_facts_json(*candidates)])
        store = FakeFactStore()
        state = FakeConsolidationStateStore()
        w = _worker(repo, chat, store, state)

        episode = w._build_extraction_episode(repo.list_messages("t1"))  # noqa: SLF001
        assert episode is not None
        extracted = w._extract(episode)  # noqa: SLF001

        assert len(extracted) == 10
        assert extracted[-1].text == "这是第0条实质性消息内容"


class TestConflictForgetting:
    def test_dedup_reinforces_not_adds(self) -> None:
        repo = InMemoryTaskRepo()
        _seed_task(repo, task_id="t1", owner_id="anon:o1")
        _seed_messages(repo, "t1", 4)
        store = FakeFactStore()
        existing = Fact(
            fact_id="f_existing",
            owner_id="anon:o1",
            text=_EXISTING_TEXT,
            confidence=0.5,
            salience=0.6,
            created_at=_NOW,
        )
        store.add(existing, _A_VEC)
        chat = FakeChat(
            responses=[_facts_json({"text": _DEDUP_TEXT, "salience": 0.7, "grounded": True})]
        )
        state = FakeConsolidationStateStore()
        w = _worker(repo, chat, store, state)

        w.consolidate("anon:o1", "t1")

        facts = store.list_owner("anon:o1")
        assert len(facts) == 1  # 未新增
        assert facts[0].fact_id == "f_existing"
        assert facts[0].confidence > 0.5  # 置信被强化

    def test_conflict_supersedes_old_and_adds_new(self) -> None:
        repo = InMemoryTaskRepo()
        _seed_task(repo, task_id="t1", owner_id="anon:o1")
        _seed_messages(repo, "t1", 4)
        store = FakeFactStore()
        existing = Fact(
            fact_id="f_old",
            owner_id="anon:o1",
            text=_EXISTING_TEXT,
            created_at=_NOW,
        )
        store.add(existing, _A_VEC)
        chat = FakeChat(
            responses=[_facts_json({"text": _CONFLICT_TEXT, "salience": 0.8, "grounded": True})]
        )
        state = FakeConsolidationStateStore()
        w = _worker(repo, chat, store, state)

        w.consolidate("anon:o1", "t1")

        all_facts = store.list_owner("anon:o1")
        active = [f for f in all_facts if f.superseded_by is None]
        old = store.get("anon:o1", "f_old")
        assert len(all_facts) == 2  # 旧的保留（标记），新的写入
        assert old is not None and old.superseded_by is not None
        assert [f.text for f in active] == [_CONFLICT_TEXT]


class TestIdempotencyAndCapacity:
    def test_watermark_idempotent(self) -> None:
        repo = InMemoryTaskRepo()
        _seed_task(repo, task_id="t1", owner_id="anon:o1")
        _seed_messages(repo, "t1", 4)
        chat = FakeChat(
            responses=[_facts_json({"text": _NEW_TEXT, "salience": 0.9, "grounded": True})]
        )
        store = FakeFactStore()
        state = FakeConsolidationStateStore()
        w = _worker(repo, chat, store, state)

        w.consolidate("anon:o1", "t1")
        w.consolidate("anon:o1", "t1")  # backlog 已空，应空操作

        assert len(chat.calls) == 1  # 第二轮不再调用 LLM
        assert store.count("anon:o1") == 1

    def test_capacity_eviction_drops_lowest_decay(self) -> None:
        repo = InMemoryTaskRepo()
        _seed_task(repo, task_id="t1", owner_id="anon:o1")
        _seed_messages(repo, "t1", 4)
        store = FakeFactStore()
        # 既有事实：低显著性 + 很旧 → 衰减分最低，应被淘汰。
        stale = Fact(
            fact_id="f_stale",
            owner_id="anon:o1",
            text=_EXISTING_TEXT,
            salience=0.1,
            created_at=_NOW - 400 * 86400,
        )
        store.add(stale, _A_VEC)
        chat = FakeChat(
            responses=[_facts_json({"text": _NEW_TEXT, "salience": 0.9, "grounded": True})]
        )
        state = FakeConsolidationStateStore()
        w = _worker(repo, chat, store, state, fact_cap=1)

        w.consolidate("anon:o1", "t1")

        remaining = store.list_owner("anon:o1")
        assert [f.text for f in remaining] == [_NEW_TEXT]
        assert store.get("anon:o1", "f_stale") is None

"""L4 记忆固化管线：提取 → 验证 → 巩固（Step 030c，§3/§14.3）。

``ConsolidationWorker.consolidate`` 在 fork 后台执行（由 ``MemoryJobSchedulerPort`` 触发），
best-effort，绝不阻塞主回复。三段式：

1. **提取 Extract**：规则预过滤选出"值得送检"的对话片段 → 轻量 LLM 提炼候选事实。
   口诀：规则管"要不要送检"，LLM 管"如何表达"，验证器管"能不能入库"。
2. **验证 Validate**：四关——接地（grounded 标记）/ 显著性（salience 阈值）/
   去重（近邻相似度 ≥ dedup → 强化置信）/ 冲突（相似度落 [conflict, dedup) → 旧事实标 superseded）。
3. **巩固 Consolidate**：通过验证的候选 embed + 落库；超容量按衰减分淘汰最低分。

幂等：``ConsolidationStatePort`` 的 ``msg_watermark`` 记进度，重试不重复写 fact、
漏固化下一轮按差额自动补。
"""

from __future__ import annotations

import json
import logging
import math
import time
import uuid
from typing import TYPE_CHECKING, Any

from domain.models import ConsolidationState, Fact

if TYPE_CHECKING:
    from domain.ports import (
        ChatPort,
        ConsolidationStatePort,
        EmbedPort,
        FactStorePort,
        TaskRepoPort,
    )

logger = logging.getLogger(__name__)

_SECONDS_PER_DAY = 86400.0

_EXTRACT_SYSTEM = (
    "你是用户长期记忆的提取器。从给定对话中提炼**稳定、可长期复用**的用户事实："
    "偏好、所属行业、关注的法规、已明确确认的业务约束等。"
    "严禁提取临时问题、闲聊、工具中间结果、未经确认的假设或对身份的敏感推断。"
    "只输出 JSON，格式："
    '{"facts": [{"text": "一句话事实", "salience": 0.0到1.0的重要性, '
    '"tags": ["标签"], "grounded": true表示对话中有明确证据}]}。'
    "无可提炼时 facts 为空数组。"
)


class ConsolidationWorker:
    """提取-验证-巩固固化 worker（依赖全为 Port，便于测试）。"""

    def __init__(
        self,
        *,
        task_repo: TaskRepoPort,
        fact_store: FactStorePort,
        embedder: EmbedPort,
        chat: ChatPort,
        state_store: ConsolidationStatePort,
        min_backlog: int = 30,
        salience_threshold: float = 0.5,
        dedup_threshold: float = 0.88,
        conflict_threshold: float = 0.72,
        fact_cap_per_owner: int = 500,
        decay_lambda: float = 0.01,
        reinforce_step: float = 0.2,
    ) -> None:
        self._repo = task_repo
        self._facts = fact_store
        self._embedder = embedder
        self._chat = chat
        self._state = state_store
        self._min_backlog = min_backlog
        self._salience_threshold = salience_threshold
        self._dedup_threshold = dedup_threshold
        self._conflict_threshold = conflict_threshold
        self._cap = fact_cap_per_owner
        self._lambda = decay_lambda
        self._reinforce_step = reinforce_step

    def consolidate(self, owner_id: str, task_id: str) -> None:
        """对单个 task 的未固化 backlog 跑一遍固化管线（幂等 + best-effort）。"""
        # 归属校验：非本人 task 一律不处理，绝不泄露。
        if self._repo.get(task_id, owner_id) is None:
            return

        msgs = self._repo.list_messages(task_id)
        state = self._state.get(task_id, owner_id)
        watermark = state.msg_watermark if state else 0
        watermark = max(0, min(watermark, len(msgs)))
        backlog = msgs[watermark:]
        if len(backlog) < self._min_backlog:
            return  # backlog 不足，等下一轮

        episode = self._rule_prefilter(backlog)
        if not episode:
            self._advance(task_id, owner_id, watermark + len(backlog))
            return

        try:
            candidates = self._extract(episode)
        except Exception:  # noqa: BLE001 — 后台 best-effort，失败保留 watermark 待重试
            logger.warning("L4 候选提取失败，保留 watermark 待下轮重试", exc_info=True)
            return

        for cand in candidates:
            try:
                self._process_candidate(owner_id, task_id, cand)
            except Exception:  # noqa: BLE001 — 单条失败不拖累整批
                logger.warning("L4 候选处理失败，跳过该条", exc_info=True)

        try:
            self._enforce_capacity(owner_id)
        except Exception:  # noqa: BLE001 — 容量淘汰失败不影响主固化
            logger.warning("L4 容量淘汰失败（已忽略）", exc_info=True)

        self._advance(task_id, owner_id, watermark + len(backlog))

    # ── ① 提取 ──────────────────────────────────────────────────────────────

    @staticmethod
    def _rule_prefilter(backlog: list[Any]) -> str:
        """规则预过滤：只把 user/assistant 的实质内容送检，滤掉工具/系统/过短消息。"""
        lines: list[str] = []
        for m in backlog:
            if m.role not in ("user", "assistant"):
                continue
            content = (m.content or "").strip()
            if len(content) < 4:  # 过短（寒暄/确认）不送检
                continue
            label = "用户" if m.role == "user" else "助手"
            lines.append(f"{label}: {content}")
        return "\n".join(lines)

    def _extract(self, episode: str) -> list[dict[str, Any]]:
        raw = self._chat.chat(
            [
                {"role": "system", "content": _EXTRACT_SYSTEM},
                {"role": "user", "content": f"对话：\n{episode}\n\n请提取："},
            ],
            temperature=0.1,
            json_mode=True,
        )
        data = json.loads(raw)
        facts = data.get("facts", []) if isinstance(data, dict) else data
        return [c for c in facts if isinstance(c, dict)]

    # ── ② 验证 + ③ 巩固 ──────────────────────────────────────────────────────

    def _process_candidate(
        self, owner_id: str, task_id: str, cand: dict[str, Any]
    ) -> None:
        text = str(cand.get("text", "")).strip()
        if not text:
            return
        # 接地关：LLM 标记无证据 → 判为幻觉记忆，丢弃。
        if cand.get("grounded") is False:
            return
        # 显著性关：低于阈值不固化（防污染）。
        salience = self._clamp01(cand.get("salience", 0.5))
        if salience < self._salience_threshold:
            return

        embedding = self._embedder.embed([text])[0]
        tags = [str(t) for t in cand.get("tags", []) if str(t).strip()]

        neighbors = [
            (f, sim)
            for f, sim in self._facts.query(owner_id, embedding, 1)
            if f.superseded_by is None
        ]
        if neighbors:
            top, sim = neighbors[0]
            if sim >= self._dedup_threshold:
                self._reinforce(top, embedding, salience)  # 去重关：强化不新增
                return
            if sim >= self._conflict_threshold:
                self._supersede(owner_id, task_id, top, text, salience, tags, embedding)
                return

        # 全新事实：首次提取低置信（tentative）。
        self._facts.add(
            self._new_fact(owner_id, task_id, text, salience, tags, confidence=0.5),
            embedding,
        )

    def _reinforce(self, existing: Fact, embedding: list[float], salience: float) -> None:
        """渐进强化：重复印证 → 置信度上调，刷新 last_used。"""
        updated = existing.model_copy(
            update={
                "confidence": self._clamp01(existing.confidence + self._reinforce_step),
                "salience": max(existing.salience, salience),
                "last_used_at": time.time(),
            }
        )
        self._facts.add(updated, embedding)

    def _supersede(
        self,
        owner_id: str,
        task_id: str,
        old: Fact,
        text: str,
        salience: float,
        tags: list[str],
        embedding: list[float],
    ) -> None:
        """冲突遗忘：近义但不重复 → 旧事实标 superseded，写入修正后的新事实。"""
        new_fact = self._new_fact(owner_id, task_id, text, salience, tags, confidence=0.5)
        self._facts.add(new_fact, embedding)
        self._facts.mark_superseded(owner_id, old.fact_id, new_fact.fact_id)

    # ── 容量遗忘 ──────────────────────────────────────────────────────────────

    def _enforce_capacity(self, owner_id: str) -> None:
        active = [f for f in self._facts.list_owner(owner_id) if f.superseded_by is None]
        if len(active) <= self._cap:
            return
        now = time.time()
        ranked = sorted(active, key=lambda f: self._decay_score(f, now))
        for f in ranked[: len(active) - self._cap]:
            self._facts.delete(owner_id, f.fact_id)

    def _decay_score(self, fact: Fact, now: float) -> float:
        age_days = max(0.0, (now - fact.created_at) / _SECONDS_PER_DAY)
        return fact.salience * math.exp(-self._lambda * age_days)

    # ── 内部 ─────────────────────────────────────────────────────────────────

    def _advance(self, task_id: str, owner_id: str, watermark: int) -> None:
        self._state.upsert(
            ConsolidationState(
                task_id=task_id,
                owner_id=owner_id,
                msg_watermark=watermark,
                updated_at=time.time(),
            )
        )

    @staticmethod
    def _new_fact(
        owner_id: str,
        task_id: str,
        text: str,
        salience: float,
        tags: list[str],
        *,
        confidence: float,
    ) -> Fact:
        now = time.time()
        return Fact(
            fact_id=f"fact_{uuid.uuid4().hex[:16]}",
            owner_id=owner_id,
            text=text,
            tags=tags,
            confidence=confidence,
            salience=salience,
            created_at=now,
            last_used_at=now,
            source_episode=task_id,
        )

    @staticmethod
    def _clamp01(value: Any) -> float:
        try:
            v = float(value)
        except (ValueError, TypeError):
            return 0.0
        return max(0.0, min(1.0, v))

"""Conversation support: owner-scoped data and bounded context."""

from __future__ import annotations

import logging
import time

from domain.models import Message, TaskSummary
from domain.ports import (
    ChatPort,
    SummaryStorePort,
    TaskRepoPort,
)

logger = logging.getLogger(__name__)

_SECONDS_PER_DAY = 86400.0

_SUMMARY_SYSTEM = (
    "你是对话摘要助手。请把已有摘要和新增对话融合成一份更新后的简洁摘要，"
    "保留关键事实、用户诉求、已确认结论与待办；剔除寒暄与重复。"
    "只输出摘要正文，不要解释，不要加标题。"
)


class TaskBackedMemory:
    """``MemoryPort`` 的 L1+L2 实现。"""

    def __init__(
        self,
        task_repo: TaskRepoPort,
        *,
        summary_store: SummaryStorePort | None = None,
        chat: ChatPort | None = None,
        l1_ttl_days: float = 30.0,
        l2_ttl_days: float = 180.0,
        summary_threshold: int = 20,
        summary_max_tokens: int = 800,
    ) -> None:
        self._repo = task_repo
        self._summary_store = summary_store
        self._chat = chat
        self._l1_ttl_days = l1_ttl_days
        self._l2_ttl_days = l2_ttl_days
        self._summary_threshold = summary_threshold
        self._summary_max_tokens = summary_max_tokens

    # ── L1 短期 ────────────────────────────────────────────────────────────

    def recent_messages(self, owner_id: str, task_id: str, n: int) -> list[Message]:
        """返回该 task 最近 n 条消息；非本人 / 越界 / 过期一律过滤。"""
        if n <= 0:
            return []
        # 归属校验：拿不到（不存在或非本人）即视为无历史，安全降级。
        if self._repo.get(task_id, owner_id) is None:
            return []
        msgs = self._repo.list_messages(task_id)
        msgs = self._filter_ttl(msgs, self._l1_ttl_days)
        if not msgs:
            return []
        return msgs[-n:]

    # ── L2 摘要 ────────────────────────────────────────────────────────────

    def get_summary(self, owner_id: str, task_id: str) -> str | None:
        """返回当前 task 摘要；未配置 / 非本人 / 过期 / 空一律返回 None。"""
        if self._summary_store is None:
            return None
        if self._repo.get(task_id, owner_id) is None:
            return None
        record = self._summary_store.get(task_id, owner_id)
        if record is None or not record.summary:
            return None
        if self._is_expired(record.updated_at, self._l2_ttl_days):
            return None  # 逻辑遗忘：过期摘要永不注入
        return record.summary

    def maybe_summarize(self, owner_id: str, task_id: str, threshold: int | None = None) -> None:
        """未摘要消息数 ≥ 阈值时，LLM 增量精炼出新摘要并推进 watermark。

        幂等：watermark 记录“已摘要到第几条”，重试 / 漏摘都按差额自愈。
        未配置 store/chat、非本人、backlog 不足均为安全空操作。
        """
        if self._summary_store is None or self._chat is None:
            return
        if self._repo.get(task_id, owner_id) is None:
            return

        thr = threshold if threshold is not None else self._summary_threshold
        msgs = self._repo.list_messages(task_id)
        record = self._summary_store.get(task_id, owner_id)
        watermark = record.msg_watermark if record else 0
        # watermark 越界（消息被删/重置）时夹紧，避免负切片。
        watermark = max(0, min(watermark, len(msgs)))
        backlog = msgs[watermark:]
        if len(backlog) < thr:
            return  # backlog 不足，等下一轮

        old_summary = record.summary if record else ""
        try:
            new_summary = self._refine_summary(old_summary, backlog)
        except Exception:  # noqa: BLE001 — 后台 best-effort，失败下一轮再试
            logger.warning("L2 摘要生成失败，保留旧 watermark 待下轮重试", exc_info=True)
            return
        if not new_summary:
            return

        self._summary_store.upsert(
            TaskSummary(
                task_id=task_id,
                owner_id=owner_id,
                summary=new_summary,
                msg_watermark=watermark + len(backlog),
                updated_at=time.time(),
            )
        )

    # ── 内部 ────────────────────────────────────────────────────────────────

    def _refine_summary(self, old_summary: str, backlog: list[Message]) -> str:
        convo = "\n".join(f"{m.role}: {(m.content or '').strip()}" for m in backlog if m.content)
        prior = old_summary.strip() or "（无）"
        user_prompt = f"【已有摘要】\n{prior}\n\n【新增对话】\n{convo}\n\n请输出更新后的摘要："
        assert self._chat is not None  # 上游已校验
        return self._chat.chat(
            [
                {"role": "system", "content": _SUMMARY_SYSTEM},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.1,
            max_tokens=self._summary_max_tokens,
            operation="memory_summary",
        ).strip()

    def _filter_ttl(self, msgs: list[Message], ttl_days: float) -> list[Message]:
        if ttl_days <= 0:
            return msgs  # 0 表示不启用 TTL 过滤
        cutoff = time.time() - ttl_days * _SECONDS_PER_DAY
        return [m for m in msgs if m.created_at >= cutoff]

    @staticmethod
    def _is_expired(updated_at: float, ttl_days: float) -> bool:
        if ttl_days <= 0:
            return False
        return updated_at < time.time() - ttl_days * _SECONDS_PER_DAY

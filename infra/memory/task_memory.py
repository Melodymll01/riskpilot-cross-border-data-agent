"""L1 短期 + L2 摘要记忆：以 ``TaskRepoPort`` 为后端的记忆适配器（S-030a/b）。

设计要点：
- L1 数据不另存一份，直接复用 task 消息表，避免双写与一致性问题。
- 读取前强制 ``owner_id`` 归属校验：非本人 task 一律返回空，
  既不抛异常（图省事降级）也不泄露跨用户数据。
- L2 滚动摘要走独立 ``SummaryStorePort``（``task_summaries`` 表），
  LLM **增量精炼**（旧摘要 + 新增消息 → 新摘要，成本 O(1)/轮），
  靠 ``msg_watermark`` 幂等：重试不重复摘要、漏摘下一轮自动补。
- TTL 逻辑遗忘：读取时过滤过期记忆（L1 原文 / L2 摘要），过期永不注入。
- L3/L4 仍保留接口但抛 ``NotImplementedError``，由 S-030c/d 补齐。
"""

from __future__ import annotations

import logging
import time

from domain.models import Fact, Message, SessionProfile, TaskSummary
from domain.ports import ChatPort, SummaryStorePort, TaskRepoPort

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
    ) -> None:
        self._repo = task_repo
        self._summary_store = summary_store
        self._chat = chat
        self._l1_ttl_days = l1_ttl_days
        self._l2_ttl_days = l2_ttl_days
        self._summary_threshold = summary_threshold

    # ── L1 短期 ────────────────────────────────────────────────────────────

    def append_message(self, task_id: str, msg: Message) -> None:
        """写入一条消息（薄委托给 task 仓储）。

        注意：当前 agent 主循环仍直接经 ``task_repo`` 落库，
        本方法保留以保证 ``MemoryPort`` L1 语义完整。
        """
        self._repo.append_message(msg)

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

    def maybe_summarize(
        self, owner_id: str, task_id: str, threshold: int | None = None
    ) -> None:
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

    # ── L3/L4：占位，后续步骤实现 ─────────────────────────────────────────

    def get_profile(self, owner_id: str) -> SessionProfile:  # pragma: no cover
        raise NotImplementedError("L3 用户画像将在 S-030d 实现")

    def update_profile(self, owner_id: str, facts: dict[str, str]) -> None:  # pragma: no cover
        raise NotImplementedError("L3 用户画像将在 S-030d 实现")

    def recall_semantic(self, owner_id: str, query: str, k: int) -> list[Fact]:  # pragma: no cover
        raise NotImplementedError("L4 语义事实将在 S-030c 实现")

    # ── 内部 ────────────────────────────────────────────────────────────────

    def _refine_summary(self, old_summary: str, backlog: list[Message]) -> str:
        convo = "\n".join(
            f"{m.role}: {(m.content or '').strip()}" for m in backlog if m.content
        )
        prior = old_summary.strip() or "（无）"
        user_prompt = (
            f"【已有摘要】\n{prior}\n\n"
            f"【新增对话】\n{convo}\n\n"
            "请输出更新后的摘要："
        )
        assert self._chat is not None  # 上游已校验
        return self._chat.chat(
            [
                {"role": "system", "content": _SUMMARY_SYSTEM},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.1,
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

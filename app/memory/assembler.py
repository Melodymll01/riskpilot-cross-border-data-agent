"""Conversation support: owner-scoped data and bounded context."""

from __future__ import annotations

import logging

from domain.models import Message
from domain.ports import MemoryPort

logger = logging.getLogger(__name__)

_HEADER = "【历史对话（仅供参考，避免重复已回答内容）】"
_SUMMARY_HEADER = "【对话摘要（更早内容已压缩）】"

_ROLE_LABEL = {
    "user": "用户",
    "assistant": "助手",
    "tool": "工具",
    "system": "系统",
}


class MemoryAssembler:
    """预算感知的记忆装配器骨架。"""

    def __init__(
        self,
        memory: MemoryPort | None,
        *,
        recent_n: int,
        token_budget: int,
    ) -> None:
        self._memory = memory
        self._recent_n = recent_n
        self._token_budget = token_budget

    @property
    def token_budget(self) -> int:
        return self._token_budget

    def assemble(
        self,
        *,
        owner_id: str,
        task_id: str,
        query: str | None = None,
        exclude_message_id: str | None = None,
        token_budget_override: int | None = None,
    ) -> str:
        """组装注入文本；无记忆 / 无内容 / 出错均返回空串。


        注入语义（对齐 ChatGPT，Step 032）：
        - **当前任务上下文（L1 最近原文 + L2 本任务摘要）永远自动填充**，不受开关控制，
          对应 ChatGPT「当前对话上下文窗口永远在」，保证多轮连贯；
        """
        if self._memory is None:
            return ""
        summary = self._safe_summary(owner_id=owner_id, task_id=task_id)
        msgs = [
            message
            for message in self._safe_recent(owner_id=owner_id, task_id=task_id)
            if message.msg_id != exclude_message_id
        ]
        block = self._render(summary, msgs, budget=token_budget_override)
        if token_budget_override is not None:
            return block[: max(0, token_budget_override)]
        return block

    # ── 读取（全程降级） ───────────────────────────────────

    def _safe_summary(self, *, owner_id: str, task_id: str) -> str:
        try:
            return self._memory.get_summary(owner_id, task_id) or ""  # type: ignore[union-attr]
        except Exception:  # noqa: BLE001 — 记忆故障必须降级，不得中断主流程
            logger.warning("L2 摘要读取失败，降级为无摘要", exc_info=True)
            return ""

    def _safe_recent(self, *, owner_id: str, task_id: str) -> list[Message]:
        if self._recent_n <= 0:
            return []
        try:
            return self._memory.recent_messages(owner_id, task_id, self._recent_n)  # type: ignore[union-attr]
        except Exception:  # noqa: BLE001 — 记忆故障必须降级，不得中断主流程
            logger.warning("L1 历史读取失败，降级为无历史", exc_info=True)
            return []

    # ── 排版 + 预算 ────────────────────────────────────────────────────────

    def _render(
        self,
        summary: str,
        msgs: list[Message],
        budget: int | None = None,
    ) -> str:
        budget = self._token_budget if budget is None else max(0, budget)
        sections: list[str] = []
        used = 0

        # L2 摘要次优先占预算（长期压缩信息，价值密度高）；超额则截断摘要正文。
        if summary:
            summary = summary.strip()
            head_cost = self._estimate_tokens(_SUMMARY_HEADER)
            avail = max(0, budget - used - head_cost)
            if self._estimate_tokens(summary) > avail:
                summary = summary[:avail]
            if summary:
                block = f"{_SUMMARY_HEADER}\n{summary}"
                sections.append(block)
                used += self._estimate_tokens(block)

        # L1 最近原文用剩余预算，从最新往最旧填（始终保留≥1条）。
        if msgs:
            lines = [self._format_line(m) for m in msgs]
            used += self._estimate_tokens(_HEADER)
            kept_rev: list[str] = []
            for line in reversed(lines):
                cost = self._estimate_tokens(line)
                if used + cost > budget and kept_rev:
                    break
                used += cost
                kept_rev.append(line)
            if kept_rev:
                kept = list(reversed(kept_rev))
                sections.append("\n".join([_HEADER, *kept]))

        return "\n\n".join(sections)

    @staticmethod
    def _format_line(msg: Message) -> str:
        label = _ROLE_LABEL.get(msg.role, msg.role)
        content = (msg.content or "").strip().replace("\n", " ")
        return f"{label}：{content}"

    @staticmethod
    def _estimate_tokens(text: str) -> int:
        """保守的 token 估算：字符数即近似 token 数。"""
        return len(text)

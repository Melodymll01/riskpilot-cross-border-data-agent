"""记忆装配器：把分层记忆汇成一段注入 prompt 的文本（S-030a/b）。

职责边界：
- 只负责"读 + 排版 + 预算裁剪"，不负责写入 / 固化 / 遗忘。
- ``memory=None`` 时返回空串，调用方据此保持无状态旧行为（降级）。
- token 预算用字符数保守近似（中文 1 字 ≈ 1 token 偏高估，宁可少注入）。
- 汇聚顺序：L2 摘要（长期压缩、优先保）→ L1 最近原文（预算剩余从最新往旧填）。
- 读取走 ``MemoryPort``，已在适配器内做 owner 归属校验与 TTL 过滤；
  任意异常都吞掉降级，绝不因记忆故障拖垮主对话。
"""

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

    def assemble(self, *, owner_id: str, task_id: str) -> str:
        """组装注入文本；无记忆 / 无内容 / 出错均返回空串。"""
        if self._memory is None:
            return ""
        summary = self._safe_summary(owner_id=owner_id, task_id=task_id)
        msgs = self._safe_recent(owner_id=owner_id, task_id=task_id)
        if not summary and not msgs:
            return ""
        return self._render(summary, msgs)

    # ── 读取（全程降级） ──────────────────────────────────────────────────

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

    def _render(self, summary: str, msgs: list[Message]) -> str:
        budget = self._token_budget
        sections: list[str] = []
        used = 0

        # L2 摘要优先占预算（长期压缩信息，价值密度高）；超额则截断摘要正文。
        if summary:
            summary = summary.strip()
            head_cost = self._estimate_tokens(_SUMMARY_HEADER)
            avail = max(0, budget - head_cost)
            if self._estimate_tokens(summary) > avail:
                summary = summary[:avail]
            if summary:
                block = f"{_SUMMARY_HEADER}\n{summary}"
                sections.append(block)
                used += self._estimate_tokens(block)

        # L1 最近原文用剩余预算，从最新往最旧填。
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

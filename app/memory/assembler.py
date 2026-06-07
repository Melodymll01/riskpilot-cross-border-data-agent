"""记忆装配器：把分层记忆汇成一段注入 prompt 的文本（S-030a 仅 L1）。

职责边界：
- 只负责"读 + 排版 + 预算裁剪"，不负责写入 / 固化 / 遗忘。
- ``memory=None`` 时返回空串，调用方据此保持无状态旧行为（降级）。
- token 预算用字符数保守近似（中文 1 字 ≈ 1 token 偏高估，宁可少注入）。
- 读取走 ``MemoryPort.recent_messages``，已在适配器内做 owner 归属校验；
  任意异常都吞掉返回空串，绝不因记忆故障拖垮主对话。
"""

from __future__ import annotations

import logging

from domain.models import Message
from domain.ports import MemoryPort

logger = logging.getLogger(__name__)

_HEADER = "【历史对话（仅供参考，避免重复已回答内容）】"

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
        """组装注入文本；无记忆 / 无历史 / 出错均返回空串。"""
        if self._memory is None or self._recent_n <= 0:
            return ""
        try:
            msgs = self._memory.recent_messages(owner_id, task_id, self._recent_n)
        except Exception:  # noqa: BLE001 — 记忆故障必须降级，不得中断主流程
            logger.warning("记忆读取失败，降级为无历史注入", exc_info=True)
            return ""
        if not msgs:
            return ""
        return self._render(msgs)

    # ── 内部 ────────────────────────────────────────────────────────────────

    def _render(self, msgs: list[Message]) -> str:
        """逐条排版并按 token 预算从最旧开始裁剪。"""
        lines = [self._format_line(m) for m in msgs]
        # 预算裁剪：header 也计入；超出则丢弃最旧（列表头部）。
        budget = self._token_budget
        used = self._estimate_tokens(_HEADER)
        kept_rev: list[str] = []
        for line in reversed(lines):  # 从最新往最旧累加，保住近期上下文
            cost = self._estimate_tokens(line)
            if used + cost > budget and kept_rev:
                break
            used += cost
            kept_rev.append(line)
        if not kept_rev:
            return ""
        kept = list(reversed(kept_rev))
        return "\n".join([_HEADER, *kept])

    @staticmethod
    def _format_line(msg: Message) -> str:
        label = _ROLE_LABEL.get(msg.role, msg.role)
        content = (msg.content or "").strip().replace("\n", " ")
        return f"{label}：{content}"

    @staticmethod
    def _estimate_tokens(text: str) -> int:
        """保守的 token 估算：字符数即近似 token 数。"""
        return len(text)

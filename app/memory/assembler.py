"""记忆装配器：把分层记忆汇成一段注入 prompt 的文本（S-030a/b）。

职责边界：
- 只负责"读 + 排版 + 预算裁剪"，不负责写入 / 固化 / 遗忘。
- ``memory=None`` 时返回空串，调用方据此保持无状态旧行为（降级）。
- token 预算用字符数保守近似（中文 1 字 ≈ 1 token 偏高估，宁可少注入）。
- 汇聚顺序：L4 长期事实（跨会话稳定知识，价值密度最高）→ L2 摘要（长期压缩）
  → L3 用户画像（稳定偏好，优先级最低）→ L1 最近原文（预算剩余从最新往旧填，始终保留≥1条）。
- 读取走 ``MemoryPort``，已在适配器内做 owner 归属校验与 TTL 过滤；
  任意异常都吞掉降级，绝不因记忆故障拖垮主对话。
"""

from __future__ import annotations

import logging

from domain.models import Fact, Message, SessionProfile
from domain.ports import MemoryPort, MemorySettingsStorePort

logger = logging.getLogger(__name__)

_HEADER = "【历史对话（仅供参考，避免重复已回答内容）】"
_SUMMARY_HEADER = "【对话摘要（更早内容已压缩）】"
_FACTS_HEADER = "【相关长期记忆（你已知的用户事实，仅供参考）】"
_PROFILE_HEADER = "【用户画像（稳定偏好，跨会话）】"

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
        recall_k: int = 0,
        profile_max_facts: int = 0,
        settings_store: MemorySettingsStorePort | None = None,
    ) -> None:
        self._memory = memory
        self._recent_n = recent_n
        self._token_budget = token_budget
        self._recall_k = recall_k
        self._profile_max_facts = profile_max_facts
        self._settings_store = settings_store

    def assemble(self, *, owner_id: str, task_id: str, query: str | None = None) -> str:
        """组装注入文本；无记忆 / 无内容 / 出错均返回空串。

        ``query`` 为本轮用户问题，用于 L4 语义召回（缺省不召回）。

        注入语义（对齐 ChatGPT，Step 032）：
        - **当前任务上下文（L1 最近原文 + L2 本任务摘要）永远自动填充**，不受开关控制，
          对应 ChatGPT「当前对话上下文窗口永远在」，保证多轮连贯；
        - ``use_saved_memory`` 关 → 不注入 L4 长期事实 + L3 用户画像（跨会话的"保存的记忆"）。
        """
        if self._memory is None:
            return ""
        use_saved_memory = self._safe_settings(owner_id=owner_id)
        facts = (
            self._safe_facts(owner_id=owner_id, query=query) if use_saved_memory else []
        )
        # 当前任务上下文：永远注入（不受开关控制）
        summary = self._safe_summary(owner_id=owner_id, task_id=task_id)
        profile = self._safe_profile(owner_id=owner_id) if use_saved_memory else None
        msgs = self._safe_recent(owner_id=owner_id, task_id=task_id)
        if not facts and not summary and not profile and not msgs:
            return ""
        return self._render(facts, summary, profile, msgs)

    # ── 读取（全程降级） ───────────────────────────────────

    def _safe_settings(self, *, owner_id: str) -> bool:
        """读取 ``use_saved_memory``；无 store / 未设置 / 出错均返回默认 True。

        默认 True（fail-open，与全局 ``memory_enabled`` 默认开一致）。
        当前任务上下文（L1/L2）不受该开关控制。
        """
        if self._settings_store is None:
            return True
        try:
            settings = self._settings_store.get(owner_id)
        except Exception:  # noqa: BLE001 — 设置读取故障降级为默认，不中断主流程
            logger.warning("记忆开关读取失败，降级为默认", exc_info=True)
            return True
        if settings is None:
            return True
        return settings.use_saved_memory

    def _safe_facts(self, *, owner_id: str, query: str | None) -> list[Fact]:
        if self._recall_k <= 0 or not (query or "").strip():
            return []
        try:
            return self._memory.recall_semantic(owner_id, query, self._recall_k)  # type: ignore[union-attr]
        except Exception:  # noqa: BLE001 — 记忆故障必须降级，不得中断主流程
            logger.warning("L4 语义召回失败，降级为无事实", exc_info=True)
            return []

    def _safe_summary(self, *, owner_id: str, task_id: str) -> str:
        try:
            return self._memory.get_summary(owner_id, task_id) or ""  # type: ignore[union-attr]
        except Exception:  # noqa: BLE001 — 记忆故障必须降级，不得中断主流程
            logger.warning("L2 摘要读取失败，降级为无摘要", exc_info=True)
            return ""

    def _safe_profile(self, *, owner_id: str) -> SessionProfile | None:
        if self._profile_max_facts <= 0:
            return None
        try:
            profile = self._memory.get_profile(owner_id)  # type: ignore[union-attr]
        except Exception:  # noqa: BLE001 — 记忆故障必须降级，不得中断主流程
            logger.warning("L3 画像读取失败，降级为无画像", exc_info=True)
            return None
        if profile is None or not profile.facts:
            return None
        return profile

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
        facts: list[Fact],
        summary: str,
        profile: SessionProfile | None,
        msgs: list[Message],
    ) -> str:
        budget = self._token_budget
        sections: list[str] = []
        used = 0

        # L4 长期事实最优先（跨会话稳定知识）；逐条填入直到超预算。
        if facts:
            head_cost = self._estimate_tokens(_FACTS_HEADER)
            kept_facts: list[str] = []
            local_used = used + head_cost
            for fact in facts:
                line = self._format_fact(fact)
                cost = self._estimate_tokens(line)
                if local_used + cost > budget and kept_facts:
                    break
                local_used += cost
                kept_facts.append(line)
            if kept_facts:
                sections.append("\n".join([_FACTS_HEADER, *kept_facts]))
                used = local_used

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

        # L3 画像偏好（优先级最低，预算耗尽时整块丢弃）；不挤占 L1 的≥1条保底。
        if profile is not None:
            lines = self._format_profile(profile)
            if lines:
                head_cost = self._estimate_tokens(_PROFILE_HEADER)
                kept_pref: list[str] = []
                local_used = used + head_cost
                for line in lines:
                    cost = self._estimate_tokens(line)
                    if local_used + cost > budget:
                        break
                    local_used += cost
                    kept_pref.append(line)
                if kept_pref:
                    sections.append("\n".join([_PROFILE_HEADER, *kept_pref]))
                    used = local_used

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
    def _format_fact(fact: Fact) -> str:
        text = (fact.text or "").strip().replace("\n", " ")
        return f"- {text}"

    def _format_profile(self, profile: SessionProfile) -> list[str]:
        """画像偏好字典渲染为 ``- key：value`` 行，按 max_facts 截断。"""
        lines: list[str] = []
        for key, value in profile.facts.items():
            k = str(key).strip().replace("\n", " ")
            v = str(value).strip().replace("\n", " ")
            if not k or not v:
                continue
            lines.append(f"- {k}：{v}")
            if len(lines) >= self._profile_max_facts:
                break
        return lines

    @staticmethod
    def _format_line(msg: Message) -> str:
        label = _ROLE_LABEL.get(msg.role, msg.role)
        content = (msg.content or "").strip().replace("\n", " ")
        return f"{label}：{content}"

    @staticmethod
    def _estimate_tokens(text: str) -> int:
        """保守的 token 估算：字符数即近似 token 数。"""
        return len(text)

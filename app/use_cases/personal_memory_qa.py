"""Answer bounded self-profile questions from validated user-provided source values."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING

from domain.account_memory import AccountMemory
from domain.memory_policy import PROFILE_LABELS, profile_question_fields
from domain.models import Citation

if TYPE_CHECKING:
    from app.use_cases.account_memory import AccountMemoryUseCase


@dataclass(frozen=True)
class PersonalMemoryAnswer:
    text: str
    citations: list[Citation]
    reason: str
    context_memories: list[AccountMemory] = field(default_factory=list)


class PersonalMemoryQAUseCase:
    def __init__(self, memory: AccountMemoryUseCase) -> None:
        self._memory = memory

    def answer(
        self, owner_id: str, question: str, run_id: str, *, task_id: str | None = None
    ) -> PersonalMemoryAnswer | None:
        fields = profile_question_fields(question)
        if fields is None:
            return None
        current = self._memory.current_profile_context(owner_id, task_id, fields) if task_id else []
        long_term = []
        try:
            self._memory.require_account(owner_id)
        except PermissionError:
            if not current:
                return PersonalMemoryAnswer(
                    "请先登录账号，再保存或查看你的个人档案。", [], "profile_account_required"
                )
        else:
            long_term = self._memory.snapshot_profile(
                owner_id, fields, run_id, current_context=current
            )
            if not self._memory.enabled(owner_id) and not current:
                return PersonalMemoryAnswer(
                    "当前账号记忆已关闭。请在“我的记忆”中开启后，再根据已保存的个人档案回答。",
                    [],
                    "profile_memory_disabled",
                )
        selected = {item.profile_field: item for item in current}
        selected.update({item.profile_field: item for item in long_term})
        memories = list(selected.values())
        if not memories:
            names = "、".join(PROFILE_LABELS[item] for item in fields)
            return PersonalMemoryAnswer(
                f"你还没有保存可用的{names}信息。可以添加用户档案，或发送“请记住：我的名字是……”等明确声明。",
                [],
                "profile_missing",
            )
        lines = ["根据你提供的个人资料："]
        citations = []
        needs_review = False
        for memory in memories:
            if memory.profile_field is None or memory.value is None:
                raise ValueError("个人资料缺少可验证的字段值")
            label = PROFILE_LABELS[memory.profile_field]
            stale = memory.review_at is not None and memory.review_at <= self._memory.now()
            if stale:
                date = datetime.fromtimestamp(memory.updated_at).strftime("%Y-%m-%d")
                lines.append(
                    f"- {label}：你在 {date} 保存的是“{memory.value}”，已到复核时间，请确认现在是否仍然有效。"
                )
                needs_review = True
            else:
                lines.append(f"- {label}：{memory.value}。")
            contextual = memory.memory_id.startswith("context_")
            citations.append(
                Citation(
                    citation_id=f"MEM-{memory.memory_id}@{memory.revision}",
                    source_id=memory.memory_id,
                    source_type="user_context" if contextual else "memory",
                    source_name=f"{'当前会话' if contextual else '我的记忆'} · {label}",
                    source_version=str(memory.revision),
                    text_snippet=memory.source_quote,
                )
            )
        contexts = [item for item in memories if item.memory_id.startswith("context_")]
        return PersonalMemoryAnswer(
            "\n".join(lines),
            citations,
            "profile_needs_review" if needs_review else "profile_answer",
            contexts,
        )

    def validate_context(self, answer: PersonalMemoryAnswer) -> None:
        self._memory.validate_current_profile(answer.context_memories)

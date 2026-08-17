"""RiskPilot V2 合规案件领域模型与状态机。"""

from __future__ import annotations

import time
from datetime import date
from typing import ClassVar, Literal

from pydantic import Field, model_validator

from domain.errors import InvalidCaseTransition
from domain.models import BaseDomainModel

CaseStatus = Literal[
    "draft",
    "collecting",
    "processing_documents",
    "facts_pending_confirmation",
    "ready_for_assessment",
    "assessing",
    "review_required",
    "completed",
    "archived",
]


class Case(BaseDomainModel):
    """一次数据出境合规事项。

    Case 与 v2 ``Task`` 分离：Task 是聊天会话，Case 是具有独立生命周期、材料、
    事实、评估和复核人的业务聚合根。
    """

    _ALLOWED_TRANSITIONS: ClassVar[dict[str, frozenset[str]]] = {
        "draft": frozenset({"collecting", "archived"}),
        "collecting": frozenset(
            {
                "processing_documents",
                "facts_pending_confirmation",
                "ready_for_assessment",
                "archived",
            }
        ),
        "processing_documents": frozenset(
            {
                "collecting",
                "facts_pending_confirmation",
                "ready_for_assessment",
                "archived",
            }
        ),
        "facts_pending_confirmation": frozenset(
            {
                "collecting",
                "processing_documents",
                "ready_for_assessment",
                "archived",
            }
        ),
        "ready_for_assessment": frozenset(
            {
                "collecting",
                "processing_documents",
                "facts_pending_confirmation",
                "assessing",
                "archived",
            }
        ),
        "assessing": frozenset({"ready_for_assessment", "review_required", "archived"}),
        "review_required": frozenset(
            {"assessing", "ready_for_assessment", "completed", "archived"}
        ),
        "completed": frozenset({"ready_for_assessment", "archived"}),
        "archived": frozenset(),
    }

    case_id: str = Field(min_length=1)
    workspace_id: str = Field(min_length=1)
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=4000)
    jurisdiction: str = Field(default="CN", min_length=1, max_length=32)
    scenario_type: str = Field(default="", max_length=100)
    assessment_date: date | None = None
    status: CaseStatus = "draft"
    owner_id: str = Field(min_length=1)
    reviewer_id: str | None = None
    active_assessment_id: str | None = None
    created_at: float
    updated_at: float

    @model_validator(mode="after")
    def validate_case(self) -> Case:
        if self.updated_at < self.created_at:
            raise ValueError("updated_at 不能早于 created_at")
        if not self.title.strip():
            raise ValueError("title 不能为空白字符串")
        if self.reviewer_id is not None and not self.reviewer_id.strip():
            raise ValueError("reviewer_id 不能为空白字符串")
        if self.active_assessment_id is not None and not self.active_assessment_id.strip():
            raise ValueError("active_assessment_id 不能为空白字符串")
        return self

    def can_transition_to(self, target: CaseStatus) -> bool:
        """判断是否允许切换；相同状态视为幂等操作。"""
        return target == self.status or target in self._ALLOWED_TRANSITIONS[self.status]

    def transition_to(self, target: CaseStatus, *, at: float | None = None) -> Case:
        """返回状态切换后的新快照，不修改当前对象。"""
        if target == self.status:
            return self
        if not self.can_transition_to(target):
            raise InvalidCaseTransition(self.case_id, self.status, target)
        transition_time = time.time() if at is None else at
        if transition_time < self.updated_at:
            raise ValueError("状态变更时间不能早于案件更新时间")
        return self.model_copy(
            update={
                "status": target,
                "updated_at": transition_time,
            }
        )

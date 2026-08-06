"""V2 案件事实与证据引用领域模型。"""

from __future__ import annotations

import time
from typing import Any, ClassVar, Literal, cast

from pydantic import Field, model_validator

from domain.errors import InvalidCaseFactTransition
from domain.models import BaseDomainModel

CaseFactStatus = Literal["proposed", "confirmed", "rejected", "conflicting", "unknown"]
CaseFactSource = Literal["user", "document", "system", "import"]
FactCriticality = Literal["normal", "critical"]


class CaseFactEvidence(BaseDomainModel):
    """支撑案件事实的原文证据引用。"""

    evidence_id: str = Field(min_length=1)
    case_id: str = Field(min_length=1)
    fact_id: str = Field(min_length=1)
    fact_version: int = Field(ge=1)
    document_id: str = Field(min_length=1)
    document_version_id: str = Field(min_length=1)
    page_number: int = Field(ge=1)
    quote: str = Field(min_length=1, max_length=4000)
    start_offset: int | None = Field(default=None, ge=0)
    end_offset: int | None = Field(default=None, ge=0)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    created_at: float

    @model_validator(mode="after")
    def validate_evidence(self) -> CaseFactEvidence:
        if not self.quote.strip():
            raise ValueError("quote 不能为空白字符串")
        if (self.start_offset is None) != (self.end_offset is None):
            raise ValueError("start_offset 和 end_offset 必须同时为空或同时存在")
        if (
            self.start_offset is not None
            and self.end_offset is not None
            and self.end_offset <= self.start_offset
        ):
            raise ValueError("end_offset 必须大于 start_offset")
        return self


class CaseFact(BaseDomainModel):
    """案件内可版本化、可确认的结构化事实。"""

    _ALLOWED_TRANSITIONS: ClassVar[dict[str, frozenset[str]]] = {
        "proposed": frozenset({"confirmed", "rejected", "conflicting", "unknown"}),
        "confirmed": frozenset({"proposed", "conflicting", "rejected"}),
        "rejected": frozenset({"proposed"}),
        "conflicting": frozenset({"proposed", "confirmed", "rejected", "unknown"}),
        "unknown": frozenset({"proposed", "confirmed", "rejected"}),
    }

    fact_id: str = Field(min_length=1)
    case_id: str = Field(min_length=1)
    field_name: str = Field(min_length=1, max_length=200)
    value: bool | int | float | str | list[Any] | dict[str, Any] | None = None
    status: CaseFactStatus = "proposed"
    source_type: CaseFactSource
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    criticality: FactCriticality = "normal"
    version: int = Field(default=1, ge=1)
    created_by: str = Field(min_length=1)
    confirmed_by: str | None = None
    confirmed_at: float | None = None
    created_at: float
    updated_at: float

    @model_validator(mode="after")
    def validate_fact(self) -> CaseFact:
        if not self.field_name.strip():
            raise ValueError("field_name 不能为空白字符串")
        if self.updated_at < self.created_at:
            raise ValueError("updated_at 不能早于 created_at")
        if (self.confirmed_by is None) != (self.confirmed_at is None):
            raise ValueError("confirmed_by 和 confirmed_at 必须同时为空或同时存在")
        if self.status == "confirmed" and self.confirmed_by is None:
            raise ValueError("confirmed 事实必须记录确认人和确认时间")
        if self.status != "confirmed" and self.confirmed_by is not None:
            raise ValueError("非 confirmed 事实不能保留确认信息")
        return self

    @property
    def usable_for_rules(self) -> bool:
        """只有已确认事实可以进入确定性规则计算。"""
        return self.status == "confirmed"

    def propose_revision(
        self,
        *,
        value: bool | int | float | str | list[Any] | dict[str, Any] | None,
        source_type: CaseFactSource,
        confidence: float,
        actor_id: str,
        at: float | None = None,
    ) -> CaseFact:
        revision_time = time.time() if at is None else at
        if revision_time < self.updated_at:
            raise ValueError("事实修订时间不能早于更新时间")
        return cast(
            "CaseFact",
            self.model_copy(
                update={
                    "value": value,
                    "status": "proposed",
                    "source_type": source_type,
                    "confidence": confidence,
                    "version": self.version + 1,
                    "created_by": actor_id,
                    "confirmed_by": None,
                    "confirmed_at": None,
                    "updated_at": revision_time,
                }
            ),
        )

    def transition_to(
        self,
        target: CaseFactStatus,
        *,
        actor_id: str,
        at: float | None = None,
    ) -> CaseFact:
        if target == self.status:
            return self
        if target not in self._ALLOWED_TRANSITIONS[self.status]:
            raise InvalidCaseFactTransition(self.fact_id, self.status, target)
        transition_time = time.time() if at is None else at
        if transition_time < self.updated_at:
            raise ValueError("事实状态变更时间不能早于更新时间")
        confirmation = (
            {"confirmed_by": actor_id, "confirmed_at": transition_time}
            if target == "confirmed"
            else {"confirmed_by": None, "confirmed_at": None}
        )
        return cast(
            "CaseFact",
            self.model_copy(
                update={
                    "status": target,
                    "updated_at": transition_time,
                    **confirmation,
                }
            ),
        )

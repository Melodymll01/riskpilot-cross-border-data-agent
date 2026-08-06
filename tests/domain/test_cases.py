"""V2 Case 聚合根与状态机测试。"""

from __future__ import annotations

from datetime import date

import pytest
from pydantic import ValidationError

from domain import Case, InvalidCaseTransition


def _case(**overrides: object) -> Case:
    values: dict[str, object] = {
        "case_id": "case_001",
        "workspace_id": "ws_001",
        "title": "海外客服系统数据出境评估",
        "owner_id": "github:alice",
        "created_at": 100.0,
        "updated_at": 100.0,
    }
    values.update(overrides)
    return Case(**values)  # type: ignore[arg-type]


class TestCaseModel:
    def test_defaults(self) -> None:
        case = _case()
        assert case.status == "draft"
        assert case.jurisdiction == "CN"
        assert case.assessment_date is None
        assert case.reviewer_id is None

    def test_accepts_assessment_date(self) -> None:
        case = _case(assessment_date=date(2026, 8, 6))
        assert case.assessment_date == date(2026, 8, 6)

    def test_rejects_blank_title(self) -> None:
        with pytest.raises(ValidationError, match="title"):
            _case(title="   ")

    def test_rejects_blank_optional_identifier(self) -> None:
        with pytest.raises(ValidationError, match="reviewer_id"):
            _case(reviewer_id=" ")

    def test_updated_at_cannot_precede_created_at(self) -> None:
        with pytest.raises(ValidationError, match="updated_at"):
            _case(created_at=200.0, updated_at=100.0)

    def test_json_round_trip(self) -> None:
        case = _case(assessment_date=date(2026, 8, 6))
        assert Case.model_validate_json(case.model_dump_json()) == case


class TestCaseTransitions:
    def test_happy_path_to_completed(self) -> None:
        case = _case()
        path = [
            "collecting",
            "ready_for_assessment",
            "assessing",
            "review_required",
            "completed",
        ]
        for index, status in enumerate(path, start=1):
            case = case.transition_to(status, at=100.0 + index)  # type: ignore[arg-type]
        assert case.status == "completed"
        assert case.updated_at == 105.0

    def test_same_status_is_idempotent(self) -> None:
        case = _case()
        assert case.transition_to("draft", at=200.0) is case

    def test_invalid_transition_rejected(self) -> None:
        case = _case()
        with pytest.raises(InvalidCaseTransition) as exc_info:
            case.transition_to("completed", at=101.0)
        assert exc_info.value.case_id == "case_001"
        assert exc_info.value.source == "draft"
        assert exc_info.value.target == "completed"

    def test_archived_is_terminal(self) -> None:
        case = _case().transition_to("archived", at=101.0)
        with pytest.raises(InvalidCaseTransition):
            case.transition_to("collecting", at=102.0)

    def test_can_reassess_completed_case(self) -> None:
        case = _case(status="completed")
        assert case.can_transition_to("ready_for_assessment") is True

    def test_transition_time_cannot_move_backwards(self) -> None:
        case = _case(updated_at=200.0)
        with pytest.raises(ValueError, match="变更时间"):
            case.transition_to("collecting", at=199.0)

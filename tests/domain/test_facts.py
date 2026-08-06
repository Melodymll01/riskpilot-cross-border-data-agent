"""V2 案件事实与证据引用领域测试。"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from domain import CaseFact, CaseFactEvidence, InvalidCaseFactTransition


def _fact(**overrides: object) -> CaseFact:
    values: dict[str, object] = {
        "fact_id": "fact_001",
        "case_id": "case_001",
        "field_name": "important_data_involved",
        "value": True,
        "source_type": "document",
        "confidence": 0.9,
        "criticality": "critical",
        "created_by": "system:extractor",
        "created_at": 100.0,
        "updated_at": 100.0,
    }
    values.update(overrides)
    return CaseFact(**values)  # type: ignore[arg-type]


class TestCaseFactEvidence:
    def test_happy_path(self) -> None:
        evidence = CaseFactEvidence(
            evidence_id="evidence_001",
            case_id="case_001",
            fact_id="fact_001",
            fact_version=1,
            document_id="doc_001",
            document_version_id="ver_001",
            page_number=2,
            quote="材料明确说明涉及重要数据",
            start_offset=10,
            end_offset=20,
            confidence=0.95,
            created_at=100.0,
        )
        assert evidence.page_number == 2

    def test_offsets_must_be_paired(self) -> None:
        with pytest.raises(ValidationError, match="同时"):
            CaseFactEvidence(
                evidence_id="evidence_001",
                case_id="case_001",
                fact_id="fact_001",
                fact_version=1,
                document_id="doc_001",
                document_version_id="ver_001",
                page_number=1,
                quote="证据",
                start_offset=1,
                created_at=100.0,
            )

    def test_end_offset_must_follow_start(self) -> None:
        with pytest.raises(ValidationError, match="大于"):
            CaseFactEvidence(
                evidence_id="evidence_001",
                case_id="case_001",
                fact_id="fact_001",
                fact_version=1,
                document_id="doc_001",
                document_version_id="ver_001",
                page_number=1,
                quote="证据",
                start_offset=5,
                end_offset=5,
                created_at=100.0,
            )


class TestCaseFact:
    def test_proposed_fact_not_usable_for_rules(self) -> None:
        fact = _fact()
        assert fact.status == "proposed"
        assert fact.usable_for_rules is False

    def test_confirm_records_actor_and_time(self) -> None:
        confirmed = _fact().transition_to(
            "confirmed",
            actor_id="github:reviewer",
            at=101.0,
        )
        assert confirmed.status == "confirmed"
        assert confirmed.confirmed_by == "github:reviewer"
        assert confirmed.confirmed_at == 101.0
        assert confirmed.usable_for_rules is True

    def test_confirmed_constructor_requires_confirmation_metadata(self) -> None:
        with pytest.raises(ValidationError, match="确认"):
            _fact(status="confirmed")

    def test_reject_clears_confirmation_metadata(self) -> None:
        confirmed = _fact().transition_to(
            "confirmed",
            actor_id="github:reviewer",
            at=101.0,
        )
        rejected = confirmed.transition_to(
            "rejected",
            actor_id="github:reviewer",
            at=102.0,
        )
        assert rejected.confirmed_by is None
        assert rejected.confirmed_at is None

    def test_revision_increments_version_and_resets_confirmation(self) -> None:
        confirmed = _fact().transition_to(
            "confirmed",
            actor_id="github:reviewer",
            at=101.0,
        )
        revised = confirmed.propose_revision(
            value=False,
            source_type="user",
            confidence=1.0,
            actor_id="github:editor",
            at=102.0,
        )
        assert revised.version == 2
        assert revised.value is False
        assert revised.status == "proposed"
        assert revised.confirmed_by is None
        assert revised.created_by == "github:editor"

    def test_invalid_transition_rejected(self) -> None:
        rejected = _fact(status="rejected")
        with pytest.raises(InvalidCaseFactTransition):
            rejected.transition_to(
                "confirmed",
                actor_id="github:reviewer",
                at=101.0,
            )

    @pytest.mark.parametrize(
        "value",
        [
            True,
            100,
            1.5,
            "EU",
            ["name", "email"],
            {"country": "DE"},
            None,
        ],
    )
    def test_supported_json_values(self, value: object) -> None:
        assert _fact(value=value).value == value

    def test_json_round_trip(self) -> None:
        fact = _fact()
        assert CaseFact.model_validate_json(fact.model_dump_json()) == fact

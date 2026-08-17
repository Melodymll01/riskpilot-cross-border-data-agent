"""ForgetMemoryUseCase 测试：主动遗忘编排 + 审计（S-030d）。"""

from __future__ import annotations

import pytest

from app.use_cases.forget_memory import ForgetMemoryUseCase
from domain.models import AuditAction, Fact, SessionProfile
from tests.fakes.fake_audit_log import FakeAuditLogRepo
from tests.fakes.fake_memory import FakeMemory

pytestmark = pytest.mark.unit


def _fact(owner: str, text: str) -> Fact:
    return Fact(fact_id=f"f_{abs(hash(text)) % 9999}", owner_id=owner, text=text)


class TestForget:
    def test_delegates_and_returns_counts(self) -> None:
        mem = FakeMemory(
            owners={"t1": "anon:o1"},
            facts={"anon:o1": [_fact("anon:o1", "事实A")]},
            profiles={"anon:o1": SessionProfile(owner_id="anon:o1", facts={"k": "v"})},
        )
        uc = ForgetMemoryUseCase(mem, audit_log=FakeAuditLogRepo())

        result = uc.forget("anon:o1", scope="memory")

        assert mem.forget_calls == [("anon:o1", "memory")]
        assert result.facts_deleted == 1
        assert result.profile_deleted == 1
        assert result.summaries_deleted == 1

    def test_success_records_audit(self) -> None:
        mem = FakeMemory(facts={"anon:o1": [_fact("anon:o1", "x")]})
        audit = FakeAuditLogRepo()
        uc = ForgetMemoryUseCase(mem, audit_log=audit)

        uc.forget("anon:o1", scope="all")

        assert len(audit.entries) == 1
        e = audit.entries[0]
        assert e.action == AuditAction.MEMORY_FORGET
        assert e.actor_id == "anon:o1"
        assert e.resource == "anon:o1"
        assert e.success is True
        assert e.extra_json["scope"] == "all"
        assert e.extra_json["facts_deleted"] == 1

    def test_no_audit_log_is_silent(self) -> None:
        mem = FakeMemory()
        uc = ForgetMemoryUseCase(mem, audit_log=None)
        result = uc.forget("anon:o1")  # 不抛
        assert result.owner_id == "anon:o1"

    def test_memory_none_returns_zero_no_audit(self) -> None:
        audit = FakeAuditLogRepo()
        uc = ForgetMemoryUseCase(None, audit_log=audit)

        result = uc.forget("anon:o1", scope="all")

        assert result.total_deleted == 0
        assert audit.entries == []

    def test_failure_records_audit_then_raises(self) -> None:
        class _BoomMemory:
            def forget(self, owner_id: str, *, scope: str = "memory") -> None:
                raise RuntimeError("boom")

        audit = FakeAuditLogRepo()
        uc = ForgetMemoryUseCase(_BoomMemory(), audit_log=audit)  # type: ignore[arg-type]

        with pytest.raises(RuntimeError, match="boom"):
            uc.forget("anon:o1", scope="memory")

        assert len(audit.entries) == 1
        assert audit.entries[0].success is False
        assert audit.entries[0].error == "boom"


class TestDeleteFact:
    def test_deletes_and_records_audit(self) -> None:
        mem = FakeMemory(facts={"anon:o1": [_fact("anon:o1", "事实A")]})
        fact_id = mem.list_facts("anon:o1")[0].fact_id
        audit = FakeAuditLogRepo()
        uc = ForgetMemoryUseCase(mem, audit_log=audit)

        deleted = uc.delete_fact("anon:o1", fact_id)

        assert deleted is True
        assert mem.delete_fact_calls == [("anon:o1", fact_id)]
        assert mem.list_facts("anon:o1") == []
        assert len(audit.entries) == 1
        e = audit.entries[0]
        assert e.action == AuditAction.MEMORY_FACT_DELETE
        assert e.actor_id == "anon:o1"
        assert e.resource == "anon:o1"
        assert e.success is True
        assert e.extra_json["fact_id"] == fact_id

    def test_not_found_returns_false_no_audit(self) -> None:
        mem = FakeMemory(facts={"anon:o1": [_fact("anon:o1", "事实A")]})
        audit = FakeAuditLogRepo()
        uc = ForgetMemoryUseCase(mem, audit_log=audit)

        deleted = uc.delete_fact("anon:o1", "f_missing")

        assert deleted is False
        assert audit.entries == []  # 没真删 → 不落审计

    def test_memory_none_returns_false_no_audit(self) -> None:
        audit = FakeAuditLogRepo()
        uc = ForgetMemoryUseCase(None, audit_log=audit)

        assert uc.delete_fact("anon:o1", "f1") is False
        assert audit.entries == []

    def test_failure_records_audit_then_raises(self) -> None:
        class _BoomMemory:
            def delete_fact(self, owner_id: str, fact_id: str) -> bool:
                raise RuntimeError("boom")

        audit = FakeAuditLogRepo()
        uc = ForgetMemoryUseCase(_BoomMemory(), audit_log=audit)  # type: ignore[arg-type]

        with pytest.raises(RuntimeError, match="boom"):
            uc.delete_fact("anon:o1", "f1")

        assert len(audit.entries) == 1
        assert audit.entries[0].action == AuditAction.MEMORY_FACT_DELETE
        assert audit.entries[0].success is False
        assert audit.entries[0].error == "boom"

"""domain.models 模型校验单测。

仅覆盖：
- 字段约束（min_length / ge / le / Literal）
- frozen 与 extra="forbid" 不变量
- JSON round-trip
- 默认值与时间戳生成
- 跨字段无副作用（不测业务）
"""

from __future__ import annotations

import time

import pytest
from pydantic import ValidationError

from domain import (
    Artifact,
    AuditAction,
    AuditEntry,
    BaseDomainModel,
    Chunk,
    Citation,
    EvidenceJudgement,
    Fact,
    KbChunk,
    KbDocument,
    Message,
    SessionProfile,
    Task,
    ToolCall,
    User,
    WebResult,
)


def _now() -> float:
    return time.time()


# === User ===


def _make_user(**overrides: object) -> User:
    defaults: dict[str, object] = {
        "user_id": "github:torvalds",
        "provider": "github",
        "provider_id": "torvalds",
        "email": "linus@example.com",
        "display_name": "Linus Torvalds",
        "avatar_url": "https://example.com/a.png",
        "created_at": _now(),
        "last_active_at": _now(),
    }
    defaults.update(overrides)
    return User(**defaults)  # type: ignore[arg-type]


class TestUser:
    def test_happy_path(self) -> None:
        u = _make_user()
        assert u.user_id == "github:torvalds"
        assert u.provider == "github"

    def test_anonymous_provider_accepted(self) -> None:
        u = _make_user(provider="anonymous", user_id="anon:abc", provider_id="abc")
        assert u.provider == "anonymous"

    def test_invalid_provider_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _make_user(provider="wechat")

    def test_empty_user_id_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _make_user(user_id="")

    def test_empty_display_name_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _make_user(display_name="")

    def test_email_optional(self) -> None:
        u = _make_user(email=None, avatar_url=None)
        assert u.email is None
        assert u.avatar_url is None


# === Task / Message / ToolCall / Artifact ===


class TestTask:
    def test_defaults(self) -> None:
        t = Task(
            task_id="t1",
            owner_id="anon:u1",
            created_at=_now(),
            updated_at=_now(),
        )
        assert t.title == ""
        assert t.state == "planning"
        assert t.collected_facts == {}

    def test_invalid_state_rejected(self) -> None:
        with pytest.raises(ValidationError):
            Task(
                task_id="t1",
                owner_id="anon:u1",
                state="exploded",  # type: ignore[arg-type]
                created_at=_now(),
                updated_at=_now(),
            )

    def test_empty_owner_id_rejected(self) -> None:
        with pytest.raises(ValidationError):
            Task(task_id="t1", owner_id="", created_at=_now(), updated_at=_now())


class TestMessage:
    def test_minimal(self) -> None:
        m = Message(msg_id="m1", task_id="t1", role="user", content="hi")
        assert m.tool_call_id is None
        assert m.citations == []
        assert m.created_at > 0

    def test_invalid_role_rejected(self) -> None:
        with pytest.raises(ValidationError):
            Message(msg_id="m1", task_id="t1", role="admin", content="x")  # type: ignore[arg-type]

    def test_citations_typed(self) -> None:
        c = Citation(source_type="law", source_name="PIPL")
        m = Message(msg_id="m1", task_id="t1", role="assistant", content="x", citations=[c])
        assert m.citations[0].source_name == "PIPL"


class TestToolCall:
    def test_defaults(self) -> None:
        c = ToolCall(tool_call_id="tc1", task_id="t1", tool_name="search_law")
        assert c.status == "pending"
        assert c.duration_ms is None
        assert c.output_json is None

    def test_negative_duration_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ToolCall(tool_call_id="tc1", task_id="t1", tool_name="x", duration_ms=-1)


class TestArtifact:
    def test_minimal(self) -> None:
        a = Artifact(artifact_id="a1", task_id="t1", artifact_type="risk_profile")
        assert a.payload_json == {}
        assert a.created_at > 0


# === Chunk / WebResult / EvidenceJudgement ===


class TestChunk:
    def test_minimal(self) -> None:
        c = Chunk(chunk_id="c1", text="...", source_type="law", source_name="PIPL")
        assert c.score == 0.0
        assert c.metadata == {}

    def test_empty_source_name_rejected(self) -> None:
        with pytest.raises(ValidationError):
            Chunk(chunk_id="c1", text="t", source_type="law", source_name="")


class TestEvidenceJudgement:
    def test_confidence_range(self) -> None:
        j = EvidenceJudgement(factor_id="F1", label="applicable", confidence=0.9)
        assert j.confidence == 0.9

    def test_confidence_out_of_range_rejected(self) -> None:
        with pytest.raises(ValidationError):
            EvidenceJudgement(factor_id="F1", label="x", confidence=1.5)


class TestEvidenceSpan:
    def test_minimal(self) -> None:
        from domain.models import EvidenceSpan

        s = EvidenceSpan(text="某条款片段")
        assert s.text == "某条款片段"
        assert s.start is None and s.end is None

    def test_with_offsets(self) -> None:
        from domain.models import EvidenceSpan

        s = EvidenceSpan(text="片段", start=12, end=16)
        assert s.start == 12 and s.end == 16

    def test_empty_text_rejected(self) -> None:
        from domain.models import EvidenceSpan

        with pytest.raises(ValidationError):
            EvidenceSpan(text="")


class TestRiskProfile:
    def test_minimal_supported(self) -> None:
        from domain.models import EvidenceSpan, RiskProfile

        rp = RiskProfile(
            target="个人信息出境标准合同备案的适用条件",
            evidence_state="supported",
            evidence_spans=[EvidenceSpan(text="非关基/非重要数据/低于阈值")],
            explanation="文档显式列出三类适用条件",
        )
        assert rp.evidence_state == "supported"
        assert len(rp.evidence_spans) == 1

    def test_default_spans_and_explanation(self) -> None:
        from domain.models import RiskProfile

        rp = RiskProfile(target="某命题", evidence_state="not_disclosed")
        assert rp.evidence_spans == []
        assert rp.explanation == ""
        assert rp.metadata == {}

    def test_invalid_evidence_state_rejected(self) -> None:
        from domain.models import RiskProfile

        with pytest.raises(ValidationError):
            RiskProfile(target="x", evidence_state="bogus")  # type: ignore[arg-type]

    def test_empty_target_rejected(self) -> None:
        from domain.models import RiskProfile

        with pytest.raises(ValidationError):
            RiskProfile(target="", evidence_state="supported")


class TestWebResult:
    def test_minimal(self) -> None:
        w = WebResult(title="t", url="https://example.com", snippet="s")
        assert w.snippet == "s"

    def test_empty_url_rejected(self) -> None:
        with pytest.raises(ValidationError):
            WebResult(title="t", url="", snippet="s")


# === SessionProfile / Fact ===


class TestSessionProfile:
    def test_minimal(self) -> None:
        p = SessionProfile(owner_id="anon:u1")
        assert p.facts == {}
        assert p.updated_at > 0


class TestFact:
    def test_minimal(self) -> None:
        f = Fact(fact_id="f1", owner_id="anon:u1", text="prefers PIPL excerpts")
        assert f.tags == []


class TestKbDocument:
    """``KbDocument`` 是知识库管理面的文档级聚合视角。"""

    def _make(self, **overrides: object) -> KbDocument:
        defaults: dict[str, object] = {
            "source_name": "PIPL.txt",
            "source_type": "file",
            "title": "个人信息保护法",
            "source_url": None,
            "chunk_count": 12,
            "category": "法规",
        }
        defaults.update(overrides)
        return KbDocument(**defaults)  # type: ignore[arg-type]

    def test_happy_path(self) -> None:
        d = self._make()
        assert isinstance(d, KbDocument)
        assert d.source_name == "PIPL.txt"
        assert d.source_type == "file"
        assert d.chunk_count == 12

    def test_source_type_literal_enforced(self) -> None:
        with pytest.raises(ValidationError):
            self._make(source_type="ftp")

    def test_chunk_count_non_negative(self) -> None:
        with pytest.raises(ValidationError):
            self._make(chunk_count=-1)

    def test_empty_source_name_rejected(self) -> None:
        with pytest.raises(ValidationError):
            self._make(source_name="")

    def test_json_round_trip(self) -> None:
        d = self._make()
        d2 = KbDocument.model_validate_json(d.model_dump_json())
        assert d == d2


class TestKbChunk:
    """``KbChunk`` 是写侧的入库 chunk（不含 embedding / score）。"""

    def _make(self, **overrides: object) -> KbChunk:
        defaults: dict[str, object] = {
            "chunk_id": "c0",
            "text": "第一条 ...",
            "source_name": "PIPL.txt",
            "source_type": "file",
            "title": "个人信息保护法",
            "source_url": None,
            "chunk_index": 0,
            "category": "法规",
        }
        defaults.update(overrides)
        return KbChunk(**defaults)  # type: ignore[arg-type]

    def test_happy_path(self) -> None:
        c = self._make()
        assert isinstance(c, KbChunk)
        assert c.chunk_id == "c0"

    def test_empty_text_rejected(self) -> None:
        with pytest.raises(ValidationError):
            self._make(text="")

    def test_chunk_index_non_negative(self) -> None:
        with pytest.raises(ValidationError):
            self._make(chunk_index=-1)

    def test_source_type_literal_enforced(self) -> None:
        with pytest.raises(ValidationError):
            self._make(source_type="ftp")

    def test_json_round_trip(self) -> None:
        c = self._make()
        c2 = KbChunk.model_validate_json(c.model_dump_json())
        assert c == c2


# === AuditEntry (Step 021) ===


def _make_audit_entry(**overrides: object) -> AuditEntry:
    defaults: dict[str, object] = {
        "actor_id": "github:Melodymll01",
        "action": AuditAction.KB_DELETE,
        "resource": "doc.pdf",
        "success": True,
    }
    defaults.update(overrides)
    return AuditEntry(**defaults)  # type: ignore[arg-type]


class TestAuditEntry:
    def test_happy_path_defaults(self) -> None:
        e = _make_audit_entry()
        assert e.actor_id == "github:Melodymll01"
        assert e.action == "kb.delete"
        assert e.resource == "doc.pdf"
        assert e.success is True
        assert e.error is None
        assert e.request_id is None
        assert e.extra_json == {}
        # 默认 timestamp 由 default_factory 填充
        assert e.timestamp > 0

    def test_failure_path(self) -> None:
        e = _make_audit_entry(success=False, error="loader exploded")
        assert e.success is False
        assert e.error == "loader exploded"

    def test_extra_json_preserved(self) -> None:
        e = _make_audit_entry(extra_json={"deleted_count": 3, "category": "law"})
        assert e.extra_json["deleted_count"] == 3
        assert e.extra_json["category"] == "law"

    def test_request_id_optional(self) -> None:
        e = _make_audit_entry(request_id="req-abc-123")
        assert e.request_id == "req-abc-123"

    def test_frozen(self) -> None:
        e = _make_audit_entry()
        with pytest.raises(ValidationError):
            e.actor_id = "evil"  # type: ignore[misc]

    def test_extra_forbidden(self) -> None:
        with pytest.raises(ValidationError):
            AuditEntry(  # type: ignore[call-arg]
                actor_id="x",
                action="kb.delete",
                resource="r",
                success=True,
                stowaway="boom",
            )

    def test_action_constants(self) -> None:
        assert AuditAction.KB_DELETE == "kb.delete"
        assert AuditAction.KB_INGEST_FILE == "kb.ingest_file"
        assert AuditAction.KB_INGEST_WEB == "kb.ingest_web"

    def test_json_round_trip(self) -> None:
        e1 = _make_audit_entry(
            request_id="r-1",
            extra_json={"k": "v"},
            timestamp=1700000000.0,
        )
        e2 = AuditEntry.model_validate_json(e1.model_dump_json())
        assert e1 == e2


# === 通用不变量 ===


class TestInvariants:
    """所有 domain 模型必须 frozen + extra='forbid' + JSON round-trip。"""

    def test_frozen(self) -> None:
        u = _make_user()
        with pytest.raises(ValidationError):
            u.display_name = "Hacked"  # type: ignore[misc]

    def test_extra_forbidden(self) -> None:
        with pytest.raises(ValidationError):
            User(  # type: ignore[call-arg]
                user_id="x",
                provider="anonymous",
                provider_id="x",
                display_name="X",
                created_at=0.0,
                last_active_at=0.0,
                extra_field="boom",
            )

    @pytest.mark.parametrize(
        "model_instance",
        [
            _make_user(),
            Task(task_id="t1", owner_id="anon:u1", created_at=1.0, updated_at=1.0),
            Message(msg_id="m1", task_id="t1", role="user", content="hi"),
            ToolCall(tool_call_id="tc1", task_id="t1", tool_name="x"),
            Artifact(artifact_id="a1", task_id="t1", artifact_type="x"),
            Chunk(chunk_id="c1", text="t", source_type="s", source_name="n"),
            WebResult(title="t", url="https://x.example", snippet=""),
            EvidenceJudgement(factor_id="F1", label="x"),
            SessionProfile(owner_id="anon:u1"),
            Fact(fact_id="f1", owner_id="anon:u1", text="x"),
        ],
    )
    def test_json_round_trip(self, model_instance: BaseDomainModel) -> None:
        payload = model_instance.model_dump_json()
        restored = type(model_instance).model_validate_json(payload)
        assert restored == model_instance

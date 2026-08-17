"""核心业务表的 SQLAlchemy 2.x 映射。

本模块只属于 infra。Domain Pydantic Model 不导入这些 ORM 类型。
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from pgvector.sqlalchemy import HALFVEC, Vector
from sqlalchemy import (
    JSON,
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    cast,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

JsonType = JSON().with_variant(JSONB(), "postgresql")
VectorType = Vector().with_variant(JSON(), "sqlite")


class Base(DeclarativeBase):
    pass


class WorkspaceRow(Base):
    __tablename__ = "workspaces"

    workspace_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(32), index=True)
    created_by: Mapped[str] = mapped_column(String(256))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class WorkspaceMembershipRow(Base):
    __tablename__ = "workspace_memberships"

    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.workspace_id", ondelete="CASCADE"),
        primary_key=True,
    )
    user_id: Mapped[str] = mapped_column(String(256), primary_key=True, index=True)
    role: Mapped[str] = mapped_column(String(32))
    joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class CaseRow(Base):
    __tablename__ = "compliance_cases"
    __table_args__ = (Index("ix_cases_workspace_updated", "workspace_id", "updated_at"),)

    case_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.workspace_id", ondelete="CASCADE"),
        index=True,
    )
    title: Mapped[str] = mapped_column(String(255))
    description: Mapped[str] = mapped_column(Text, default="")
    jurisdiction: Mapped[str] = mapped_column(String(32), default="CN")
    scenario_type: Mapped[str] = mapped_column(String(100), default="")
    assessment_date: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(64), index=True)
    owner_id: Mapped[str] = mapped_column(String(256))
    reviewer_id: Mapped[str | None] = mapped_column(String(256))
    active_assessment_id: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class DocumentRow(Base):
    __tablename__ = "documents"
    __table_args__ = (Index("ix_documents_workspace_updated", "workspace_id", "updated_at"),)

    document_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.workspace_id", ondelete="CASCADE"),
        index=True,
    )
    logical_name: Mapped[str] = mapped_column(String(255))
    document_type: Mapped[str] = mapped_column(String(100), index=True)
    status: Mapped[str] = mapped_column(String(32), index=True)
    created_by: Mapped[str] = mapped_column(String(256))
    current_version_id: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class DocumentVersionRow(Base):
    __tablename__ = "document_versions"
    __table_args__ = (
        UniqueConstraint("document_id", "version_number"),
        Index("ix_document_versions_document", "document_id", "version_number"),
    )

    version_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    document_id: Mapped[str] = mapped_column(
        ForeignKey("documents.document_id", ondelete="CASCADE"),
    )
    version_number: Mapped[int] = mapped_column(Integer)
    object_key: Mapped[str] = mapped_column(String(1000), unique=True)
    sha256: Mapped[str] = mapped_column(String(64))
    mime_type: Mapped[str] = mapped_column(String(255))
    size_bytes: Mapped[int] = mapped_column(Integer)
    parser_version: Mapped[str] = mapped_column(String(100), default="")
    page_count: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class CaseDocumentRow(Base):
    __tablename__ = "case_documents"

    case_id: Mapped[str] = mapped_column(
        ForeignKey("compliance_cases.case_id", ondelete="CASCADE"),
        primary_key=True,
    )
    document_id: Mapped[str] = mapped_column(
        ForeignKey("documents.document_id", ondelete="CASCADE"),
        primary_key=True,
    )
    purpose: Mapped[str] = mapped_column(Text, default="")
    added_by: Mapped[str] = mapped_column(String(256))
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ProcessingJobRow(Base):
    __tablename__ = "processing_jobs"
    __table_args__ = (
        CheckConstraint(
            "progress >= 0 AND progress <= 1",
            name="processing_jobs_progress_check",
        ),
        Index("ix_processing_jobs_version_created", "document_version_id", "created_at"),
    )

    job_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    document_version_id: Mapped[str] = mapped_column(
        ForeignKey("document_versions.version_id", ondelete="CASCADE"),
    )
    status: Mapped[str] = mapped_column(String(32), index=True)
    current_stage: Mapped[str] = mapped_column(String(64))
    progress: Mapped[float] = mapped_column(Float)
    error_code: Mapped[str | None] = mapped_column(String(128))
    error_message: Mapped[str | None] = mapped_column(Text)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DocumentParseSnapshotRow(Base):
    __tablename__ = "document_parse_snapshots"

    snapshot_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    document_version_id: Mapped[str] = mapped_column(
        ForeignKey("document_versions.version_id", ondelete="CASCADE"),
        unique=True,
    )
    payload: Mapped[dict[str, Any]] = mapped_column(JsonType)
    parsed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class EvidenceChunkRow(Base):
    __tablename__ = "evidence_chunks"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.workspace_id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["case_id"],
            ["compliance_cases.case_id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["document_id"],
            ["documents.document_id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["document_version_id"],
            ["document_versions.version_id"],
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "case_id",
            "document_version_id",
            "page_number",
            "chunk_index",
        ),
        Index(
            "ix_evidence_chunks_scope",
            "workspace_id",
            "case_id",
            "document_version_id",
        ),
        Index(
            "ix_evidence_chunks_search_tokens_fts",
            text("to_tsvector('simple', search_tokens)"),
            postgresql_using="gin",
        ).ddl_if(dialect="postgresql"),
    )

    chunk_id: Mapped[str] = mapped_column(String(256), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(128), index=True)
    case_id: Mapped[str] = mapped_column(String(128), index=True)
    document_id: Mapped[str] = mapped_column(String(128))
    document_version_id: Mapped[str] = mapped_column(String(128), index=True)
    page_number: Mapped[int] = mapped_column(Integer)
    chunk_index: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    search_tokens: Mapped[str] = mapped_column(Text, default="")
    source_sha256: Mapped[str] = mapped_column(String(64))
    embedding: Mapped[list[float]] = mapped_column(VectorType)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


Index(
    "ix_evidence_chunks_embedding_hnsw_2048",
    cast(EvidenceChunkRow.embedding, HALFVEC(2048)).label("embedding"),
    postgresql_using="hnsw",
    postgresql_ops={"embedding": "halfvec_cosine_ops"},
    postgresql_where=func.vector_dims(EvidenceChunkRow.embedding) == 2048,
).ddl_if(dialect="postgresql")


class CaseFactRow(Base):
    __tablename__ = "case_facts"
    __table_args__ = (Index("ix_case_facts_case_updated", "case_id", "updated_at"),)

    fact_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    case_id: Mapped[str] = mapped_column(
        ForeignKey("compliance_cases.case_id", ondelete="CASCADE"),
    )
    field_name: Mapped[str] = mapped_column(String(128), index=True)
    value: Mapped[Any | None] = mapped_column(JsonType)
    status: Mapped[str] = mapped_column(String(32), index=True)
    source_type: Mapped[str] = mapped_column(String(32))
    confidence: Mapped[float] = mapped_column(Float)
    criticality: Mapped[str] = mapped_column(String(32))
    version: Mapped[int] = mapped_column(Integer)
    created_by: Mapped[str] = mapped_column(String(256))
    confirmed_by: Mapped[str | None] = mapped_column(String(256))
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class CaseFactVersionRow(Base):
    __tablename__ = "case_fact_versions"

    fact_id: Mapped[str] = mapped_column(
        ForeignKey("case_facts.fact_id", ondelete="CASCADE"),
        primary_key=True,
    )
    version: Mapped[int] = mapped_column(Integer, primary_key=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JsonType)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class CaseFactEvidenceRow(Base):
    __tablename__ = "case_fact_evidence"
    __table_args__ = (
        ForeignKeyConstraint(
            ["case_id"],
            ["compliance_cases.case_id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["fact_id", "fact_version"],
            ["case_fact_versions.fact_id", "case_fact_versions.version"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["document_id"],
            ["documents.document_id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["document_version_id"],
            ["document_versions.version_id"],
            ondelete="CASCADE",
        ),
        Index("ix_case_fact_evidence_fact", "fact_id", "fact_version"),
    )

    evidence_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    case_id: Mapped[str] = mapped_column(String(128), index=True)
    fact_id: Mapped[str] = mapped_column(String(128), index=True)
    fact_version: Mapped[int] = mapped_column(Integer)
    document_id: Mapped[str] = mapped_column(String(128))
    document_version_id: Mapped[str] = mapped_column(String(128))
    page_number: Mapped[int] = mapped_column(Integer)
    quote: Mapped[str] = mapped_column(Text)
    start_offset: Mapped[int | None] = mapped_column(Integer)
    end_offset: Mapped[int | None] = mapped_column(Integer)
    confidence: Mapped[float] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class PolicyRuleRow(Base):
    __tablename__ = "policy_rules"
    __table_args__ = (
        Index(
            "ix_policy_rules_lookup",
            "workspace_id",
            "ruleset_version",
            "jurisdiction",
            "status",
            "effective_from",
        ),
    )

    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.workspace_id", ondelete="CASCADE"),
        primary_key=True,
    )
    rule_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    ruleset_version: Mapped[str] = mapped_column(String(128), primary_key=True)
    jurisdiction: Mapped[str] = mapped_column(String(32))
    effective_from: Mapped[date] = mapped_column(Date)
    effective_to: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(32), index=True)
    required_fact_fields: Mapped[list[str]] = mapped_column(JsonType)
    condition: Mapped[dict[str, Any]] = mapped_column(JsonType)
    result: Mapped[dict[str, Any]] = mapped_column(JsonType)
    source_clause_ids: Mapped[list[str]] = mapped_column(JsonType)


class AssessmentRow(Base):
    __tablename__ = "assessments"
    __table_args__ = (
        UniqueConstraint("case_id", "version"),
        Index("ix_assessments_case_version", "case_id", "version"),
    )

    assessment_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    case_id: Mapped[str] = mapped_column(
        ForeignKey("compliance_cases.case_id", ondelete="CASCADE"),
    )
    version: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(32), index=True)
    assessment_date: Mapped[date] = mapped_column(Date)
    jurisdiction: Mapped[str] = mapped_column(String(32))
    ruleset_version: Mapped[str] = mapped_column(String(128))
    fact_versions: Mapped[dict[str, int]] = mapped_column(JsonType)
    policy_evaluations: Mapped[list[dict[str, Any]]] = mapped_column(JsonType)
    risk_level: Mapped[str] = mapped_column(String(32))
    candidate_paths: Mapped[list[str]] = mapped_column(JsonType)
    generated_by_run_id: Mapped[str | None] = mapped_column(String(128))
    approved_by: Mapped[str | None] = mapped_column(String(256))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    review_comment: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class AssessmentEvidenceCitationRow(Base):
    __tablename__ = "assessment_evidence_citations"
    __table_args__ = (
        UniqueConstraint("assessment_id", "source_evidence_id"),
        Index("ix_assessment_evidence_fact", "assessment_id", "fact_id"),
    )

    citation_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    assessment_id: Mapped[str] = mapped_column(
        ForeignKey("assessments.assessment_id", ondelete="CASCADE"),
    )
    source_evidence_id: Mapped[str] = mapped_column(String(128))
    fact_id: Mapped[str] = mapped_column(String(128))
    fact_version: Mapped[int] = mapped_column(Integer)
    document_id: Mapped[str] = mapped_column(String(128))
    document_version_id: Mapped[str] = mapped_column(String(128))
    page_number: Mapped[int] = mapped_column(Integer)
    quote: Mapped[str] = mapped_column(Text)
    start_offset: Mapped[int | None] = mapped_column(Integer)
    end_offset: Mapped[int | None] = mapped_column(Integer)
    source_sha256: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class FindingRow(Base):
    __tablename__ = "assessment_findings"

    finding_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    assessment_id: Mapped[str] = mapped_column(
        ForeignKey("assessments.assessment_id", ondelete="CASCADE"),
        index=True,
    )
    finding_type: Mapped[str] = mapped_column(String(32))
    severity: Mapped[str] = mapped_column(String(32))
    title: Mapped[str] = mapped_column(String(500))
    description: Mapped[str] = mapped_column(Text)
    fact_ids: Mapped[list[str]] = mapped_column(JsonType)
    evidence_ids: Mapped[list[str]] = mapped_column(JsonType)
    clause_ids: Mapped[list[str]] = mapped_column(JsonType)
    rule_ids: Mapped[list[str]] = mapped_column(JsonType)
    status: Mapped[str] = mapped_column(String(32))


class ActionItemRow(Base):
    __tablename__ = "assessment_actions"

    action_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    assessment_id: Mapped[str] = mapped_column(
        ForeignKey("assessments.assessment_id", ondelete="CASCADE"),
        index=True,
    )
    title: Mapped[str] = mapped_column(String(500))
    description: Mapped[str] = mapped_column(Text)
    priority: Mapped[str] = mapped_column(String(32))
    owner_id: Mapped[str | None] = mapped_column(String(256))
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(32))
    related_finding_ids: Mapped[list[str]] = mapped_column(JsonType)


class AgentRunRow(Base):
    __tablename__ = "agent_runs"
    __table_args__ = (
        CheckConstraint("revision >= 1", name="agent_runs_revision_check"),
        Index("ix_agent_runs_case_created", "case_id", "created_at"),
        Index(
            "uq_agent_runs_active_case_workflow",
            "case_id",
            "workflow_type",
            unique=True,
            postgresql_where=text(
                "status IN ('queued','running','waiting_for_user','waiting_for_review','retrying')"
            ),
            sqlite_where=text(
                "status IN ('queued','running','waiting_for_user','waiting_for_review','retrying')"
            ),
        ),
    )

    run_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.workspace_id", ondelete="CASCADE"),
        index=True,
    )
    case_id: Mapped[str] = mapped_column(
        ForeignKey("compliance_cases.case_id", ondelete="CASCADE"),
        index=True,
    )
    workflow_type: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32), index=True)
    thread_id: Mapped[str] = mapped_column(String(256), unique=True)
    checkpoint_id: Mapped[str | None] = mapped_column(String(128))
    current_stage: Mapped[str] = mapped_column(String(100))
    model_config_snapshot: Mapped[dict[str, Any]] = mapped_column(JsonType)
    token_usage: Mapped[int] = mapped_column(Integer, default=0)
    cost: Mapped[float] = mapped_column(Float, default=0.0)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    created_by: Mapped[str] = mapped_column(String(256))
    error_code: Mapped[str | None] = mapped_column(String(128))
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RunCheckpointRow(Base):
    __tablename__ = "run_checkpoints"
    __table_args__ = (
        UniqueConstraint("run_id", "version"),
        Index("ix_run_checkpoints_run_version", "run_id", "version"),
    )

    checkpoint_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    run_id: Mapped[str] = mapped_column(
        ForeignKey("agent_runs.run_id", ondelete="CASCADE"),
    )
    thread_id: Mapped[str] = mapped_column(String(256))
    version: Mapped[int] = mapped_column(Integer)
    stage: Mapped[str] = mapped_column(String(100))
    state: Mapped[dict[str, Any]] = mapped_column(JsonType)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class RunEventRow(Base):
    __tablename__ = "run_events"
    __table_args__ = (
        UniqueConstraint("run_id", "sequence"),
        Index("ix_run_events_run_sequence", "run_id", "sequence"),
    )

    event_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    run_id: Mapped[str] = mapped_column(
        ForeignKey("agent_runs.run_id", ondelete="CASCADE"),
    )
    sequence: Mapped[int] = mapped_column(Integer)
    event_type: Mapped[str] = mapped_column(String(64))
    stage: Mapped[str | None] = mapped_column(String(100))
    payload: Mapped[dict[str, Any]] = mapped_column(JsonType)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

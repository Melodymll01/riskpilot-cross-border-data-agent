"""RiskPilot V3 HTTP 请求与响应模型。"""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

WorkspaceRoleValue = Literal["viewer", "editor", "reviewer", "admin"]
WorkspaceStatusValue = Literal["active", "archived"]
CaseStatusValue = Literal[
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


class CreateWorkspaceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=100)


class WorkspaceOut(BaseModel):
    workspace_id: str
    name: str
    status: WorkspaceStatusValue
    created_by: str
    created_at: float
    updated_at: float


class WorkspaceListResponse(BaseModel):
    workspaces: list[WorkspaceOut]


class UpsertWorkspaceMemberRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: WorkspaceRoleValue


class WorkspaceMembershipOut(BaseModel):
    workspace_id: str
    user_id: str
    role: WorkspaceRoleValue
    joined_at: float


class CreateCaseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: str = Field(min_length=1)
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=4000)
    jurisdiction: str = Field(default="CN", min_length=1, max_length=32)
    scenario_type: str = Field(default="", max_length=100)
    assessment_date: date | None = None
    reviewer_id: str | None = Field(default=None, min_length=1)


class UpdateCaseRequest(BaseModel):
    """PATCH 只更新显式传入字段；`None` 可清空日期或 reviewer。"""

    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=4000)
    jurisdiction: str | None = Field(default=None, min_length=1, max_length=32)
    scenario_type: str | None = Field(default=None, max_length=100)
    assessment_date: date | None = None
    reviewer_id: str | None = Field(default=None, min_length=1)


class TransitionCaseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target: CaseStatusValue


class CaseOut(BaseModel):
    case_id: str
    workspace_id: str
    title: str
    description: str
    jurisdiction: str
    scenario_type: str
    assessment_date: date | None
    status: CaseStatusValue
    owner_id: str
    reviewer_id: str | None
    active_assessment_id: str | None
    created_at: float
    updated_at: float


class CaseListResponse(BaseModel):
    cases: list[CaseOut]


class DocumentOut(BaseModel):
    document_id: str
    workspace_id: str
    logical_name: str
    document_type: str
    status: Literal[
        "uploaded",
        "queued",
        "parsing",
        "ocr",
        "chunking",
        "embedding",
        "indexing",
        "ready",
        "failed",
        "cancelled",
        "deleted",
    ]
    created_by: str
    current_version_id: str | None
    created_at: float
    updated_at: float


class DocumentVersionOut(BaseModel):
    version_id: str
    document_id: str
    version_number: int
    sha256: str
    mime_type: str
    size_bytes: int
    parser_version: str
    page_count: int | None
    created_at: float


class ProcessingJobOut(BaseModel):
    job_id: str
    document_version_id: str
    status: Literal["queued", "running", "completed", "failed", "cancelled"]
    current_stage: str
    progress: float
    error_code: str | None
    error_message: str | None
    retry_count: int
    revision: int
    created_at: float
    updated_at: float
    started_at: float | None
    completed_at: float | None


class DocumentUploadResponse(BaseModel):
    document: DocumentOut
    version: DocumentVersionOut
    job: ProcessingJobOut
    purpose: str


class CaseDocumentSummaryOut(DocumentOut):
    latest_job: ProcessingJobOut | None


class DocumentDetailResponse(BaseModel):
    document: DocumentOut
    version: DocumentVersionOut
    latest_job: ProcessingJobOut | None
    purpose: str


class DocumentListResponse(BaseModel):
    documents: list[CaseDocumentSummaryOut]


class ProcessingJobListResponse(BaseModel):
    jobs: list[ProcessingJobOut]


class ParseStageResponse(BaseModel):
    document: DocumentOut
    version: DocumentVersionOut
    job: ProcessingJobOut
    next_stage: Literal["ocr", "chunk"]
    page_count: int
    warnings: list[str]


class IndexStageResponse(BaseModel):
    document: DocumentOut
    job: ProcessingJobOut
    chunk_count: int


class EvidenceChunkOut(BaseModel):
    chunk_id: str
    document_id: str
    document_version_id: str
    page_number: int
    chunk_index: int
    text: str
    source_sha256: str


class EvidenceSearchHitOut(BaseModel):
    chunk: EvidenceChunkOut
    score: float
    vector_score: float
    bm25_score: float


class EvidenceSearchResponse(BaseModel):
    hits: list[EvidenceSearchHitOut]


class VisualAssetOut(BaseModel):
    asset_id: str
    workspace_id: str
    case_id: str
    filename: str
    mime_type: str
    width: int
    height: int
    caption: str
    created_by: str
    created_at: float


class VisualSearchHitOut(BaseModel):
    asset: VisualAssetOut
    score: float


class VisualSearchResponse(BaseModel):
    hits: list[VisualSearchHitOut]


FactStatusValue = Literal["proposed", "confirmed", "rejected", "conflicting", "unknown"]
FactSourceValue = Literal["user", "document", "system", "import"]
FactCriticalityValue = Literal["normal", "critical"]


class FactEvidenceInputRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_id: str = Field(min_length=1)
    document_version_id: str = Field(min_length=1)
    page_number: int = Field(ge=1)
    quote: str = Field(min_length=1, max_length=4000)
    start_offset: int | None = Field(default=None, ge=0)
    end_offset: int | None = Field(default=None, ge=0)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class CreateFactRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field_name: str = Field(min_length=1, max_length=200)
    value: object = None
    source_type: FactSourceValue
    confidence: float = Field(ge=0.0, le=1.0)
    criticality: FactCriticalityValue = "normal"
    evidence: list[FactEvidenceInputRequest] = Field(default_factory=list)


class ReviseFactRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: object = None
    source_type: FactSourceValue
    confidence: float = Field(ge=0.0, le=1.0)
    evidence: list[FactEvidenceInputRequest] = Field(default_factory=list)


class TransitionFactRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target: FactStatusValue


class ProposeFactsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field_names: list[str] = Field(min_length=1, max_length=100)
    document_ids: list[str] | None = Field(default=None, min_length=1, max_length=100)


class FactEvidenceOut(BaseModel):
    evidence_id: str
    fact_version: int
    document_id: str
    document_version_id: str
    page_number: int
    quote: str
    start_offset: int | None
    end_offset: int | None
    confidence: float
    created_at: float


class CaseFactOut(BaseModel):
    fact_id: str
    case_id: str
    field_name: str
    value: object
    status: FactStatusValue
    source_type: FactSourceValue
    confidence: float
    criticality: FactCriticalityValue
    version: int
    created_by: str
    confirmed_by: str | None
    confirmed_at: float | None
    created_at: float
    updated_at: float


class FactDetailResponse(BaseModel):
    fact: CaseFactOut
    evidence: list[FactEvidenceOut]


class FactListResponse(BaseModel):
    facts: list[CaseFactOut]


class FactProposalBatchResponse(BaseModel):
    facts: list[FactDetailResponse]
    requested_field_names: list[str]
    source_document_ids: list[str]
    conflict_field_names: list[str]


PolicyRuleStatusValue = Literal["draft", "published", "retired"]


class CreatePolicyRuleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rule_id: str = Field(min_length=1)
    ruleset_version: str = Field(min_length=1, max_length=100)
    jurisdiction: str = Field(min_length=1, max_length=32)
    effective_from: date
    effective_to: date | None = None
    required_fact_fields: list[str] = Field(default_factory=list)
    condition: dict
    result: dict = Field(default_factory=dict)
    source_clause_ids: list[str] = Field(min_length=1)


class PolicyRuleOut(BaseModel):
    workspace_id: str
    rule_id: str
    ruleset_version: str
    jurisdiction: str
    effective_from: date
    effective_to: date | None
    status: PolicyRuleStatusValue
    required_fact_fields: list[str]
    condition: dict
    result: dict
    source_clause_ids: list[str]


class PolicyRuleListResponse(BaseModel):
    rules: list[PolicyRuleOut]


class EvaluatePolicyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ruleset_version: str = Field(min_length=1)


class PolicyEvaluationOut(BaseModel):
    rule_id: str
    ruleset_version: str
    status: Literal["triggered", "not_triggered", "missing_facts"]
    missing_fact_fields: list[str]
    consumed_fact_versions: dict[str, int]
    result: dict
    source_clause_ids: list[str]


class PolicyEvaluationReportOut(BaseModel):
    ruleset_version: str
    jurisdiction: str
    assessment_date: date
    evaluations: list[PolicyEvaluationOut]
    missing_fact_fields: list[str]


AssessmentStatusValue = Literal[
    "draft",
    "review_required",
    "approved",
    "rejected",
    "superseded",
]
RiskLevelValue = Literal["low", "medium", "high", "critical", "unknown"]
FindingTypeValue = Literal[
    "risk",
    "missing_fact",
    "missing_material",
    "evidence_conflict",
    "rule_trigger",
    "recommendation",
]
FindingSeverityValue = Literal["info", "low", "medium", "high", "critical"]
FindingStatusValue = Literal["open", "accepted", "resolved", "dismissed"]
ActionPriorityValue = Literal["low", "medium", "high", "urgent"]
ActionStatusValue = Literal["todo", "in_progress", "done", "cancelled"]


class GenerateAssessmentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ruleset_version: str = Field(min_length=1, max_length=100)
    generated_by_run_id: str | None = Field(default=None, min_length=1)


class ReviewAssessmentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Literal["approved", "rejected"]
    comment: str = Field(default="", max_length=5000)


class AssessmentOut(BaseModel):
    assessment_id: str
    case_id: str
    version: int
    status: AssessmentStatusValue
    assessment_date: date
    jurisdiction: str
    ruleset_version: str
    fact_versions: dict[str, int]
    policy_evaluations: list[PolicyEvaluationOut]
    risk_level: RiskLevelValue
    candidate_paths: list[str]
    generated_by_run_id: str | None
    approved_by: str | None
    approved_at: float | None
    review_comment: str
    created_at: float
    updated_at: float


class FindingOut(BaseModel):
    finding_id: str
    assessment_id: str
    finding_type: FindingTypeValue
    severity: FindingSeverityValue
    title: str
    description: str
    fact_ids: list[str]
    evidence_ids: list[str]
    clause_ids: list[str]
    rule_ids: list[str]
    status: FindingStatusValue


class AssessmentEvidenceCitationOut(BaseModel):
    citation_id: str
    source_evidence_id: str
    fact_id: str
    fact_version: int
    document_id: str
    document_version_id: str
    page_number: int
    quote: str
    start_offset: int | None
    end_offset: int | None
    source_sha256: str
    created_at: float


class ActionItemOut(BaseModel):
    action_id: str
    assessment_id: str
    title: str
    description: str
    priority: ActionPriorityValue
    owner_id: str | None
    due_at: float | None
    status: ActionStatusValue
    related_finding_ids: list[str]


class AssessmentBundleResponse(BaseModel):
    assessment: AssessmentOut
    findings: list[FindingOut]
    action_items: list[ActionItemOut]
    evidence_citations: list[AssessmentEvidenceCitationOut]


class AssessmentListResponse(BaseModel):
    assessments: list[AssessmentOut]


AgentRunStatusValue = Literal[
    "queued",
    "running",
    "waiting_for_user",
    "waiting_for_review",
    "retrying",
    "completed",
    "failed",
    "cancelled",
]
RunEventTypeValue = Literal[
    "run_started",
    "stage_started",
    "stage_progress",
    "stage_completed",
    "tool_started",
    "tool_completed",
    "evidence_found",
    "facts_proposed",
    "fact_confirmation_required",
    "conflict_detected",
    "human_input_required",
    "human_review_required",
    "artifact_ready",
    "run_paused",
    "run_resumed",
    "run_retrying",
    "run_failed",
    "run_completed",
    "run_cancelled",
]


class StartAssessmentRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ruleset_version: str = Field(min_length=1, max_length=100)
    model_config_snapshot: dict[str, object] = Field(default_factory=dict)


class ReviewAssessmentRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Literal["approved", "rejected"]
    comment: str = Field(default="", max_length=5000)


class AgentRunOut(BaseModel):
    run_id: str
    workspace_id: str
    case_id: str
    workflow_type: Literal["case_assessment", "deep_research"]
    status: AgentRunStatusValue
    current_stage: str
    checkpoint_id: str | None
    token_usage: int
    cost: float
    retry_count: int
    revision: int
    created_by: str
    error_code: str | None
    error_message: str | None
    created_at: float
    updated_at: float
    started_at: float | None
    completed_at: float | None


class AgentRunListResponse(BaseModel):
    runs: list[AgentRunOut]


class RunEventOut(BaseModel):
    event_id: str
    run_id: str
    sequence: int
    event_type: RunEventTypeValue
    stage: str | None
    payload: dict[str, object]
    created_at: float


class RunEventListResponse(BaseModel):
    events: list[RunEventOut]


class EvidencePlanOut(BaseModel):
    investigation_questions: list[str]
    required_fact_fields: list[str]
    planned_tools: list[str]
    evidence_gaps: list[str]
    completion_criteria: list[str]


QACorpusValue = Literal["regulatory", "workspace", "case", "assessment"]
EvidenceQAStatusValue = Literal["answered", "partially_answered", "refused"]


class EvidenceQARequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1, max_length=2000)
    corpora: list[QACorpusValue] = Field(min_length=1)
    workspace_id: str | None = Field(default=None, min_length=1)
    case_id: str | None = Field(default=None, min_length=1)
    assessment_id: str | None = Field(default=None, min_length=1)
    top_k: int = Field(default=5, ge=1, le=20)


class EvidenceQAScopeOut(BaseModel):
    corpora: list[QACorpusValue]
    workspace_id: str | None
    case_id: str | None
    assessment_id: str | None


class EvidenceQACitationOut(BaseModel):
    citation_id: str
    corpus: QACorpusValue
    source_id: str
    source_name: str
    title: str
    quote: str
    source_url: str | None
    workspace_id: str | None
    case_id: str | None
    document_id: str | None
    document_version_id: str | None
    page_number: int | None
    source_sha256: str | None
    assessment_id: str | None
    clause_id: str | None
    score: float


class EvidenceQAClaimOut(BaseModel):
    claim_id: str
    text: str
    citation_ids: list[str]


class ClaimCitationVerificationOut(BaseModel):
    claim_count: int
    cited_claim_count: int
    coverage: float
    uncited_claim_ids: list[str]
    unknown_citation_ids: list[str]
    unused_citation_ids: list[str]
    valid: bool
    method: Literal["structural_v1"]


class ClaimSupportJudgementOut(BaseModel):
    claim_id: str
    supported: bool
    citation_ids: list[str]
    reason: str


class ClaimSupportResultOut(BaseModel):
    judgements: list[ClaimSupportJudgementOut]
    unsupported_claim_ids: list[str]
    valid: bool
    method: Literal["independent_llm_v1"]


class ClaimRepairReportOut(BaseModel):
    status: Literal["not_needed", "repaired", "failed"]
    original_claim_count: int
    kept_claim_ids: list[str]
    removed_claim_ids: list[str]
    removal_reasons: dict[
        str,
        list[
            Literal[
                "uncited",
                "unknown_citation",
                "unsupported",
                "verification_error",
            ]
        ],
    ]
    method: Literal["bounded_filter_v1"]


class EvidenceQAResponse(BaseModel):
    question: str
    scope: EvidenceQAScopeOut
    status: EvidenceQAStatusValue
    answer: str
    claims: list[EvidenceQAClaimOut]
    citations: list[EvidenceQACitationOut]
    refusal_reason: str
    unanswered_aspects: list[str]
    verification: ClaimCitationVerificationOut
    support_verification: ClaimSupportResultOut
    repair_report: ClaimRepairReportOut

"""V2 案件事实创建、修订、确认与证据核验用例。"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

from domain.errors import CaseFactNotFound, InvalidDocumentContent
from domain.facts import (
    CaseFact,
    CaseFactEvidence,
    CaseFactSource,
    CaseFactStatus,
    FactCriticality,
)
from domain.workspaces import WorkspaceRole

if TYPE_CHECKING:
    from app.use_cases.case_management import CaseManagementUseCase
    from app.use_cases.workspace_management import WorkspaceManagementUseCase
    from domain.ports import CaseFactRepoPort, DocumentRepoPort

_WRITE_ROLES: set[WorkspaceRole] = {"editor", "reviewer", "admin"}
_REVIEW_ROLES: set[WorkspaceRole] = {"reviewer", "admin"}


@dataclass(frozen=True)
class FactEvidenceInput:
    document_id: str
    document_version_id: str
    page_number: int
    quote: str
    start_offset: int | None = None
    end_offset: int | None = None
    confidence: float = 0.0


@dataclass(frozen=True)
class FactDetail:
    fact: CaseFact
    evidence: list[CaseFactEvidence]


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


class FactManagementUseCase:
    def __init__(
        self,
        *,
        fact_repo: CaseFactRepoPort,
        document_repo: DocumentRepoPort,
        case_management: CaseManagementUseCase,
        workspace_management: WorkspaceManagementUseCase,
    ) -> None:
        self._facts = fact_repo
        self._documents = document_repo
        self._case_management = case_management
        self._workspace_management = workspace_management

    def create_fact(
        self,
        actor_id: str,
        *,
        case_id: str,
        field_name: str,
        value: bool | int | float | str | list[Any] | dict[str, Any] | None,
        source_type: CaseFactSource,
        confidence: float,
        criticality: FactCriticality = "normal",
        evidence: list[FactEvidenceInput] | None = None,
    ) -> FactDetail:
        case = self._case_management.get_case(case_id, actor_id)
        self._workspace_management.require_role(
            case.workspace_id,
            actor_id,
            _WRITE_ROLES,
            action="创建案件事实",
        )
        now = time.time()
        fact = CaseFact(
            fact_id=_new_id("fact"),
            case_id=case.case_id,
            field_name=field_name,
            value=value,
            source_type=source_type,
            confidence=confidence,
            criticality=criticality,
            created_by=actor_id,
            created_at=now,
            updated_at=now,
        )
        evidence_models = self._build_evidence(fact, evidence or [], created_at=now)
        self._require_source_evidence(fact, evidence_models)
        self._facts.create(fact, evidence_models)
        return FactDetail(fact=fact, evidence=evidence_models)

    def revise_fact(
        self,
        fact_id: str,
        actor_id: str,
        *,
        value: bool | int | float | str | list[Any] | dict[str, Any] | None,
        source_type: CaseFactSource,
        confidence: float,
        evidence: list[FactEvidenceInput] | None = None,
    ) -> FactDetail:
        current, workspace_id = self._get_authorized_fact(
            fact_id,
            actor_id,
            roles=_WRITE_ROLES,
            action="修订案件事实",
        )
        _ = workspace_id
        revised = current.propose_revision(
            value=value,
            source_type=source_type,
            confidence=confidence,
            actor_id=actor_id,
        )
        evidence_models = self._build_evidence(
            revised,
            evidence or [],
            created_at=revised.updated_at,
        )
        self._require_source_evidence(revised, evidence_models)
        self._facts.save_revision(revised, evidence_models)
        return FactDetail(fact=revised, evidence=evidence_models)

    def transition_fact(
        self,
        fact_id: str,
        actor_id: str,
        target: CaseFactStatus,
    ) -> CaseFact:
        current = self._facts.get(fact_id)
        if current is None:
            raise CaseFactNotFound(fact_id)
        case = self._case_management.get_case(current.case_id, actor_id)
        allowed_roles = (
            _REVIEW_ROLES
            if target == "confirmed" and current.criticality == "critical"
            else _WRITE_ROLES
        )
        self._workspace_management.require_role(
            case.workspace_id,
            actor_id,
            allowed_roles,
            action=f"将案件事实状态更新为 {target}",
        )
        updated = current.transition_to(target, actor_id=actor_id)
        if updated is not current:
            self._facts.update_status(updated)
        return cast("CaseFact", updated)

    def list_facts(
        self,
        case_id: str,
        actor_id: str,
        *,
        statuses: set[str] | None = None,
    ) -> list[CaseFact]:
        self._case_management.get_case(case_id, actor_id)
        return cast(
            "list[CaseFact]",
            self._facts.list_for_case(case_id, statuses=statuses),
        )

    def get_detail(self, fact_id: str, actor_id: str) -> FactDetail:
        fact = self._facts.get(fact_id)
        if fact is None:
            raise CaseFactNotFound(fact_id)
        self._case_management.get_case(fact.case_id, actor_id)
        return FactDetail(
            fact=fact,
            evidence=self._facts.list_evidence(
                fact.fact_id,
                fact_version=fact.version,
            ),
        )

    def _get_authorized_fact(
        self,
        fact_id: str,
        actor_id: str,
        *,
        roles: set[WorkspaceRole],
        action: str,
    ) -> tuple[CaseFact, str]:
        fact = self._facts.get(fact_id)
        if fact is None:
            raise CaseFactNotFound(fact_id)
        case = self._case_management.get_case(fact.case_id, actor_id)
        self._workspace_management.require_role(
            case.workspace_id,
            actor_id,
            roles,
            action=action,
        )
        return fact, case.workspace_id

    def _build_evidence(
        self,
        fact: CaseFact,
        inputs: list[FactEvidenceInput],
        *,
        created_at: float,
    ) -> list[CaseFactEvidence]:
        evidence: list[CaseFactEvidence] = []
        for item in inputs:
            self._validate_evidence_source(fact.case_id, item)
            evidence.append(
                CaseFactEvidence(
                    evidence_id=_new_id("evidence"),
                    case_id=fact.case_id,
                    fact_id=fact.fact_id,
                    fact_version=fact.version,
                    document_id=item.document_id,
                    document_version_id=item.document_version_id,
                    page_number=item.page_number,
                    quote=item.quote,
                    start_offset=item.start_offset,
                    end_offset=item.end_offset,
                    confidence=item.confidence,
                    created_at=created_at,
                )
            )
        return evidence

    def _validate_evidence_source(
        self,
        case_id: str,
        item: FactEvidenceInput,
    ) -> None:
        binding = self._documents.get_binding(case_id, item.document_id)
        if binding is None:
            raise InvalidDocumentContent("证据文档未绑定当前案件")
        version = self._documents.get_version(item.document_version_id)
        if version is None or version.document_id != item.document_id:
            raise InvalidDocumentContent("证据版本不属于当前文档")
        snapshot = self._documents.get_parse_snapshot(item.document_version_id)
        if snapshot is None:
            raise InvalidDocumentContent("证据版本尚未完成解析")
        if item.page_number > snapshot.page_count:
            raise InvalidDocumentContent("证据页码超出解析快照范围")
        page = snapshot.pages[item.page_number - 1]
        page_parts = [page.text]
        page_parts.extend(table.markdown for table in page.tables)
        page_body = "\n\n".join(page_parts)
        if item.start_offset is not None and item.end_offset is not None:
            if page_body[item.start_offset : item.end_offset] != item.quote:
                raise InvalidDocumentContent("证据 offset 与原文不一致")
        elif item.quote not in page_body:
            raise InvalidDocumentContent("证据 quote 未在对应原文页中找到")

    @staticmethod
    def _require_source_evidence(
        fact: CaseFact,
        evidence: list[CaseFactEvidence],
    ) -> None:
        if fact.source_type == "document" and not evidence:
            raise InvalidDocumentContent("document 来源的事实必须包含原文证据")

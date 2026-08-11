"""V3 Evidence QA 普通应用服务。"""

from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from typing import TYPE_CHECKING, cast

from domain.qa import (
    ClaimCitationVerifier,
    ClaimRemovalReason,
    ClaimRepairReport,
    ClaimSupportJudgement,
    ClaimSupportResult,
    EvidenceQAAnswer,
    EvidenceQACitation,
    EvidenceQAClaim,
    EvidenceQADraft,
    EvidenceQAScope,
    QACorpus,
)

if TYPE_CHECKING:
    from app.use_cases.assessment_management import AssessmentManagementUseCase
    from app.use_cases.case_management import CaseManagementUseCase
    from app.use_cases.workspace_management import WorkspaceManagementUseCase
    from domain.assessments import AssessmentBundle
    from domain.evidence import EvidenceSearchHit
    from domain.models import Chunk
    from domain.ports import (
        ClaimSupportVerifierPort,
        DocumentRepoPort,
        EmbedPort,
        EvidenceIndexPort,
        EvidenceQAGeneratorPort,
        RetrievePort,
    )

_REFUSAL_NO_EVIDENCE = "根据当前检索范围未找到足够证据，暂时无法回答该问题。"
_REFUSAL_UNSUPPORTED = "候选回答未通过 Claim-Citation 支持校验，已拒绝输出。"
_REFUSAL_GENERATION = "证据问答生成或校验失败，已安全拒答。"


class EvidenceQAUseCase:
    """线性 Evidence QA：鉴权 → 范围检索 → Claim 生成 → 两阶段校验。"""

    def __init__(
        self,
        *,
        retriever: RetrievePort,
        evidence_index: EvidenceIndexPort,
        document_repo: DocumentRepoPort,
        embedder: EmbedPort,
        generator: EvidenceQAGeneratorPort,
        support_verifier: ClaimSupportVerifierPort,
        workspace_management: WorkspaceManagementUseCase,
        case_management: CaseManagementUseCase,
        assessment_management: AssessmentManagementUseCase,
    ) -> None:
        self._retriever = retriever
        self._evidence_index = evidence_index
        self._documents = document_repo
        self._embedder = embedder
        self._generator = generator
        self._support_verifier = support_verifier
        self._workspace_management = workspace_management
        self._case_management = case_management
        self._assessment_management = assessment_management

    def answer(
        self,
        actor_id: str,
        *,
        question: str,
        corpora: list[QACorpus],
        workspace_id: str | None = None,
        case_id: str | None = None,
        assessment_id: str | None = None,
        top_k: int = 5,
    ) -> EvidenceQAAnswer:
        if not question.strip():
            raise ValueError("question 不能为空")
        if len(question) > 2000:
            raise ValueError("question 不能超过 2000 个字符")
        if top_k < 1 or top_k > 20:
            raise ValueError("top_k 必须在 1 到 20 之间")
        scope = self._authorize_scope(
            actor_id,
            corpora=corpora,
            workspace_id=workspace_id,
            case_id=case_id,
            assessment_id=assessment_id,
        )
        citations = self._retrieve(
            actor_id,
            question=question,
            scope=scope,
            top_k=top_k,
        )
        citations = self._verify_source_citations(citations, scope=scope)
        if not citations:
            return _refusal(question, scope, _REFUSAL_NO_EVIDENCE)
        try:
            draft = self._generator.generate(
                question=question,
                citations=citations,
            )
            if draft.status == "refused":
                return _refusal(
                    question,
                    scope,
                    draft.refusal_reason,
                )
            return self._validate_and_repair(
                question=question,
                scope=scope,
                draft=draft,
                citations=citations,
            )
        except (RuntimeError, ValueError):
            return _refusal(question, scope, _REFUSAL_GENERATION)

    def _validate_and_repair(
        self,
        *,
        question: str,
        scope: EvidenceQAScope,
        draft: EvidenceQADraft,
        citations: list[EvidenceQACitation],
    ) -> EvidenceQAAnswer:
        known_citation_ids = {citation.citation_id for citation in citations}
        removal_reasons: dict[str, list[ClaimRemovalReason]] = {}
        structurally_valid_claims: list[EvidenceQAClaim] = []
        for claim in draft.claims:
            reasons: list[ClaimRemovalReason] = []
            if not claim.citation_ids:
                reasons.append("uncited")
            if set(claim.citation_ids) - known_citation_ids:
                reasons.append("unknown_citation")
            if reasons:
                removal_reasons[claim.claim_id] = reasons
            else:
                structurally_valid_claims.append(claim)

        if not structurally_valid_claims:
            return _refusal(
                question,
                scope,
                _REFUSAL_UNSUPPORTED,
                repair_report=_repair_report(draft.claims, [], removal_reasons),
            )

        candidate_citations = _citations_for_claims(
            structurally_valid_claims,
            citations,
        )
        try:
            support = self._support_verifier.verify(
                structurally_valid_claims,
                candidate_citations,
            )
        except (RuntimeError, ValueError):
            for claim in structurally_valid_claims:
                removal_reasons[claim.claim_id] = ["verification_error"]
            return _refusal(
                question,
                scope,
                _REFUSAL_GENERATION,
                repair_report=_repair_report(draft.claims, [], removal_reasons),
            )
        if not _support_contract_valid(
            structurally_valid_claims,
            candidate_citations,
            support,
        ):
            for claim in structurally_valid_claims:
                removal_reasons[claim.claim_id] = ["verification_error"]
            return _refusal(
                question,
                scope,
                _REFUSAL_GENERATION,
                repair_report=_repair_report(draft.claims, [], removal_reasons),
            )

        judgements_by_id = {judgement.claim_id: judgement for judgement in support.judgements}
        kept_claims: list[EvidenceQAClaim] = []
        kept_judgements: list[ClaimSupportJudgement] = []
        for claim in structurally_valid_claims:
            judgement = judgements_by_id[claim.claim_id]
            if not judgement.supported:
                removal_reasons[claim.claim_id] = ["unsupported"]
                continue
            kept_claims.append(claim)
            kept_judgements.append(judgement)

        if not kept_claims:
            return _refusal(
                question,
                scope,
                _REFUSAL_UNSUPPORTED,
                repair_report=_repair_report(draft.claims, [], removal_reasons),
            )

        repair_report = _repair_report(
            draft.claims,
            kept_claims,
            removal_reasons,
        )
        repaired = repair_report.status == "repaired"
        unanswered_aspects = list(draft.unanswered_aspects)
        if repaired:
            unanswered_aspects.append(
                "部分候选结论未通过引用完整性或语义支持校验，已安全移除："
                + "、".join(repair_report.removed_claim_ids)
            )
        final_citations = _citations_for_claims(kept_claims, candidate_citations)
        final_support = ClaimSupportResult(
            judgements=kept_judgements,
            unsupported_claim_ids=[],
            valid=True,
        )
        return EvidenceQAAnswer(
            question=question,
            scope=scope,
            status="partially_answered" if repaired else draft.status,
            claims=kept_claims,
            citations=final_citations,
            unanswered_aspects=unanswered_aspects,
            verification=ClaimCitationVerifier.verify(
                kept_claims,
                final_citations,
            ),
            support_verification=final_support,
            repair_report=repair_report,
        )

    def _authorize_scope(
        self,
        actor_id: str,
        *,
        corpora: list[QACorpus],
        workspace_id: str | None,
        case_id: str | None,
        assessment_id: str | None,
    ) -> EvidenceQAScope:
        requested = list(corpora)
        if len(requested) != len(set(requested)):
            raise ValueError("corpora 不能重复")
        resolved_workspace_id = workspace_id
        resolved_case_id = case_id
        resolved_assessment_id = assessment_id

        case = None
        if any(corpus in {"case", "assessment"} for corpus in requested):
            if not case_id:
                raise ValueError("Case 或 Assessment 范围必须提供 case_id")
            case = self._case_management.get_case(case_id, actor_id)
            resolved_case_id = case.case_id
            resolved_workspace_id = case.workspace_id
            if workspace_id is not None and workspace_id != case.workspace_id:
                raise ValueError("workspace_id 与 Case 归属不一致")
        elif "workspace" in requested:
            if not workspace_id:
                raise ValueError("Workspace 范围必须提供 workspace_id")
            workspace = self._workspace_management.get_workspace(workspace_id, actor_id)
            resolved_workspace_id = workspace.workspace_id

        if "assessment" in requested:
            if not assessment_id:
                raise ValueError("Assessment 范围必须提供 assessment_id")
            bundle = self._assessment_management.get(assessment_id, actor_id)
            if case is None or bundle.assessment.case_id != case.case_id:
                raise ValueError("Assessment 不属于当前 Case")
            resolved_assessment_id = bundle.assessment.assessment_id

        return EvidenceQAScope(
            corpora=requested,
            workspace_id=resolved_workspace_id,
            case_id=resolved_case_id,
            assessment_id=resolved_assessment_id,
        )

    def _retrieve(
        self,
        actor_id: str,
        *,
        question: str,
        scope: EvidenceQAScope,
        top_k: int,
    ) -> list[EvidenceQACitation]:
        futures: list[tuple[QACorpus, Future[object]]] = []
        immediate_results: list[tuple[QACorpus, object]] = []
        query_embedding: list[float] | None = None
        if any(corpus in {"workspace", "case"} for corpus in scope.corpora):
            query_embedding = self._embedder.embed([question])[0]
        with ThreadPoolExecutor(max_workers=min(len(scope.corpora), 4)) as executor:
            for corpus in scope.corpora:
                if corpus == "regulatory":
                    futures.append(
                        (
                            corpus,
                            executor.submit(
                                self._retriever.retrieve,
                                question,
                                top_k,
                                "law",
                                None,
                                None,
                            ),
                        )
                    )
                elif corpus == "workspace":
                    assert scope.workspace_id is not None
                    assert query_embedding is not None
                    futures.append(
                        (
                            corpus,
                            executor.submit(
                                self._evidence_index.search_workspace,
                                workspace_id=scope.workspace_id,
                                query=question,
                                query_embedding=query_embedding,
                                top_k=top_k,
                            ),
                        )
                    )
                elif corpus == "case":
                    assert scope.workspace_id is not None
                    assert scope.case_id is not None
                    assert query_embedding is not None
                    futures.append(
                        (
                            corpus,
                            executor.submit(
                                self._evidence_index.search,
                                workspace_id=scope.workspace_id,
                                case_id=scope.case_id,
                                query=question,
                                query_embedding=query_embedding,
                                top_k=top_k,
                            ),
                        )
                    )
                else:
                    assert scope.assessment_id is not None
                    immediate_results.append(
                        (
                            corpus,
                            self._assessment_management.get(
                                scope.assessment_id,
                                actor_id,
                            ),
                        )
                    )
            raw_results = [(corpus, future.result()) for corpus, future in futures]
        raw_results.extend(immediate_results)
        return _normalize_citations(raw_results, scope=scope, per_corpus_limit=top_k)

    def _verify_source_citations(
        self,
        citations: list[EvidenceQACitation],
        *,
        scope: EvidenceQAScope,
    ) -> list[EvidenceQACitation]:
        verified: list[EvidenceQACitation] = []
        for citation in citations:
            if citation.corpus not in {"workspace", "case"}:
                verified.append(citation)
                continue
            if citation.workspace_id != scope.workspace_id:
                continue
            if citation.corpus == "case" and citation.case_id != scope.case_id:
                continue
            if citation.document_version_id is None or citation.page_number is None:
                continue
            version = self._documents.get_version(citation.document_version_id)
            document = (
                None if citation.document_id is None else self._documents.get(citation.document_id)
            )
            snapshot = self._documents.get_parse_snapshot(citation.document_version_id)
            if (
                version is None
                or document is None
                or snapshot is None
                or version.document_id != document.document_id
                or document.workspace_id != citation.workspace_id
                or document.status != "ready"
                or document.current_version_id != version.version_id
                or version.sha256 != citation.source_sha256
                or citation.page_number > snapshot.page_count
            ):
                continue
            if citation.corpus == "workspace" and document.document_type != "workspace_knowledge":
                continue
            if citation.corpus == "case":
                if citation.case_id is None:
                    continue
                binding = self._documents.get_binding(
                    citation.case_id,
                    document.document_id,
                )
                if binding is None:
                    continue
            page = snapshot.pages[citation.page_number - 1]
            page_parts = [page.text]
            page_parts.extend(table.markdown for table in page.tables)
            page_text = "\n\n".join(part for part in page_parts if part)
            if citation.quote not in page_text:
                continue
            verified.append(
                cast(
                    "EvidenceQACitation",
                    citation.model_copy(update={"source_name": document.logical_name}),
                )
            )
        return verified


def _normalize_citations(
    raw_results: list[tuple[QACorpus, object]],
    *,
    scope: EvidenceQAScope,
    per_corpus_limit: int,
) -> list[EvidenceQACitation]:
    candidates: list[tuple[float, str, dict[str, object]]] = []
    for corpus, result in raw_results:
        if corpus == "regulatory":
            for chunk in cast("list[Chunk]", result)[:per_corpus_limit]:
                candidates.append(
                    (
                        chunk.score,
                        f"regulatory:{chunk.chunk_id}",
                        {
                            "corpus": "regulatory",
                            "source_id": chunk.chunk_id,
                            "source_name": chunk.source_name,
                            "title": chunk.title,
                            "quote": chunk.text[:4000],
                            "source_url": chunk.source_url,
                            "clause_id": str(chunk.metadata.get("clause_id") or "") or None,
                            "score": max(0.0, chunk.score),
                        },
                    )
                )
        elif corpus in {"workspace", "case"}:
            for hit in cast("list[EvidenceSearchHit]", result)[:per_corpus_limit]:
                chunk = hit.chunk
                candidates.append(
                    (
                        hit.score,
                        (
                            f"{corpus}:{chunk.document_version_id}:"
                            f"{chunk.page_number}:{chunk.chunk_index}"
                        ),
                        {
                            "corpus": corpus,
                            "source_id": chunk.chunk_id,
                            "source_name": chunk.document_id,
                            "title": f"第 {chunk.page_number} 页",
                            "quote": chunk.text[:4000],
                            "workspace_id": chunk.workspace_id,
                            "case_id": chunk.case_id if corpus == "case" else None,
                            "document_id": chunk.document_id,
                            "document_version_id": chunk.document_version_id,
                            "page_number": chunk.page_number,
                            "source_sha256": chunk.source_sha256,
                            "score": hit.score,
                        },
                    )
                )
        else:
            bundle = cast("AssessmentBundle", result)
            candidates.extend(_assessment_candidates(bundle, scope))

    deduplicated: dict[str, tuple[float, dict[str, object]]] = {}
    for score, key, values in candidates:
        current = deduplicated.get(key)
        if current is None or score > current[0]:
            deduplicated[key] = (score, values)
    ordered = sorted(
        deduplicated.values(),
        key=lambda item: item[0],
        reverse=True,
    )
    citations: list[EvidenceQACitation] = []
    for index, (_, values) in enumerate(ordered, start=1):
        citations.append(
            EvidenceQACitation(
                citation_id=f"E{index}",
                **values,
            )
        )
    return citations


def _assessment_candidates(
    bundle: AssessmentBundle,
    scope: EvidenceQAScope,
) -> list[tuple[float, str, dict[str, object]]]:
    assessment = bundle.assessment
    source_name = f"Assessment v{assessment.version}"
    candidates: list[tuple[float, str, dict[str, object]]] = []
    for finding in bundle.findings:
        quote = finding.title
        if finding.description:
            quote += f"：{finding.description}"
        candidates.append(
            (
                float(_severity_score(finding.severity)),
                f"assessment:finding:{finding.finding_id}",
                {
                    "corpus": "assessment",
                    "source_id": finding.finding_id,
                    "source_name": source_name,
                    "title": finding.title,
                    "quote": quote[:4000],
                    "workspace_id": scope.workspace_id,
                    "case_id": assessment.case_id,
                    "assessment_id": assessment.assessment_id,
                    "clause_id": finding.clause_ids[0] if finding.clause_ids else None,
                    "score": float(_severity_score(finding.severity)),
                },
            )
        )
    for action in bundle.action_items:
        candidates.append(
            (
                0.5,
                f"assessment:action:{action.action_id}",
                {
                    "corpus": "assessment",
                    "source_id": action.action_id,
                    "source_name": source_name,
                    "title": action.title,
                    "quote": (action.description or action.title)[:4000],
                    "workspace_id": scope.workspace_id,
                    "case_id": assessment.case_id,
                    "assessment_id": assessment.assessment_id,
                    "score": 0.5,
                },
            )
        )
    return candidates


def _severity_score(severity: str) -> float:
    return {
        "info": 0.2,
        "low": 0.3,
        "medium": 0.5,
        "high": 0.8,
        "critical": 1.0,
    }.get(severity, 0.0)


def _citations_for_claims(
    claims: list[EvidenceQAClaim],
    citations: list[EvidenceQACitation],
) -> list[EvidenceQACitation]:
    used_ids = {citation_id for claim in claims for citation_id in claim.citation_ids}
    return [citation for citation in citations if citation.citation_id in used_ids]


def _support_contract_valid(
    claims: list[EvidenceQAClaim],
    citations: list[EvidenceQACitation],
    support: ClaimSupportResult,
) -> bool:
    claims_by_id = {claim.claim_id: claim for claim in claims}
    expected_claim_ids = set(claims_by_id)
    actual_claim_ids = {judgement.claim_id for judgement in support.judgements}
    if actual_claim_ids != expected_claim_ids:
        return False
    known_citation_ids = {citation.citation_id for citation in citations}
    for judgement in support.judgements:
        used_ids = set(judgement.citation_ids)
        if not used_ids.issubset(known_citation_ids):
            return False
        if judgement.supported and not used_ids.issubset(
            claims_by_id[judgement.claim_id].citation_ids
        ):
            return False
    return True


def _repair_report(
    original_claims: list[EvidenceQAClaim],
    kept_claims: list[EvidenceQAClaim],
    removal_reasons: dict[str, list[ClaimRemovalReason]],
) -> ClaimRepairReport:
    kept_ids = [claim.claim_id for claim in kept_claims]
    kept_id_set = set(kept_ids)
    removed_ids = [claim.claim_id for claim in original_claims if claim.claim_id not in kept_id_set]
    if not removed_ids:
        status = "not_needed"
    elif kept_ids:
        status = "repaired"
    else:
        status = "failed"
    return ClaimRepairReport(
        status=status,
        original_claim_count=len(original_claims),
        kept_claim_ids=kept_ids,
        removed_claim_ids=removed_ids,
        removal_reasons={claim_id: removal_reasons[claim_id] for claim_id in removed_ids},
    )


def _refusal(
    question: str,
    scope: EvidenceQAScope,
    reason: str,
    *,
    repair_report: ClaimRepairReport | None = None,
) -> EvidenceQAAnswer:
    return EvidenceQAAnswer(
        question=question,
        scope=scope,
        status="refused",
        refusal_reason=reason,
        verification=ClaimCitationVerifier.verify([], []),
        support_verification=ClaimSupportResult(
            judgements=[],
            unsupported_claim_ids=[],
            valid=True,
        ),
        repair_report=repair_report
        or ClaimRepairReport(
            status="not_needed",
            original_claim_count=0,
        ),
    )

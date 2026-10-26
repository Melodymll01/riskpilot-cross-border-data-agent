"""Evidence QA 普通应用服务。"""

from __future__ import annotations

from contextlib import nullcontext
from typing import TYPE_CHECKING

from domain.qa import (
    ClaimCitationVerifier,
    ClaimRemovalReason,
    ClaimRepairReport,
    ClaimRepairStatus,
    ClaimSupportJudgement,
    ClaimSupportResult,
    EvidenceQAAnswer,
    EvidenceQACitation,
    EvidenceQAClaim,
    EvidenceQADraft,
    EvidenceQAPresentation,
    EvidenceQAScope,
    QACorpus,
    UserContextMessage,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from domain.ports import (
        ClaimSupportVerifierPort,
        DocumentRepoPort,
        EmbedPort,
        EvidenceIndexPort,
        EvidenceQAGeneratorPort,
        MetricsPort,
        RetrievePort,
        RunControlPort,
        TracePort,
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
        trace: TracePort | None = None,
        metrics: MetricsPort | None = None,
    ) -> None:
        self._retriever = retriever
        self._evidence_index = evidence_index
        self._documents = document_repo
        self._embedder = embedder
        self._generator = generator
        self._support_verifier = support_verifier
        self._trace = trace
        self._metrics = metrics

    def verify_sources(
        self,
        citations: list[EvidenceQACitation],
        scope: EvidenceQAScope,
    ) -> list[EvidenceQACitation]:
        return self._verify_source_citations(citations, scope=scope)

    def verify_draft(
        self,
        *,
        question: str,
        scope: EvidenceQAScope,
        citations: list[EvidenceQACitation],
        draft: EvidenceQADraft,
        user_context: list[UserContextMessage],
        runtime_control: RunControlPort,
    ) -> EvidenceQAAnswer:
        runtime_control.check()
        sources = self.verify_sources(citations, scope)
        if not sources or not draft.claims:
            return _refusal(question, scope, _REFUSAL_NO_EVIDENCE)
        return self._validate_and_repair(
            question=question,
            scope=scope,
            citations=sources,
            draft=draft,
            user_context=user_context,
            runtime_control=runtime_control,
            on_stage=None,
        )

    def answer(
        self,
        actor_id: str,
        *,
        question: str,
        corpora: list[QACorpus],
        top_k: int = 5,
        document_ids: list[str] | None = None,
    ) -> EvidenceQAAnswer:
        if not question.strip():
            raise ValueError("question 不能为空")
        if len(question) > 2000:
            raise ValueError("question 不能超过 2000 个字符")
        if top_k < 1 or top_k > 20:
            raise ValueError("top_k 必须在 1 到 20 之间")
        if not actor_id:
            raise ValueError("actor_id 必填")
        scope = EvidenceQAScope(corpora=corpora, owner_id=actor_id, document_ids=document_ids)
        if document_ids is not None:
            for document_id in document_ids:
                document = self._documents.get(document_id)
                if document is None or document.owner_id != actor_id or document.status != "ready":
                    raise ValueError("附件不可用或不属于当前用户")
        citations = self._retrieve(
            actor_id,
            question=question,
            scope=scope,
            top_k=top_k,
        )
        return self.answer_from_citations(
            question=question,
            scope=scope,
            citations=citations,
            user_context=[],
        )

    def answer_from_citations(
        self,
        *,
        question: str,
        scope: EvidenceQAScope,
        citations: list[EvidenceQACitation],
        run_id: str | None = None,
        user_context: list[UserContextMessage] | None = None,
        preferences: list[str] | None = None,
        runtime_control: RunControlPort | None = None,
        on_stage: Callable[[str], None] | None = None,
    ) -> EvidenceQAAnswer:
        """Generate and verify an answer from server-collected evidence only."""

        trace_context = (
            self._trace.span(
                "riskpilot.citation.verify",
                metadata={
                    "run_id": run_id or "",
                    "owner_id": scope.owner_id,
                    "evidence_count": len(citations),
                    "stage": "verification",
                },
            )
            if self._trace is not None
            else nullcontext(None)
        )
        with trace_context as span:
            error_type: str | None = None
            try:
                answer = self._answer_from_citations(
                    question=question,
                    scope=scope,
                    citations=citations,
                    user_context=user_context or [],
                    preferences=preferences or [],
                    runtime_control=runtime_control,
                    on_stage=on_stage,
                )
            except (RuntimeError, ValueError) as exc:
                error_type = type(exc).__name__
                answer = _refusal(question, scope, _REFUSAL_GENERATION)
            if span is not None:
                metadata: dict[str, object] = {
                    "status": "failed" if error_type else "completed",
                    "decision": answer.status,
                    "evidence_count": len(answer.citations),
                    "refused": answer.status == "refused",
                }
                if error_type:
                    metadata["error_type"] = error_type
                span.add_metadata(metadata)
            if answer.status == "refused" and self._metrics is not None:
                self._metrics.record_citation_failure(workflow="conversation")
            return answer

    def _answer_from_citations(
        self,
        *,
        question: str,
        scope: EvidenceQAScope,
        citations: list[EvidenceQACitation],
        user_context: list[UserContextMessage],
        preferences: list[str],
        runtime_control: RunControlPort | None,
        on_stage: Callable[[str], None] | None,
    ) -> EvidenceQAAnswer:
        if runtime_control is not None:
            runtime_control.check(stage="verification")
        citations = self._verify_source_citations(citations, scope=scope)
        if runtime_control is not None:
            runtime_control.check(stage="verification")
        if not citations:
            return _refusal(question, scope, _REFUSAL_NO_EVIDENCE)
        if on_stage is not None:
            on_stage("generation")
        generation_trace = (
            self._trace.span(
                "riskpilot.claim.generate",
                run_type="llm",
                metadata={
                    "run_id": runtime_control.run_id if runtime_control is not None else "",
                    "stage": "generation",
                    "evidence_count": len(citations),
                },
            )
            if self._trace is not None
            else nullcontext(None)
        )
        with generation_trace as generation_span:
            try:
                draft = self._generator.generate(
                    question=question,
                    citations=citations,
                    user_context=user_context,
                    preferences=preferences,
                    runtime_control=runtime_control,
                )
            except Exception as exc:
                if generation_span is not None:
                    generation_span.add_metadata(
                        {"status": "failed", "error_type": type(exc).__name__}
                    )
                raise
            else:
                if generation_span is not None:
                    generation_span.add_metadata({"status": "completed", "decision": draft.status})
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
            user_context=user_context,
            runtime_control=runtime_control,
            on_stage=on_stage,
        )

    def _validate_and_repair(
        self,
        *,
        question: str,
        scope: EvidenceQAScope,
        draft: EvidenceQADraft,
        citations: list[EvidenceQACitation],
        user_context: list[UserContextMessage],
        runtime_control: RunControlPort | None,
        on_stage: Callable[[str], None] | None,
    ) -> EvidenceQAAnswer:
        known_citation_ids = {citation.citation_id for citation in citations}
        known_context_ids = {item.message_id for item in user_context}
        removal_reasons: dict[str, list[ClaimRemovalReason]] = {}
        structurally_valid_claims: list[EvidenceQAClaim] = []
        for claim in draft.claims:
            reasons: list[ClaimRemovalReason] = []
            if not claim.citation_ids:
                reasons.append("uncited")
            if set(claim.citation_ids) - known_citation_ids:
                reasons.append("unknown_citation")
            if set(claim.condition_message_ids) - known_context_ids:
                reasons.append("unknown_context")
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
        if on_stage is not None:
            on_stage("verification")
        verification_trace = (
            self._trace.span(
                "riskpilot.claim.verify",
                run_type="llm",
                metadata={
                    "run_id": runtime_control.run_id if runtime_control is not None else "",
                    "stage": "verification",
                    "evidence_count": len(candidate_citations),
                },
            )
            if self._trace is not None
            else nullcontext(None)
        )
        with verification_trace as verification_span:
            try:
                support = self._support_verifier.verify(
                    structurally_valid_claims,
                    candidate_citations,
                    user_context=user_context,
                    runtime_control=runtime_control,
                )
            except Exception as exc:
                if verification_span is not None:
                    verification_span.add_metadata(
                        {"status": "failed", "error_type": type(exc).__name__}
                    )
                raise
            else:
                if verification_span is not None:
                    verification_span.add_metadata(
                        {
                            "status": "completed",
                            "decision": "supported" if support.valid else "filtered",
                        }
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
            presentation=_repair_presentation(
                draft.presentation,
                kept_claims,
            ),
            verification=ClaimCitationVerifier.verify(
                kept_claims,
                final_citations,
            ),
            support_verification=final_support,
            repair_report=repair_report,
        )

    def _retrieve(
        self, actor_id: str, *, question: str, scope: EvidenceQAScope, top_k: int
    ) -> list[EvidenceQACitation]:
        citations: list[EvidenceQACitation] = []
        if "regulatory" in scope.corpora:
            for chunk in self._retriever.retrieve(
                question, top_k=top_k, corpus="law", owner_id=None
            ):
                citations.append(
                    EvidenceQACitation(
                        citation_id=f"E{len(citations) + 1}",
                        corpus="regulatory",
                        source_id=chunk.chunk_id,
                        source_name=chunk.source_name,
                        title=chunk.title,
                        quote=chunk.text[:4000],
                        source_url=chunk.source_url,
                        score=max(0.0, chunk.score),
                    )
                )
        if "user_docs" in scope.corpora:
            hits = self._evidence_index.search(
                owner_id=actor_id,
                query=question,
                query_embedding=self._embedder.embed([question])[0],
                top_k=top_k,
                document_ids=scope.document_ids,
            )
            for hit in hits:
                c = hit.chunk
                if c.owner_id != actor_id:
                    continue
                citations.append(
                    EvidenceQACitation(
                        citation_id=f"E{len(citations) + 1}",
                        corpus="user_docs",
                        source_id=c.chunk_id,
                        source_name=c.document_id,
                        quote=c.text[:4000],
                        document_id=c.document_id,
                        document_version_id=c.document_version_id,
                        page_number=c.page_number,
                        source_sha256=c.source_sha256,
                        score=hit.score,
                    )
                )
        return citations

    def _verify_source_citations(
        self, citations: list[EvidenceQACitation], *, scope: EvidenceQAScope
    ) -> list[EvidenceQACitation]:
        verified: list[EvidenceQACitation] = []
        for citation in citations:
            if citation.corpus in {"regulatory", "web"}:
                verified.append(citation)
                continue
            if citation.corpus == "knowledge":
                if citation.owner_id == scope.owner_id:
                    verified.append(citation)
                continue
            if (
                citation.document_version_id is None
                or citation.document_id is None
                or citation.page_number is None
            ):
                continue
            version = self._documents.get_version(citation.document_version_id)
            document = self._documents.get(citation.document_id)
            snapshot = self._documents.get_parse_snapshot(citation.document_version_id)
            if (
                version is None
                or document is None
                or snapshot is None
                or document.owner_id != scope.owner_id
                or document.status != "ready"
                or version.document_id != document.document_id
                or document.current_version_id != version.version_id
                or version.sha256 != citation.source_sha256
                or snapshot.source_sha256 != version.sha256
                or snapshot.document_version_id != version.version_id
                or citation.page_number > snapshot.page_count
                or (
                    scope.document_ids is not None
                    and document.document_id not in scope.document_ids
                )
            ):
                continue
            page = snapshot.pages[citation.page_number - 1]
            page_text = "\n\n".join([page.text, *(t.markdown for t in page.tables)])
            if citation.quote not in page_text:
                continue
            verified.append(
                citation.model_copy(
                    update={
                        "source_name": document.logical_name,
                        "source_version": str(version.version_number),
                    }
                )
            )
        return verified


def _citations_for_claims(
    claims: list[EvidenceQAClaim],
    citations: list[EvidenceQACitation],
) -> list[EvidenceQACitation]:
    used_ids = {citation_id for claim in claims for citation_id in claim.citation_ids}
    return [citation for citation in citations if citation.citation_id in used_ids]


def _repair_presentation(
    presentation: EvidenceQAPresentation | None,
    kept_claims: list[EvidenceQAClaim],
) -> EvidenceQAPresentation | None:
    """只删除依赖坏 Claim 的展示元素；图表不再完整时安全降级为文本。"""
    if presentation is None or presentation.kind == "text":
        return presentation
    kept_claim_ids = {claim.claim_id for claim in kept_claims}
    payload = presentation.model_dump()
    if presentation.kind == "table":
        table = payload.get("table") or {}
        rows = table.get("rows") or []
        table["rows"] = [
            row for row in rows if set(row.get("claim_ids") or []).issubset(kept_claim_ids)
        ]
    else:
        flowchart = payload.get("flowchart") or {}
        nodes = flowchart.get("nodes") or []
        kept_nodes = [
            node for node in nodes if set(node.get("claim_ids") or []).issubset(kept_claim_ids)
        ]
        kept_node_ids = {node.get("node_id") for node in kept_nodes}
        edges = flowchart.get("edges") or []
        flowchart["nodes"] = kept_nodes
        flowchart["edges"] = [
            edge
            for edge in edges
            if edge.get("source_node_id") in kept_node_ids
            and edge.get("target_node_id") in kept_node_ids
            and set(edge.get("claim_ids") or []).issubset(kept_claim_ids)
        ]
    try:
        repaired = EvidenceQAPresentation.model_validate(payload)
        EvidenceQADraft(status="answered", claims=kept_claims, presentation=repaired)
        return repaired
    except ValueError:
        return EvidenceQAPresentation(kind="text")


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
        status: ClaimRepairStatus = "not_needed"
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

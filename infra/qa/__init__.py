"""V3 Evidence QA 生成与独立 Claim 支持校验适配器。"""

from infra.qa.fact_proposals import StructuredFactProposalGenerator
from infra.qa.structured import (
    StructuredClaimSupportVerifier,
    StructuredEvidenceQAGenerator,
)

__all__ = [
    "StructuredFactProposalGenerator",
    "StructuredClaimSupportVerifier",
    "StructuredEvidenceQAGenerator",
]

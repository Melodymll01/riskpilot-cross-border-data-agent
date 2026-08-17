"""离线测试用 Fake 实现：所有 Fake 都实现 domain.ports 中的对应 Protocol。"""

from tests.fakes.fake_agent_model import FakeToolCallingModel, final_answer_model
from tests.fakes.fake_audit_log import FakeAuditLogRepo
from tests.fakes.fake_auth import FakeAuth, FakeOAuthProvider
from tests.fakes.fake_chat import FakeChat
from tests.fakes.fake_copilot_agent import FakeCopilotAgent
from tests.fakes.fake_document_loader import FakeDocumentLoader
from tests.fakes.fake_document_parser import FakeDocumentParser
from tests.fakes.fake_embed import FakeEmbed
from tests.fakes.fake_evidence_chunker import FakeEvidenceChunker
from tests.fakes.fake_evidence_index import FakeEvidenceIndex
from tests.fakes.fake_fact_proposals import FakeFactProposalGenerator
from tests.fakes.fake_kb_repo import FakeKbRepo
from tests.fakes.fake_object_store import FakeObjectStore
from tests.fakes.fake_qa import FakeClaimSupportVerifier, FakeEvidenceQAGenerator
from tests.fakes.fake_readiness import FakeReadiness
from tests.fakes.fake_repos import (
    InMemoryAgentRunRepo,
    InMemoryAssessmentRepo,
    InMemoryCaseFactRepo,
    InMemoryCaseRepo,
    InMemoryDocumentRepo,
    InMemoryPolicyRuleRepo,
    InMemoryTaskRepo,
    InMemoryUserRepo,
    InMemoryWorkspaceRepo,
)
from tests.fakes.fake_retrieve import FakeRetrieve
from tests.fakes.fake_risk_profile import FakeRiskProfile
from tests.fakes.fake_trace import FakeTrace, FakeTraceSpan
from tests.fakes.fake_visual import FakeVisualEmbedder, FakeVisualIndex
from tests.fakes.fake_websearch import FakeWebSearch

__all__ = [
    "FakeAuditLogRepo",
    "FakeToolCallingModel",
    "FakeAuth",
    "FakeChat",
    "FakeCopilotAgent",
    "FakeDocumentLoader",
    "FakeDocumentParser",
    "FakeEmbed",
    "FakeEvidenceChunker",
    "FakeEvidenceIndex",
    "FakeFactProposalGenerator",
    "FakeKbRepo",
    "FakeObjectStore",
    "FakeClaimSupportVerifier",
    "FakeEvidenceQAGenerator",
    "FakeOAuthProvider",
    "FakeReadiness",
    "FakeRetrieve",
    "FakeRiskProfile",
    "FakeTrace",
    "FakeTraceSpan",
    "FakeWebSearch",
    "FakeVisualEmbedder",
    "FakeVisualIndex",
    "InMemoryTaskRepo",
    "InMemoryUserRepo",
    "InMemoryAgentRunRepo",
    "InMemoryCaseRepo",
    "InMemoryAssessmentRepo",
    "InMemoryCaseFactRepo",
    "InMemoryDocumentRepo",
    "InMemoryPolicyRuleRepo",
    "InMemoryWorkspaceRepo",
    "final_answer_model",
]

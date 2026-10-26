"""Fakes for the retained conversation contracts."""

from tests.fakes.fake_agent_model import (
    FakeStreamingToolCallingModel,
    FakeToolCallingModel,
    final_answer_model,
)
from tests.fakes.fake_audit_log import FakeAuditLogRepo
from tests.fakes.fake_auth import FakeAuth, FakeOAuthProvider
from tests.fakes.fake_chat import FakeChat
from tests.fakes.fake_conversation_run_repo import InMemoryConversationRunRepo
from tests.fakes.fake_copilot_agent import FakeCopilotAgent
from tests.fakes.fake_document_loader import FakeDocumentLoader
from tests.fakes.fake_document_ocr import FakeDocumentOcr
from tests.fakes.fake_document_parser import FakeDocumentParser
from tests.fakes.fake_embed import FakeEmbed
from tests.fakes.fake_evidence_chunker import FakeEvidenceChunker
from tests.fakes.fake_evidence_index import FakeEvidenceIndex
from tests.fakes.fake_job_dispatcher import FakeJobDispatcher
from tests.fakes.fake_kb_repo import FakeKbRepo
from tests.fakes.fake_object_store import FakeObjectStore
from tests.fakes.fake_qa import FakeClaimSupportVerifier, FakeEvidenceQAGenerator
from tests.fakes.fake_readiness import FakeReadiness
from tests.fakes.fake_repos import InMemoryDocumentRepo, InMemoryTaskRepo, InMemoryUserRepo
from tests.fakes.fake_retrieve import FakeRetrieve
from tests.fakes.fake_trace import FakeTrace, FakeTraceSpan
from tests.fakes.fake_websearch import FakeWebSearch

__all__ = [
    "FakeToolCallingModel",
    "FakeStreamingToolCallingModel",
    "final_answer_model",
    "FakeAuditLogRepo",
    "FakeAuth",
    "FakeOAuthProvider",
    "FakeChat",
    "FakeCopilotAgent",
    "InMemoryConversationRunRepo",
    "FakeDocumentLoader",
    "FakeDocumentOcr",
    "FakeDocumentParser",
    "FakeEmbed",
    "FakeEvidenceChunker",
    "FakeEvidenceIndex",
    "FakeJobDispatcher",
    "FakeKbRepo",
    "FakeObjectStore",
    "FakeClaimSupportVerifier",
    "FakeEvidenceQAGenerator",
    "FakeReadiness",
    "InMemoryDocumentRepo",
    "InMemoryTaskRepo",
    "InMemoryUserRepo",
    "FakeRetrieve",
    "FakeTrace",
    "FakeTraceSpan",
    "FakeWebSearch",
]

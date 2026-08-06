"""离线测试用 Fake 实现：所有 Fake 都实现 domain.ports 中的对应 Protocol。"""

from tests.fakes.fake_audit_log import FakeAuditLogRepo
from tests.fakes.fake_auth import FakeAuth, FakeOAuthProvider
from tests.fakes.fake_chat import FakeChat
from tests.fakes.fake_document_loader import FakeDocumentLoader
from tests.fakes.fake_document_parser import FakeDocumentParser
from tests.fakes.fake_embed import FakeEmbed
from tests.fakes.fake_evidence import FakeEvidence
from tests.fakes.fake_evidence_chunker import FakeEvidenceChunker
from tests.fakes.fake_evidence_index import FakeEvidenceIndex
from tests.fakes.fake_kb_repo import FakeKbRepo
from tests.fakes.fake_object_store import FakeObjectStore
from tests.fakes.fake_repos import (
    InMemoryCaseFactRepo,
    InMemoryCaseRepo,
    InMemoryDocumentRepo,
    InMemoryPolicyRuleRepo,
    InMemoryTaskRepo,
    InMemoryUserRepo,
    InMemoryWorkspaceRepo,
)
from tests.fakes.fake_retrieve import FakeRetrieve
from tests.fakes.fake_websearch import FakeWebSearch

__all__ = [
    "FakeAuditLogRepo",
    "FakeAuth",
    "FakeChat",
    "FakeDocumentLoader",
    "FakeDocumentParser",
    "FakeEmbed",
    "FakeEvidenceChunker",
    "FakeEvidenceIndex",
    "FakeEvidence",
    "FakeKbRepo",
    "FakeObjectStore",
    "FakeOAuthProvider",
    "FakeRetrieve",
    "FakeWebSearch",
    "InMemoryTaskRepo",
    "InMemoryUserRepo",
    "InMemoryCaseRepo",
    "InMemoryCaseFactRepo",
    "InMemoryDocumentRepo",
    "InMemoryPolicyRuleRepo",
    "InMemoryWorkspaceRepo",
]

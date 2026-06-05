"""离线测试用 Fake 实现：所有 Fake 都实现 domain.ports 中的对应 Protocol。"""

from tests.fakes.fake_chat import FakeChat
from tests.fakes.fake_embed import FakeEmbed
from tests.fakes.fake_evidence import FakeEvidence
from tests.fakes.fake_repos import InMemoryTaskRepo, InMemoryUserRepo
from tests.fakes.fake_retrieve import FakeRetrieve
from tests.fakes.fake_websearch import FakeWebSearch

__all__ = [
    "FakeChat",
    "FakeEmbed",
    "FakeEvidence",
    "FakeRetrieve",
    "FakeWebSearch",
    "InMemoryTaskRepo",
    "InMemoryUserRepo",
]

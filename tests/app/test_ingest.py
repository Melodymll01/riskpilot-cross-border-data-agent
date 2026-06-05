"""IngestionUseCase 单测：仅校验 owner_id 守卫 + EmbedPort 链路通。"""

from __future__ import annotations

import pytest

from app.use_cases.ingest import IngestionUseCase
from tests.fakes import FakeEmbed


def test_rejects_empty_owner() -> None:
    uc = IngestionUseCase(FakeEmbed())
    with pytest.raises(ValueError):
        uc.ingest_texts("", ["hi"])


def test_empty_texts_returns_zero() -> None:
    uc = IngestionUseCase(FakeEmbed())
    result = uc.ingest_texts("anon:a", [])
    assert result == {"owner_id": "anon:a", "text_count": 0, "embedding_dim": 0}


def test_returns_embedding_dim() -> None:
    uc = IngestionUseCase(FakeEmbed(dim=16))
    result = uc.ingest_texts("anon:a", ["t1", "t2"])
    assert result == {"owner_id": "anon:a", "text_count": 2, "embedding_dim": 16}

import pytest

from infra.search import local_embedder


class FakeTokenizer:
    def __call__(self, texts, **kwargs):
        del kwargs
        return {"input_ids": [list(range(len(text) + 2)) for text in texts]}


class FakeModel:
    max_seq_length = 8
    tokenizer = FakeTokenizer()

    def encode(self, texts, **kwargs):
        del kwargs
        return FakeEncoded([[float(len(text))] for text in texts])


class FakeEncoded(list):
    def tolist(self):
        return list(self)


def test_local_embedder_rejects_inputs_that_would_be_truncated(monkeypatch):
    monkeypatch.setattr(local_embedder, "_MODELS", {"test": FakeModel()})
    embedder = local_embedder.LocalSentenceEmbedder("test", max_input_tokens=6)

    with pytest.raises(ValueError, match="超过安全 token 上限"):
        embedder.embed_texts(["12345"])


def test_local_embedder_accepts_inputs_within_configured_budget(monkeypatch):
    monkeypatch.setattr(local_embedder, "_MODELS", {"test": FakeModel()})
    embedder = local_embedder.LocalSentenceEmbedder("test", max_input_tokens=6)

    assert embedder.embed_texts(["1234"]) == [[4.0]]

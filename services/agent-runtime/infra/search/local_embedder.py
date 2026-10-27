"""Real local Chinese embeddings; lazy loading and per-run resource accounting."""

from __future__ import annotations

import threading
from typing import Any

from domain.ports import RunControlPort

_MODELS: dict[str, Any] = {}
_LOCK = threading.Lock()


class LocalSentenceEmbedder:
    def __init__(
        self,
        model_name: str = "BAAI/bge-small-zh-v1.5",
        *,
        local_files_only: bool = False,
        max_input_tokens: int | None = None,
    ) -> None:
        self.model_name = model_name
        self.local_files_only = local_files_only
        self.max_input_tokens = max_input_tokens

    def _model(self) -> Any:
        with _LOCK:
            if self.model_name not in _MODELS:
                from sentence_transformers import SentenceTransformer

                _MODELS[self.model_name] = SentenceTransformer(
                    self.model_name, device="cpu", local_files_only=self.local_files_only
                )
            return _MODELS[self.model_name]

    def embed_texts(
        self,
        texts: list[str],
        *,
        runtime_control: RunControlPort | None = None,
    ) -> list[list[float]]:
        if not texts:
            return []

        def invoke() -> list[list[float]]:
            model = self._model()
            self._validate_input_lengths(model, texts)
            return model.encode(texts, normalize_embeddings=True, show_progress_bar=False).tolist()

        if runtime_control is None:
            return invoke()
        vectors = runtime_control.call_model(
            operation="local_embedding", stage="tool", invoke=invoke
        )
        runtime_control.record_model_usage(input_tokens=None, output_tokens=None)
        return vectors

    def embed_query(
        self, query: str, *, runtime_control: RunControlPort | None = None
    ) -> list[float]:
        return self.embed_texts([query], runtime_control=runtime_control)[0]

    def _validate_input_lengths(self, model: Any, texts: list[str]) -> None:
        """拒绝会被 Transformer 静默截断的输入。"""
        tokenizer = getattr(model, "tokenizer", None)
        model_limit = getattr(model, "max_seq_length", None)
        if tokenizer is None or not isinstance(model_limit, int):
            return
        configured_limit = self.max_input_tokens or model_limit
        limit = min(configured_limit, model_limit)
        encoded = tokenizer(texts, add_special_tokens=True, truncation=False, padding=False)
        lengths = [len(ids) for ids in encoded.get("input_ids", [])]
        if not lengths:
            return
        longest = max(lengths)
        if longest > limit:
            raise ValueError(
                f"Embedding 输入超过安全 token 上限: {longest} > {limit}; "
                "请降低 chunk_size 或使用更大上下文的 Embedding 模型后重建索引"
            )

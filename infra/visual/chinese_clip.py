"""Chinese-CLIP 图文共享向量适配器。"""

from __future__ import annotations

import io
import threading
from typing import Any

import torch
from PIL import Image


class ChineseCLIPEmbedder:
    """懒加载 Chinese-CLIP；适合几十张图片的小规模作品集演示。"""

    def __init__(
        self,
        model_name: str = "OFA-Sys/chinese-clip-vit-base-patch16",
        *,
        model: Any | None = None,
        processor: Any | None = None,
    ) -> None:
        self._model_name = model_name
        self._model = model
        self._processor = processor
        self._lock = threading.Lock()

    def embed_images(self, images: list[bytes]) -> list[list[float]]:
        if not images:
            return []
        model, processor = self._ensure_loaded()
        decoded = [
            Image.open(io.BytesIO(content)).convert("RGB") for content in images
        ]
        inputs = processor(images=decoded, return_tensors="pt", padding=True)
        with torch.inference_mode():
            features = model.get_image_features(**inputs)
        return _normalize(features)

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        model, processor = self._ensure_loaded()
        inputs = processor(
            text=texts,
            return_tensors="pt",
            padding=True,
            truncation=True,
        )
        with torch.inference_mode():
            features = model.get_text_features(**inputs)
        return _normalize(features)

    def _ensure_loaded(self) -> tuple[Any, Any]:
        if self._model is not None and self._processor is not None:
            return self._model, self._processor
        with self._lock:
            if self._model is None or self._processor is None:
                from transformers import ChineseCLIPModel, ChineseCLIPProcessor

                self._processor = ChineseCLIPProcessor.from_pretrained(
                    self._model_name
                )
                self._model = ChineseCLIPModel.from_pretrained(self._model_name)
                self._model.eval()
        return self._model, self._processor


def _normalize(features: Any) -> list[list[float]]:
    features = features / features.norm(dim=-1, keepdim=True).clamp_min(1e-12)
    return features.detach().cpu().tolist()

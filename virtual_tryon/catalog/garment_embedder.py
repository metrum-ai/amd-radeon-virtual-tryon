# Copyright Advanced Micro Devices, Inc.
# 
# SPDX-License-Identifier: MIT

"""Garment embedder: BGE-small-en-v1.5 (text) + DINOv3 ViT-S (visual), 384-dim each."""

from __future__ import annotations

import os
from pathlib import Path

import torch
from PIL import Image
from transformers import AutoImageProcessor, AutoModel, AutoTokenizer

_TEXT_MODEL_ID = "BAAI/bge-small-en-v1.5"
_VISUAL_MODEL_ID = "facebook/dinov3-vits16-pretrain-lvd1689m"


class GarmentEmbedder:
    """Produces 384-dim text and visual embeddings for catalog garments.

    Args:
        device: Torch device string (e.g. ``"cuda"``, ``"cpu"``).

    """

    def __init__(self, device: str = "cpu") -> None:
        """Initialize the GarmentEmbedder."""
        self._device = device
        self._torch = torch
        local_only = _is_offline_mode()
        self._text_tokenizer = AutoTokenizer.from_pretrained(
            _TEXT_MODEL_ID,
            local_files_only=local_only,
        )
        self._text_model = AutoModel.from_pretrained(
            _TEXT_MODEL_ID,
            local_files_only=local_only,
        ).to(device)
        self._text_model.eval()

        self._auto_model = AutoModel
        self._visual_model = None
        self._image_processor = None

    def embed_text(self, text: str) -> list[float]:
        """Embed a garment text description with BGE-small-en-v1.5.

        Args:
            text: Concatenated name, category, color, and description string.

        Returns:
            384-dim float list.

        """
        torch = self._torch
        with torch.no_grad():
            inputs = self._text_tokenizer(
                text,
                return_tensors="pt",
                truncation=True,
                max_length=128,
                padding=True,
            ).to(self._device)
            outputs = self._text_model(**inputs)
            embedding = outputs.last_hidden_state[:, 0, :]
            embedding = torch.nn.functional.normalize(embedding, dim=-1)
            return embedding[0].cpu().tolist()

    def embed_image(self, image_path: str | Path) -> list[float]:
        """Embed a garment image with DINOv3 ViT-S via pooler_output.

        Args:
            image_path: Path to the garment image file.

        Returns:
            384-dim float list.

        """
        if self._visual_model is None or self._image_processor is None:
            self._visual_model = self._auto_model.from_pretrained(
                _VISUAL_MODEL_ID,
                local_files_only=_is_offline_mode(),
            ).to(self._device)
            self._visual_model.eval()
            self._image_processor = AutoImageProcessor.from_pretrained(
                _VISUAL_MODEL_ID,
                local_files_only=_is_offline_mode(),
            )

        torch = self._torch
        with torch.no_grad():
            try:
                image = Image.open(image_path).convert("RGB")
            except (FileNotFoundError, OSError) as exc:
                raise RuntimeError(
                    f"Cannot load garment image at {image_path}: {exc}"
                ) from exc
            inputs = self._image_processor(
                images=image, return_tensors="pt"
            ).to(self._device)
            outputs = self._visual_model(**inputs)
            embedding = outputs.pooler_output  # shape (1, 384)
            embedding = torch.nn.functional.normalize(embedding, dim=-1)
            return embedding[0].cpu().tolist()

    def embed_garment(
        self, text: str, image_path: str | Path
    ) -> tuple[list[float], list[float]]:
        """Return (text_embedding, visual_embedding) for a garment.

        Args:
            text: Text description string.
            image_path: Path to garment image.

        Returns:
            Tuple of (text_vector, visual_vector), each 384-dim.

        """
        return self.embed_text(text), self.embed_image(image_path)


def _is_offline_mode() -> bool:
    """Return whether Hugging Face model loading should use local cache only."""
    return (
        os.environ.get("HF_HUB_OFFLINE") == "1"
        or os.environ.get("TRANSFORMERS_OFFLINE") == "1"
    )

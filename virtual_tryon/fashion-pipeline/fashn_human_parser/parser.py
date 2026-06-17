# Copyright Advanced Micro Devices, Inc.
#
# SPDX-License-Identifier: MIT

"""FASHN Human Parser — commercially clean compatibility wrapper.

The public API matches the original package exactly so that fashn-vton
requires zero code changes.
"""

import logging
import os
from pathlib import Path
from typing import List, Union

import cv2
import numpy as np
from PIL import Image, ImageOps

from .labels import IDS_TO_LABELS
from .mapping import remap_atr_to_fashn

# ImageNet normalization constants (same as original training)
_IMAGENET_MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32)
_IMAGENET_STD = np.array([0.229, 0.224, 0.225], dtype=np.float32)

# Model input/output size (basso4/humanparsing ONNX model)
_INPUT_SIZE = 512
_OUTPUT_SIZE = 128

logger = logging.getLogger(__name__)


def _resolve_onnx_path() -> str:
    """Return path to the ONNX model, downloading if necessary.

    Search order:
    1. Environment variable FASHN_PARSER_ONNX_PATH
    2. /app/models/parsing_atr.onnx (Docker build default)
    3. HuggingFace cache via huggingface_hub
    """
    env_path = os.environ.get("FASHN_PARSER_ONNX_PATH")
    if env_path and Path(env_path).exists():
        return env_path

    default = "/app/models/parsing_atr.onnx"
    if Path(default).exists():
        return default

    try:
        from huggingface_hub import hf_hub_download

        return hf_hub_download(
            repo_id="basso4/humanparsing",
            filename="parsing_atr.onnx",
            local_dir="/app/models",
            local_dir_use_symlinks=False,
        )
    except Exception as exc:
        raise RuntimeError(
            "Cannot locate parsing_atr.onnx. "
            "Set FASHN_PARSER_ONNX_PATH or ensure HuggingFace cache is available."
        ) from exc


class FashnHumanParser:
    """Human parsing model that segments images into 18 semantic classes.

    This wrapper uses an Apache-2.0 ONNX model under the hood while exposing
    the exact same interface as the original fashn-human-parser package.

    Args:
        model_id: Ignored (kept for API compatibility).
        device: Ignored for ONNX CPU runtime (kept for API compatibility).

    Example:
        >>> parser = FashnHumanParser(device="cpu")
        >>> segmentation = parser.predict(image)
        >>> # segmentation is a numpy array of shape (H, W) with values 0-17
    """

    def __init__(
        self,
        model_id: str = "basso4/humanparsing",
        device: str = None,
    ):
        import onnxruntime as ort

        self._device = device or "cpu"
        onnx_path = _resolve_onnx_path()
        logger.info("Loading FashnHumanParser (ONNX) from %s", onnx_path)

        # Use CPU execution provider — human parsing is lightweight
        # and avoids ROCm/CUDA dependency conflicts in the pipeline.
        sess_options = ort.SessionOptions()
        sess_options.graph_optimization_level = (
            ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        )
        self._session = ort.InferenceSession(
            onnx_path,
            sess_options,
            providers=["CPUExecutionProvider"],
        )
        self._input_name = self._session.get_inputs()[0].name
        logger.info("FashnHumanParser loaded")

    def _preprocess_single(self, image: np.ndarray) -> np.ndarray:
        """Preprocess a single image for ONNX model input.

        Args:
            image: RGB image as numpy array (H, W, 3), uint8.

        Returns:
            Preprocessed float32 array of shape (1, 3, _INPUT_HEIGHT, _INPUT_WIDTH).
        """
        resized = cv2.resize(
            image,
            (_INPUT_SIZE, _INPUT_SIZE),
            interpolation=cv2.INTER_AREA,
        )
        normalized = resized.astype(np.float32) / 255.0
        normalized = (normalized - _IMAGENET_MEAN) / _IMAGENET_STD
        transposed = normalized.transpose(2, 0, 1)  # HWC -> CHW
        return np.expand_dims(transposed, axis=0)  # Add batch dimension

    def _to_numpy(self, image: Union[Image.Image, np.ndarray, str]) -> np.ndarray:
        """Convert various image formats to RGB numpy array."""
        if isinstance(image, str):
            with Image.open(image) as pil_img:
                pil_img = ImageOps.exif_transpose(pil_img)
                image = np.array(pil_img.convert("RGB"))
        elif isinstance(image, Image.Image):
            image = np.array(image.convert("RGB"))

        if isinstance(image, np.ndarray):
            if image.ndim == 2:
                image = cv2.cvtColor(image, cv2.COLOR_GRAY2RGB)
            elif image.ndim == 3:
                if image.shape[2] == 4:
                    image = cv2.cvtColor(image, cv2.COLOR_RGBA2RGB)
                elif image.shape[2] != 3:
                    raise ValueError(
                        f"Expected RGB image with 3 channels, got {image.shape[2]}"
                    )
            else:
                raise ValueError(
                    f"Expected 2D or 3D image array, got {image.ndim}D"
                )

            if image.dtype in (np.float32, np.float64):
                image = (image * 255).clip(0, 255).astype(np.uint8)
        else:
            raise TypeError(
                f"Unsupported image type: {type(image).__name__}"
            )

        return image

    def predict(
        self,
        image: Union[Image.Image, np.ndarray, str, List],
        return_logits: bool = False,
    ) -> Union[np.ndarray, "np.ndarray", List]:
        """Run human parsing on one or more images.

        Args:
            image: Input image(s) — single or list of PIL/numpy/path.
            return_logits: If True, returns one-hot logits (unsupported by ONNX
                wrapper; raises NotImplementedError).

        Returns:
            Numpy array (or list) of shape (H, W) with class IDs 0-17.
        """
        if return_logits:
            raise NotImplementedError(
                "return_logits=True is not supported by the ONNX compatibility wrapper. "
                "FASHN vton only uses return_logits=False in production."
            )

        is_batch = isinstance(image, list)
        images = image if is_batch else [image]

        if len(images) == 0:
            return []

        for i, img in enumerate(images):
            if img is None:
                raise ValueError(f"Image at index {i} is None")

        images_np = [self._to_numpy(img) for img in images]
        original_sizes = [(img.shape[0], img.shape[1]) for img in images_np]

        results = []
        for img_np, size in zip(images_np, original_sizes):
            preprocessed = self._preprocess_single(img_np)
            outputs = self._session.run(None, {self._input_name: preprocessed})
            # The first output is the main segmentation logits: (1, 18, 128, 128)
            raw_logits = outputs[0].squeeze(0)  # (18, 128, 128)

            # Upsample each class channel to original size before argmax so
            # class boundaries are resolved at full resolution, not 128x128.
            h, w = size
            upsampled = np.stack(
                [
                    cv2.resize(
                        raw_logits[c],
                        (w, h),
                        interpolation=cv2.INTER_LINEAR,
                    )
                    for c in range(raw_logits.shape[0])
                ],
                axis=0,
            )  # (18, H, W)
            pred_seg = upsampled.argmax(axis=0).astype(np.uint8)

            # Remap ATR labels to FASHN taxonomy
            pred_seg = remap_atr_to_fashn(pred_seg)
            results.append(pred_seg)

        return results if is_batch else results[0]

    @staticmethod
    def get_label_name(label_id: int) -> str:
        """Get the label name for a given ID."""
        return IDS_TO_LABELS.get(label_id, "unknown")

    @staticmethod
    def get_labels() -> dict:
        """Get the full ID to label mapping."""
        return IDS_TO_LABELS.copy()

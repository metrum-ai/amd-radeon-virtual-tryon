# Copyright Advanced Micro Devices, Inc.
# 
# SPDX-License-Identifier: MIT

"""Persistent FASHN try-on HTTP server.

Keeps one TryOnPipeline warm on a single GPU.  Run one instance per
GPU on different ports (e.g. 8010, 8011).

Usage:
    python fashn_server.py --device cuda:0 --port 8010 \\
        --weights-dir /app/weights
"""

import argparse
import asyncio
import base64
import binascii
import io
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import Response
from PIL import Image
from pydantic import BaseModel

_FASHN_SRC = Path(__file__).parent.parent / "fashn-vton-1.5" / "src"
if _FASHN_SRC.is_dir():
    sys.path.insert(0, str(_FASHN_SRC))

from fashn_vton import TryOnPipeline  # noqa: E402

try:
    from identity_preservation import preserve_identity_regions
except ModuleNotFoundError:

    def preserve_identity_regions(person_img, result_img, hp_model):
        """Return the generated image when identity helper is unavailable."""
        return result_img


_pipeline: TryOnPipeline | None = None
_device: str = "cuda:0"
_executor = ThreadPoolExecutor(max_workers=1)

# Input-hardening limits for the /tryon endpoint. Payloads above these
# bounds are rejected before inference to prevent memory-exhaustion and
# decompression-bomb DoS. Tunable via env for unusual catalog images.
_MAX_IMAGE_BYTES = int(
    os.environ.get("FASHN_MAX_IMAGE_BYTES", str(16 * 1024 * 1024))
)
_MAX_IMAGE_PIXELS = int(
    os.environ.get("FASHN_MAX_IMAGE_PIXELS", str(40_000_000))
)
# base64 inflates ~4/3; allow a margin for padding/whitespace.
_MAX_B64_CHARS = (_MAX_IMAGE_BYTES * 4) // 3 + 1024

# Make PIL itself refuse absurd pixel counts (defence in depth alongside
# the explicit size check in _load_rgb_image).
Image.MAX_IMAGE_PIXELS = _MAX_IMAGE_PIXELS


class ImageInputError(ValueError):
    """Raised when a request image is missing, malformed, or oversized."""


def _load_rgb_image(b64: str, field: str) -> Image.Image:
    """Decode a base64 image with strict byte and pixel limits.

    Args:
        b64: Base64-encoded image payload.
        field: Request field name, used in error messages.

    Returns:
        The decoded image converted to RGB.

    Raises:
        ImageInputError: If the payload is not valid base64, exceeds the
            byte/pixel limits, or is not a decodable image.

    """
    try:
        raw = base64.b64decode(b64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ImageInputError(f"{field}: not valid base64") from exc
    if len(raw) > _MAX_IMAGE_BYTES:
        raise ImageInputError(
            f"{field}: decoded image exceeds {_MAX_IMAGE_BYTES} bytes"
        )
    try:
        img = Image.open(io.BytesIO(raw))
    except Image.DecompressionBombError as exc:
        # PIL raises this at open() when dimensions exceed ~2x its limit.
        raise ImageInputError(f"{field}: image exceeds pixel limit") from exc
    except (OSError, ValueError) as exc:
        raise ImageInputError(f"{field}: not a decodable image") from exc
    # Header gives dimensions before the full raster is materialised, so
    # reject pixel bombs prior to the expensive load() (covers the band
    # between PIL's limit and its 2x hard error, where PIL only warns).
    if img.width * img.height > _MAX_IMAGE_PIXELS:
        raise ImageInputError(f"{field}: image exceeds pixel limit")
    try:
        img.load()
    except Image.DecompressionBombError as exc:
        raise ImageInputError(f"{field}: image exceeds pixel limit") from exc
    except (OSError, ValueError) as exc:
        raise ImageInputError(f"{field}: not a decodable image") from exc
    return img.convert("RGB")


class TryOnRequest(BaseModel):
    """JSON body for POST /tryon."""

    person_b64: str
    garment_b64: str
    category: Literal["tops", "bottoms", "one-pieces"] = "tops"
    photo_type: Literal["model", "flat-lay"] = "model"
    timesteps: int = 30
    seed: int = 42
    guidance_scale: float = 1.5
    skip_cfg_last_n_steps: int = 1
    segmentation_free: bool = True
    preserve_identity: bool = True


def _run_inference(req: TryOnRequest) -> bytes:
    """Blocking GPU inference — called from thread pool.

    Raises:
        ImageInputError: If either input image is malformed or oversized.

    """
    person_img = _load_rgb_image(req.person_b64, "person_b64")
    garment_img = _load_rgb_image(req.garment_b64, "garment_b64")
    output = _pipeline(
        person_image=person_img,
        garment_image=garment_img,
        category=req.category,
        garment_photo_type=req.photo_type,
        num_timesteps=req.timesteps,
        guidance_scale=req.guidance_scale,
        skip_cfg_last_n_steps=req.skip_cfg_last_n_steps,
        seed=req.seed,
        segmentation_free=req.segmentation_free,
    )
    buf = io.BytesIO()
    result_img = output.images[0]
    if req.preserve_identity:
        try:
            result_img = preserve_identity_regions(
                person_img,
                result_img,
                _pipeline.hp_model,
            )
        except Exception as exc:  # pylint: disable=broad-except
            print(f"  [identity] WARNING: preservation skipped: {exc}")
    result_img.save(buf, format="PNG")
    return buf.getvalue()


@asynccontextmanager
async def _lifespan(app: FastAPI):
    yield


app = FastAPI(lifespan=_lifespan)


@app.post("/tryon")
async def tryon(req: TryOnRequest) -> Response:
    """Run try-on inference; return PNG bytes.

    Raises:
        HTTPException: 413 if a payload is too large, 400 if an image is
            malformed or exceeds the decoded size/pixel limits.

    """
    # Cheap pre-check: reject oversized payloads without decoding them on
    # the event loop (the full size/pixel checks run in _load_rgb_image).
    for field, b64 in (
        ("person_b64", req.person_b64),
        ("garment_b64", req.garment_b64),
    ):
        if len(b64) > _MAX_B64_CHARS:
            raise HTTPException(
                status_code=413, detail=f"{field}: payload too large"
            )
    loop = asyncio.get_event_loop()
    try:
        png = await loop.run_in_executor(_executor, _run_inference, req)
    except ImageInputError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return Response(content=png, media_type="image/png")


@app.get("/health")
async def health() -> dict:
    """Liveness probe."""
    return {"status": "ok", "device": _device}


def _ensure_weights(weights_dir: str) -> None:
    """Download model weights if not already present."""
    from huggingface_hub import hf_hub_download

    model_path = os.path.join(weights_dir, "model.safetensors")
    dwpose_dir = os.path.join(weights_dir, "dwpose")
    dwpose_files = [
        os.path.join(dwpose_dir, "yolox_l.onnx"),
        os.path.join(dwpose_dir, "dw-ll_ucoco_384.onnx"),
    ]

    if not os.path.exists(model_path):
        print("  Downloading TryOnModel weights (first run)...")
        os.makedirs(weights_dir, exist_ok=True)
        hf_hub_download(
            repo_id="fashn-ai/fashn-vton-1.5",
            filename="model.safetensors",
            local_dir=weights_dir,
        )

    missing_dwpose = [f for f in dwpose_files if not os.path.exists(f)]
    if missing_dwpose:
        print("  Downloading DWPose weights (first run)...")
        os.makedirs(dwpose_dir, exist_ok=True)
        for filename in ["yolox_l.onnx", "dw-ll_ucoco_384.onnx"]:
            hf_hub_download(
                repo_id="fashn-ai/DWPose",
                filename=filename,
                local_dir=dwpose_dir,
            )


def main() -> None:
    """Parse args, load model, start server."""
    global _pipeline, _device
    ap = argparse.ArgumentParser(description="Persistent FASHN try-on server")
    ap.add_argument("--device", default="cuda:0")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8010)
    ap.add_argument("--weights-dir", default="/app/weights")
    # Used by fashn-weights-init: download once and exit, so fashn0/fashn1
    # don't race each other on the shared fashn_weights volume.
    ap.add_argument("--download-only", action="store_true",
                    help="Download weights into --weights-dir and exit")
    args = ap.parse_args()
    _device = args.device
    _ensure_weights(args.weights_dir)
    if args.download_only:
        print(f"  Weights ready in {args.weights_dir}")
        return
    print(f"  Loading TryOnPipeline on {_device} ...")
    _pipeline = TryOnPipeline(weights_dir=args.weights_dir, device=_device)
    print(f"  Ready — listening on {args.host}:{args.port}")
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()

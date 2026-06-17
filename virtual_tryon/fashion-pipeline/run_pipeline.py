# Copyright Advanced Micro Devices, Inc.
# 
# SPDX-License-Identifier: MIT

"""Fashion slideshow pipeline — server-based, 90-second warm budget.

Orchestrates:
  1. Uniform candidate frame extraction
  2. DWPose pose-diverse keyframe selection + garment photo-type detection
  3. FASHN virtual try-on via persistent warm HTTP servers
  4. (Parallel) LLM styling tips + ACE-Step music queue
  5. Crossfade slideshow assembly with tip card overlays
  6. Background music mux

Output per run:
    <output_dir>/
      keyframes/        — selected original frames
      tryon/            — FASHN output frames
      slideshow.mp4     — final video (with music if available)
      metadata.json     — job params, timing, frame counts

Usage (single garment):
    python run_pipeline.py \\
        --video input/person.mp4 \\
        --garment input/shirt.png \\
        --category tops \\
        --server-urls http://fashn0:7860,http://fashn1:7860 \\
        --output-dir output/job_001

Usage (dual garment):
    python run_pipeline.py \\
        --video input/person.mp4 \\
        --garment-top input/shirt.png \\
        --garment-bottom input/jeans.png \\
        --server-urls http://fashn0:7860,http://fashn1:7860 \\
        --output-dir output/job_001

Environment variables:
    LLM_API_BASE        — OpenAI-compatible base URL
                          (default: http://ollama-metrics:8080/v1)
    LLM_MODEL           — model name (default: qwen3.6:27b)
    LLM_API_KEY         — API key for LLM service (empty for local Ollama)
    ACESTEP_URL         — ACE-Step server URL (default: http://localhost:8001)
    ACESTEP_API_KEY     — optional ACE-Step API key
"""

import argparse
import concurrent.futures
import json
import os
import shutil
import sys
import threading
import time
import urllib.request
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urlparse

import cv2
from fashion_tips import generate_fashion_tips_llm
from fashn_client import (
    run_fashn_distributed,
    run_fashn_dual_distributed,
    wait_for_server,
)
from frame_selector import (
    DWposeDetector,
    detect_garment_photo_type,
    extract_frames,
    save_keyframes,
    select_diverse_frames,
)
from music_client import add_music, poll_music, queue_music
from slideshow_assembler import assemble_slideshow, slideshow_duration_secs


# DWpose shares GPU 0 with the fashn0 server; running multiple ONNX sessions
# concurrently causes VRAM contention. One DWpose session at a time is enough
# because the stage is fast (<10s) relative to downstream FASHN inference.
_DWPOSE_LOCK = threading.Semaphore(1)


def _unload_ollama_model(api_base: str, model: str) -> None:
    """Unload an Ollama model so another GPU service can allocate memory."""
    if "ollama" not in api_base:
        return
    base = api_base.rstrip("/")
    if base.endswith("/v1"):
        base = base[:-3]
    parsed = urlparse(base)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(
            f"URL must use http or https scheme, got {parsed.scheme!r}: {base}"
        )
    payload = json.dumps(
        {
            "model": model,
            "prompt": "",
            "stream": False,
            "keep_alive": 0,
        }
    ).encode()
    req = urllib.request.Request(
        f"{base}/api/generate",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60):  # nosec B310
        pass


def _garment_name(path: str) -> str:
    """Return a human-readable name derived from a garment filename."""
    return Path(path).stem.replace("-", " ").replace("_", " ").lower()


def _video_dimensions(video_path: str) -> tuple[int, int]:
    """Return source video dimensions as ``(width, height)``."""
    cap = cv2.VideoCapture(video_path)
    try:
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    finally:
        cap.release()
    if width <= 0 or height <= 0:
        raise RuntimeError(f"Could not read video dimensions: {video_path}")
    return width, height


def run_pipeline(
    video: str,
    garment: str | None,
    garment_top: str | None,
    garment_bottom: str | None,
    category: str,
    server_urls: list[str],
    output_dir: Path,
    *,
    num_frames: int = 8,
    candidate_frames: int = 30,
    frame_duration: float = 3.0,
    timesteps: int = 20,
    seed: int = 42,
    skip_music: bool = False,
    music_prompt: str | None = None,
    weights_dir: str | None = None,
    llm_api_base: str = "http://ollama-metrics:8080/v1",
    llm_model: str = "qwen3.6:27b",
    llm_api_key: str = "",
    garment_metadata: str = "",
    acestep_url: str = "http://localhost:8001",
    acestep_key: str | None = None,
    progress_callback: Callable[[str, str, int], None] | None = None,
    unload_llm: bool = True,
) -> dict:
    """Run the full pipeline and return a metadata dict.

    Args:
        video: Input video path.
        garment: Single garment image path (exclusive with dual args).
        garment_top: Top garment image path (dual mode).
        garment_bottom: Bottom garment image path (dual mode).
        category: Garment category for single-garment mode.
        server_urls: FASHN server base URLs; frames are distributed
            round-robin across these servers.
        output_dir: Directory to write all outputs.
        num_frames: Number of pose-diverse frames to select.
        candidate_frames: Number of uniformly-extracted candidates.
        frame_duration: Seconds each slide is held in the slideshow.
        timesteps: FASHN diffusion steps per inference call.
        seed: Random seed.
        skip_music: Skip ACE-Step music generation.
        music_prompt: Custom ACE-Step music prompt.
        weights_dir: Path to fashn-vton-1.5/weights (for DWPose).
        llm_api_base: LLM endpoint base URL.
        llm_model: LLM model name.
        llm_api_key: LLM API key.
        garment_metadata: Structured garment metadata for styling tips.
        acestep_url: ACE-Step server URL.
        acestep_key: ACE-Step API key.
        unload_llm: Unload the Ollama model after tips generation to free
            GPU memory for ACE-Step.  Set to False when called from a
            parallel multi-outfit context so that concurrent tips calls
            are not racing against an unload from a sibling thread.

    Returns:
        Metadata dict with paths, timing, and frame counts.

    """

    def _progress(stage: str, message: str, progress: int) -> None:
        if progress_callback:
            progress_callback(stage, message, progress)

    is_dual = bool(garment_top and garment_bottom)
    tips_category = "both" if is_dual else category

    output_dir.mkdir(parents=True, exist_ok=True)
    candidates_dir = output_dir / "candidates"
    keyframe_dir = output_dir / "keyframes"
    tryon_dir = output_dir / "tryon"
    slideshow_raw = str(output_dir / "slideshow_raw.mp4")
    slideshow_final = str(output_dir / "slideshow.mp4")

    t0 = time.time()
    source_size = _video_dimensions(video)

    # ------------------------------------------------------------------
    # Stage 1: Extract candidate frames
    # ------------------------------------------------------------------
    _progress("extract_frames", "Extracting candidate video frames", 10)
    print(f"\n[1] Extracting {candidate_frames} candidate frames")
    fps = extract_frames(video, candidate_frames, candidates_dir)
    fps = max(fps, 24.0)
    print(f"    FPS: {fps:.1f}")

    # ------------------------------------------------------------------
    # Stage 2: DWPose selection + garment photo-type detection
    # ------------------------------------------------------------------
    _progress("select_keyframes", "Selecting pose-diverse keyframes", 25)
    print(f"\n[2] Selecting {num_frames} pose-diverse frames")
    if weights_dir is None:
        weights_dir = str(
            Path(__file__).parent.parent / "fashn-vton-1.5" / "weights"
        )
    with _DWPOSE_LOCK:
        dwpose = DWposeDetector(
            checkpoints_dir=str(Path(weights_dir) / "dwpose"),
            device="cuda:0",
        )
        selected, orientations = select_diverse_frames(
            candidates_dir, dwpose, num_frames
        )
        print("\n[2b] Detecting garment photo types")
        _progress("detect_garments", "Detecting garment photo type", 35)
        if is_dual:
            photo_type_top = detect_garment_photo_type(
                garment_top, dwpose  # type: ignore[arg-type]
            )
            photo_type_bottom = detect_garment_photo_type(
                garment_bottom, dwpose  # type: ignore[arg-type]
            )
            photo_type_single = None
        else:
            photo_type_single = detect_garment_photo_type(
                garment, dwpose  # type: ignore[arg-type]
            )
            photo_type_top = photo_type_bottom = None
        del dwpose

    # For tops/dual outfits, drop back-pose frames: we only have front-facing
    # garment images, so FASHN would render the front texture on the model's
    # back view. Keep at least one frame if every selected frame is a back view.
    _renders_tops = is_dual or category in ("tops", "one-pieces")
    if _renders_tops:
        non_back = [(s, o) for s, o in zip(selected, orientations) if o != "back"]
        if non_back:
            selected, orientations = (list(t) for t in zip(*non_back))
            print(f"  [back-filter] dropped back frames; {len(selected)} frame(s) remain")

    keyframes = save_keyframes(selected, keyframe_dir)
    print(f"    Saved {len(keyframes)} keyframes → {keyframe_dir}")

    t_selection = time.time() - t0

    # ------------------------------------------------------------------
    # Stage 3: Parallel — FASHN + LLM tips + music queue
    # ------------------------------------------------------------------
    if is_dual:
        garment_desc = (
            f"{_garment_name(garment_top)} + "
            f"{_garment_name(garment_bottom)}"
        )
    else:
        garment_desc = _garment_name(garment)  # type: ignore[arg-type]

    duration = slideshow_duration_secs(len(keyframes), frame_duration)

    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:

        # 3a. FASHN distributed across server_urls
        print(
            f"\n[3] Running FASHN on {len(keyframes)} frames "
            f"across {len(server_urls)} server(s)"
        )
        _progress("generate_tryon", "Running FASHN virtual try-on", 50)
        if is_dual:
            fashn_fut = pool.submit(
                run_fashn_dual_distributed,
                keyframes,
                garment_top,  # type: ignore[arg-type]
                garment_bottom,  # type: ignore[arg-type]
                photo_type_top,  # type: ignore[arg-type]
                photo_type_bottom,  # type: ignore[arg-type]
                server_urls,
                tryon_dir,
                timesteps,
                seed,
            )
        else:
            fashn_fut = pool.submit(
                run_fashn_distributed,
                keyframes,
                garment,  # type: ignore[arg-type]
                category,
                photo_type_single,  # type: ignore[arg-type]
                server_urls,
                tryon_dir,
                timesteps,
                seed,
            )

        # 3b. LLM tips (parallel with FASHN)
        print(f"\n[4] Generating tips via {llm_api_base} (model: {llm_model})")
        _progress("generate_tips", "Generating styling tips with Ollama", 65)
        tips_fut = pool.submit(
            generate_fashion_tips_llm,
            tips_category,
            len(keyframes),
            llm_api_key,
            llm_api_base,
            llm_model,
            garment_desc,
            garment_metadata,
        )

        tryon_paths = fashn_fut.result()
        t_fashn = time.time() - t0

        tips_by_frame: list[list[str]]
        tips_source = "llm"
        tips_by_frame = tips_fut.result()
        print(f"    Got {len(tips_by_frame)} tip sets from LLM")
        if unload_llm:
            _unload_ollama_model(llm_api_base, llm_model)

    music_task_id = None
    if not skip_music:
        print(f"\n[5] Queuing music via ACE-Step ({acestep_url})")
        _progress("generate_music", "Generating music with ACE-Step", 75)
        music_task_id = queue_music(
            tips_category,
            duration,
            acestep_url,
            acestep_key,
            music_prompt,
        )

    # ------------------------------------------------------------------
    # Stage 4: Assemble slideshow
    # ------------------------------------------------------------------
    print(
        f"\n[6] Assembling slideshow "
        f"({frame_duration}s/slide, 0.5s crossfade)"
    )
    _progress("assemble_slideshow", "Assembling try-on slideshow", 85)
    assemble_slideshow(
        tryon_paths,
        tips_category,
        tips_by_frame,
        frame_duration,
        fps,
        slideshow_raw,
        # Pass None so the full styled canvas (frame + side rails) is
        # encoded as-is.  Width is wider than source; height is unchanged.
        target_size=None,
    )
    print(f"    Raw slideshow: {slideshow_raw}")

    # ------------------------------------------------------------------
    # Stage 5: Overlay music
    # ------------------------------------------------------------------
    music_meta = {"status": "skipped", "path": None, "source": None}
    if not skip_music and music_task_id is not None:
        _progress("mux_music", "Muxing generated music into video", 92)
        music_path = poll_music(
            music_task_id,
            acestep_url,
            acestep_key,
            tmp_path=str(output_dir / "bgm.flac"),
        )
        if music_path is not None:
            add_music(slideshow_raw, music_path, slideshow_final)
            music_meta = {
                "status": "muxed",
                "path": music_path,
                "source": "acestep",
            }
            print(f"    Final: {slideshow_final}")
        else:
            shutil.copy(slideshow_raw, slideshow_final)
            music_meta = {"status": "timed_out", "path": None, "source": None}
            print("    Music timed out — using raw slideshow")
    else:
        shutil.copy(slideshow_raw, slideshow_final)
        if skip_music:
            print("\n[5] Music skipped")

    t_total = time.time() - t0
    _progress("done", "Try-on video ready", 100)

    # ------------------------------------------------------------------
    # Stage 6: Write metadata
    # ------------------------------------------------------------------
    meta = {
        "video": video,
        "garment": garment,
        "garment_top": garment_top,
        "garment_bottom": garment_bottom,
        "category": category,
        "is_dual": is_dual,
        "num_keyframes": len(keyframes),
        "keyframes": [str(p) for p in keyframes],
        "tryon_frames": [str(p) for p in tryon_paths],
        "slideshow": slideshow_final,
        "tips": tips_by_frame,
        "tips_source": tips_source,
        "music": music_meta,
        "fps": fps,
        "timesteps": timesteps,
        "seed": seed,
        "timing": {
            "selection_secs": round(t_selection, 1),
            "fashn_secs": round(t_fashn - t_selection, 1),
            "total_secs": round(t_total, 1),
        },
    }
    with open(output_dir / "metadata.json", "w") as fh:
        json.dump(meta, fh, indent=2)

    print("\n" + "=" * 60)
    print(f"DONE  {t_total:.1f}s  →  {slideshow_final}")
    print("=" * 60)
    return meta


# ---------------------------------------------------------------------------
# CLI entry-point
# ---------------------------------------------------------------------------


def _parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    p = argparse.ArgumentParser(
        description="Fashion slideshow pipeline (server-based FASHN)"
    )
    p.add_argument("--video", required=True)
    p.add_argument(
        "--garment",
        default=None,
        help="Single garment image (mutually exclusive with dual args).",
    )
    p.add_argument("--garment-top", default=None)
    p.add_argument("--garment-bottom", default=None)
    p.add_argument(
        "--category",
        default="tops",
        choices=["tops", "bottoms", "one-pieces"],
    )
    p.add_argument(
        "--server-urls",
        default=os.environ.get("FASHN_SERVER_URLS", "http://localhost:7860"),
        help="Comma-separated FASHN server base URLs.",
    )
    p.add_argument("--num-frames", type=int, default=8)
    p.add_argument("--candidate-frames", type=int, default=30)
    p.add_argument("--frame-duration", type=float, default=3.0)
    p.add_argument("--timesteps", type=int, default=20)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--skip-music", action="store_true")
    p.add_argument("--music-prompt", default=None)
    p.add_argument("--weights-dir", default=None)
    p.add_argument(
        "--output-dir",
        default="output/default",
        help="Output directory for this job.",
    )
    return p.parse_args()


def main() -> None:
    """CLI entry-point: parse args and run the pipeline."""
    args = _parse_args()

    is_dual = bool(args.garment_top and args.garment_bottom)
    if not args.garment and not is_dual:
        print(
            "ERROR: provide --garment, or both "
            "--garment-top and --garment-bottom",
            file=sys.stderr,
        )
        sys.exit(1)
    if args.garment and is_dual:
        print(
            "ERROR: --garment cannot be combined with "
            "--garment-top/--garment-bottom",
            file=sys.stderr,
        )
        sys.exit(1)

    server_urls = [u.strip() for u in args.server_urls.split(",") if u.strip()]
    for url in server_urls:
        wait_for_server(url)

    run_pipeline(
        video=args.video,
        garment=args.garment,
        garment_top=args.garment_top,
        garment_bottom=args.garment_bottom,
        category=args.category,
        server_urls=server_urls,
        output_dir=Path(args.output_dir),
        num_frames=args.num_frames,
        candidate_frames=args.candidate_frames,
        frame_duration=args.frame_duration,
        timesteps=args.timesteps,
        seed=args.seed,
        skip_music=args.skip_music,
        music_prompt=args.music_prompt,
        weights_dir=args.weights_dir,
        llm_api_base=os.environ.get(
            "LLM_API_BASE", "http://ollama-metrics:8080/v1"
        ),
        llm_model=os.environ.get("LLM_MODEL", "qwen3.6:27b"),
        llm_api_key=os.environ.get("LLM_API_KEY", ""),
        acestep_url=os.environ.get(
            "ACESTEP_URL", "http://localhost:8001"
        ).rstrip("/"),
        acestep_key=os.environ.get("ACESTEP_API_KEY") or None,
    )


if __name__ == "__main__":
    main()

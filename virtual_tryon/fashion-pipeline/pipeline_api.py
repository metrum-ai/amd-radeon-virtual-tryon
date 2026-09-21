# Copyright Advanced Micro Devices, Inc.
# 
# SPDX-License-Identifier: MIT

"""FastAPI service wrapping the fashion slideshow pipeline.

Runs permanently; accepts HTTP job requests and processes them
via a background worker queue (one job at a time when using both
servers together, or parallel when outfits are split across servers).

Endpoints:
    GET  /health              — liveness probe
    GET  /videos              — list input video files
    GET  /garments            — list garment images
    GET  /outputs             — list completed job directories
    POST /run                 — submit a single-outfit job
    POST /run-multi           — submit multiple outfits in parallel
    GET  /jobs/{job_id}       — status, keyframes, and output path
    GET  /jobs                — all known jobs
    GET  /outputs/{job_id}/slideshow.mp4  — download result
    GET  /inputs/{file_path}  — serve an input file for preview
    POST /upload/garment      — upload a garment image
"""

import json
import os
import shutil
import tempfile
import threading
import time
import traceback
import uuid
from pathlib import Path
from queue import Queue
from typing import Literal, Optional

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel
from run_pipeline import _unload_ollama_model, run_pipeline

INPUT_DIR = Path(os.environ.get("INPUT_DIR", "/app/input"))
DEMO_VIDEOS_DIR = Path(
    os.environ.get("DEMO_VIDEOS_INPUT_DIR", "/app/demo-videos")
)
OUTPUT_DIR = Path(os.environ.get("OUTPUT_DIR", "/app/output"))
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
# Writable directory for user-uploaded garment images (INPUT_DIR may be :ro)
UPLOAD_DIR = Path(
    os.environ.get("UPLOAD_DIR")
    or os.path.join(tempfile.gettempdir(), "pipeline-uploads")
)
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="Fashion Pipeline API")


@app.on_event("startup")
def _restore_jobs_from_disk() -> None:
    """Reload completed job state from session.json files on startup.

    This ensures that jobs completed before a container restart are still
    queryable via /jobs/{job_id} rather than returning 404.
    """
    restored = 0
    for session_file in OUTPUT_DIR.glob("*/session.json"):
        try:
            data = json.loads(session_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        job_id = data.get("job_id")
        if not job_id:
            continue
        slideshow = OUTPUT_DIR / job_id / "slideshow.mp4"
        with _lock:
            if job_id in _jobs:
                continue
            _jobs[job_id] = {
                "status": data.get("status", "done"),
                "stage": "done",
                "message": "Try-on video ready",
                "progress": 100,
                "session_id": data.get("session_id"),
                "input_video": data.get("input_video"),
                "input_garments": data.get("input_garments", []),
                "output": (
                    f"{job_id}/slideshow.mp4" if slideshow.exists() else None
                ),
                "error": None,
                "keyframes": [],
                "tryon_frames": [],
                "timing": {},
                "updated_at": data.get("updated_at", 0),
            }
        restored += 1
    if restored:
        print(f"[startup] Restored {restored} completed job(s) from disk")


_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp"}
_VIDEO_EXTS = {".mp4", ".mov", ".avi"}

_jobs: dict[str, dict] = {}
_queue: Queue = Queue()
_lock = threading.Lock()

# FASHN server URLs — two persistent warm servers, one per GPU
_SERVER_URLS = [
    u.strip()
    for u in os.environ.get(
        "FASHN_SERVER_URLS",
        "http://fashn0:7860,http://fashn1:7860",
    ).split(",")
    if u.strip()
]

_WEIGHTS_DIR = os.environ.get("WEIGHTS_DIR", "/app/weights")
_LLM_API_BASE = os.environ.get("LLM_API_BASE", "http://ollama-metrics:8080/v1")
_LLM_MODEL = os.environ.get("LLM_MODEL", "qwen3.6:27b")
_LLM_API_KEY = os.environ.get("LLM_API_KEY", "")
_ACESTEP_URL = os.environ.get("ACESTEP_URL", "http://acestep:8001").rstrip("/")
_ACESTEP_KEY = os.environ.get("ACESTEP_API_KEY") or None


def _write_job_metadata(job_id: str, info: dict) -> None:
    """Persist session metadata beside an output directory.

    Called both at job start (so the dir is trackable even if the job
    fails) and again on completion (to capture the final status/output).
    """
    if not info.get("session_id"):
        return
    metadata = {
        "job_id": job_id,
        "session_id": info.get("session_id"),
        "status": info.get("status"),
        "output": info.get("output"),
        "input_video": info.get("input_video"),
        "input_garments": info.get("input_garments", []),
        "updated_at": info.get("updated_at", time.time()),
    }
    path = OUTPUT_DIR / job_id / "session.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(metadata), encoding="utf-8")


def _set_job_progress(
    job_id: str,
    stage: str,
    message: str,
    progress: int,
) -> None:
    """Update a job with stage-level progress for UI polling."""
    with _lock:
        if job_id in _jobs:
            _jobs[job_id].update(
                stage=stage,
                message=message,
                progress=progress,
                updated_at=time.time(),
            )


def _input_path(filename: str) -> Path:
    """Return a safe path inside the mounted input directory.

    Args:
        filename: Relative input path, optionally including subdirectories.

    Returns:
        Resolved absolute input path.

    Raises:
        HTTPException: 400 if the path escapes the input directory.

    """
    if filename.startswith("demo-videos/"):
        root = DEMO_VIDEOS_DIR.resolve()
        candidate = (
            DEMO_VIDEOS_DIR / filename.removeprefix("demo-videos/")
        ).resolve()
    else:
        # Check UPLOAD_DIR first (user-uploaded garments, writable)
        upload_candidate = (UPLOAD_DIR / filename).resolve()
        try:
            upload_candidate.relative_to(UPLOAD_DIR.resolve())
            if upload_candidate.exists():
                return upload_candidate
        except ValueError:
            pass
        root = INPUT_DIR.resolve()
        candidate = (INPUT_DIR / filename).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise HTTPException(
            status_code=400, detail=f"Invalid input path: {filename}"
        ) from exc
    return candidate


def _list_inputs(extensions: set[str]) -> list[str]:
    """Return sorted relative input paths matching the allowed extensions."""
    items = []
    if INPUT_DIR.exists():
        items.extend(
            f.relative_to(INPUT_DIR).as_posix()
            for f in INPUT_DIR.rglob("*")
            if f.is_file() and f.suffix.lower() in extensions
        )
    if DEMO_VIDEOS_DIR.exists():
        items.extend(
            "demo-videos/" + f.relative_to(DEMO_VIDEOS_DIR).as_posix()
            for f in DEMO_VIDEOS_DIR.rglob("*")
            if f.is_file() and f.suffix.lower() in extensions
        )
    return sorted(items)


# ---------------------------------------------------------------------------
# Serial background worker (one job uses all servers together)
# ---------------------------------------------------------------------------


def _worker() -> None:
    """Processes queued pipeline jobs serially."""
    while True:
        job_id, req = _queue.get()
        with _lock:
            _jobs[job_id].update(
                status="running",
                stage="queued",
                message="Pipeline worker started",
                progress=5,
                updated_at=time.time(),
            )
        try:
            _execute(job_id, req)
        except Exception as exc:  # pylint: disable=broad-except
            # The worker is the single serial queue processor; if it dies the
            # whole pipeline wedges. Never let an unexpected error escape —
            # mark the job failed and keep draining the queue.
            tb = traceback.format_exc()
            # Print the full traceback so `docker compose logs pipeline` shows the real failure point.
            print(f"[worker] job {job_id} failed:\n{tb}")
            detail = f"{type(exc).__name__}: {exc}"
            with _lock:
                if job_id in _jobs:
                    _jobs[job_id].update(
                        status="failed",
                        stage="failed",
                        message=detail,
                        error=detail,
                        traceback=tb[-4000:],
                        updated_at=time.time(),
                    )
        finally:
            _queue.task_done()


threading.Thread(target=_worker, daemon=True).start()


def _execute(job_id: str, req: dict) -> None:
    """Run the pipeline for one job and update job state.

    Args:
        job_id: Unique job identifier.
        req: Validated request parameters as a plain dict.

    """

    def _e(key: str, env_key: str, default: str) -> str:
        v = req.get(key)
        return str(v) if v is not None else os.environ.get(env_key, default)

    out_dir = OUTPUT_DIR / job_id
    is_dual = bool(
        req.get("input_garment_top") and req.get("input_garment_bottom")
    )
    category = req.get("garment_category") or "tops"

    try:
        # Write session.json now so the dir is trackable even if the job
        # fails. Kept inside the try so a failure here (e.g. an unwritable
        # output dir) marks the job failed instead of escaping the worker.
        with _lock:
            _write_job_metadata(job_id, dict(_jobs[job_id]))

        meta = run_pipeline(
            video=str(_input_path(req["input_video"])),
            garment=(
                str(_input_path(req["input_garment"]))
                if req.get("input_garment")
                else None
            ),
            garment_top=(
                str(_input_path(req["input_garment_top"]))
                if req.get("input_garment_top")
                else None
            ),
            garment_bottom=(
                str(_input_path(req["input_garment_bottom"]))
                if req.get("input_garment_bottom")
                else None
            ),
            category=category,
            server_urls=_SERVER_URLS,
            output_dir=out_dir,
            num_frames=int(_e("num_frames", "NUM_FRAMES", "8")),
            candidate_frames=int(
                _e("candidate_frames", "CANDIDATE_FRAMES", "30")
            ),
            frame_duration=float(_e("frame_duration", "FRAME_DURATION", "3.0")),
            timesteps=int(_e("timesteps", "TIMESTEPS", "20")),
            seed=int(_e("seed", "SEED", "42")),
            skip_music=bool(req.get("skip_music", False)),
            music_prompt=req.get("music_prompt") or None,
            weights_dir=_WEIGHTS_DIR,
            llm_api_base=_LLM_API_BASE,
            llm_model=_LLM_MODEL,
            llm_api_key=_LLM_API_KEY,
            garment_metadata=str(req.get("styling_context") or ""),
            acestep_url=_ACESTEP_URL,
            acestep_key=_ACESTEP_KEY,
            progress_callback=lambda stage, message, progress: (
                _set_job_progress(job_id, stage, message, progress)
            ),
        )
        with _lock:
            _jobs[job_id].update(
                status="done",
                stage="done",
                message="Try-on video ready",
                progress=100,
                output=f"{job_id}/slideshow.mp4",
                keyframes=meta.get("keyframes", []),
                tryon_frames=meta.get("tryon_frames", []),
                tips=meta.get("tips", []),
                tips_source=meta.get("tips_source"),
                music=meta.get("music", {}),
                timing=meta.get("timing", {}),
                updated_at=time.time(),
            )
            job_info = dict(_jobs[job_id])
        _write_job_metadata(job_id, job_info)
    except Exception as exc:  # pylint: disable=broad-except
        # This handler wraps the whole run_pipeline() call, so it's the one that
        # actually fires for real pipeline failures.
        tb = traceback.format_exc()
        print(f"[_execute] job {job_id} failed:\n{tb}")
        detail = f"{type(exc).__name__}: {exc}"
        with _lock:
            _jobs[job_id].update(
                status="failed",
                stage="failed",
                message=detail,
                error=detail,
                traceback=tb[-4000:],
                updated_at=time.time(),
            )


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class RunRequest(BaseModel):
    """Parameters for a single-outfit pipeline run."""

    session_id: Optional[str] = None
    input_video: str
    input_garment: Optional[str] = None
    input_garment_top: Optional[str] = None
    input_garment_bottom: Optional[str] = None
    # Single-garment category; must match the FASHN/CLI accepted set. In
    # dual-garment mode this field is ignored (the pipeline applies bottoms
    # then tops internally), but it is still constrained to a known value.
    garment_category: Literal["tops", "bottoms", "one-pieces"] = "tops"
    styling_context: Optional[str] = None
    num_frames: Optional[int] = None
    candidate_frames: Optional[int] = None
    frame_duration: Optional[float] = None
    timesteps: Optional[int] = None
    seed: Optional[int] = None
    skip_music: bool = False
    music_prompt: Optional[str] = None


class OutfitPair(BaseModel):
    """A single top+bottom outfit for multi-outfit runs."""

    garment_top: str
    garment_bottom: str


class RunMultiRequest(BaseModel):
    """Parameters for a multi-outfit parallel run."""

    session_id: Optional[str] = None
    input_video: str
    outfits: list[OutfitPair]
    num_frames: Optional[int] = None
    candidate_frames: Optional[int] = None
    frame_duration: Optional[float] = None
    timesteps: Optional[int] = None
    seed: Optional[int] = None
    skip_music: bool = False
    music_prompt: Optional[str] = None


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@app.get("/health")
def health() -> dict:
    """Liveness probe."""
    return {"status": "ok"}


@app.get("/videos")
def list_videos() -> list[str]:
    """Return video filenames found in the input directory."""
    return _list_inputs(_VIDEO_EXTS)


@app.get("/garments")
def list_garments() -> list[str]:
    """Return garment image filenames found in the input directory."""
    return _list_inputs(_IMAGE_EXTS)


@app.get("/outputs")
def list_outputs() -> list[str]:
    """Return completed job IDs (directories containing slideshow.mp4)."""
    return sorted(
        d.name
        for d in OUTPUT_DIR.iterdir()
        if d.is_dir() and (d / "slideshow.mp4").exists()
    )


@app.get("/outputs/{job_id}/slideshow.mp4")
def download_output(job_id: str) -> FileResponse:
    """Stream a completed slideshow video.

    Args:
        job_id: Job identifier returned by /run or /run-multi.

    Raises:
        HTTPException: 404 if the output does not exist.

    """
    path = OUTPUT_DIR / job_id / "slideshow.mp4"
    if not path.exists():
        raise HTTPException(status_code=404, detail="Output not found")
    return FileResponse(
        path, media_type="video/mp4", filename=f"{job_id}_slideshow.mp4"
    )


@app.get("/outputs/{job_id}/keyframes/preview")
def download_keyframe_preview(job_id: str) -> FileResponse:
    """Stream the first try-on frame for a completed try-on job.

    Args:
        job_id: Job identifier returned by /run or /run-multi.

    Raises:
        HTTPException: 404 if no preview image exists for the job.

    """
    candidates = []
    for dirname in ("tryon", "keyframes"):
        preview_dir = OUTPUT_DIR / job_id / dirname
        if not preview_dir.exists():
            continue
        candidates = sorted(
            path
            for path in preview_dir.iterdir()
            if path.is_file() and path.suffix.lower() in _IMAGE_EXTS
        )
        if candidates:
            break
    if not candidates:
        raise HTTPException(status_code=404, detail="Preview image not found")
    path = candidates[0]
    media_type = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
    return FileResponse(
        path, media_type=media_type, filename=f"{job_id}_preview{path.suffix}"
    )


@app.get("/inputs/{file_path:path}")
def serve_input_file(file_path: str) -> FileResponse:
    """Serve an input file for browser preview.

    Args:
        file_path: Relative file path in the input directory.

    Raises:
        HTTPException: 404 if the file does not exist.

    """
    path = _input_path(file_path)
    if not path.exists():
        raise HTTPException(status_code=404, detail="Input file not found")
    suffix = path.suffix.lower()
    media_type = (
        "video/mp4"
        if suffix == ".mp4"
        else (
            "video/quicktime"
            if suffix == ".mov"
            else "image/png" if suffix == ".png" else "image/jpeg"
        )
    )
    return FileResponse(path, media_type=media_type)


@app.post("/upload/garment")
async def upload_garment(file: UploadFile = File(...)) -> dict:
    """Save an uploaded garment image to the input directory.

    Args:
        file: The uploaded image file.

    Returns:
        Dict with filename and status.

    Raises:
        HTTPException: 400 for unsupported file types.

    """
    allowed = {".png", ".jpg", ".jpeg", ".webp"}
    # Strip any directory components first so a missing/odd filename cannot
    # raise before the friendly 400, and the saved name matches the response.
    filename = Path(file.filename or "").name
    if not filename:
        raise HTTPException(status_code=400, detail="Missing filename")
    suffix = Path(filename).suffix.lower()
    if suffix not in allowed:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported type {suffix}. Allowed: {allowed}",
        )
    dest = UPLOAD_DIR / filename
    with dest.open("wb") as out:
        shutil.copyfileobj(file.file, out)
    return {"filename": filename, "status": "uploaded"}


@app.post("/run")
def run_job(req: RunRequest) -> dict:
    """Enqueue a single-outfit pipeline job.

    Args:
        req: Job parameters.

    Returns:
        Dict with job_id, status, and queue position.

    Raises:
        HTTPException: 400 for missing or invalid inputs.

    """
    _validate_video(req.input_video)
    is_dual = bool(req.input_garment_top and req.input_garment_bottom)
    has_single = bool(req.input_garment)
    if not has_single and not is_dual:
        raise HTTPException(
            status_code=400,
            detail="Provide input_garment or both "
            "input_garment_top + input_garment_bottom.",
        )
    if has_single and is_dual:
        raise HTTPException(
            status_code=400,
            detail="input_garment cannot be combined with "
            "input_garment_top/input_garment_bottom.",
        )
    garment_files = (
        [req.input_garment_top, req.input_garment_bottom]
        if is_dual
        else [req.input_garment]
    )
    for fname in garment_files:
        _validate_garment(fname)

    job_id = uuid.uuid4().hex[:10]
    with _lock:
        _jobs[job_id] = {
            "status": "queued",
            "stage": "queued",
            "message": "Waiting for pipeline worker",
            "progress": 0,
            "session_id": req.session_id,
            "input_video": req.input_video,
            "input_garments": garment_files,
            "output": None,
            "error": None,
            "keyframes": [],
            "tryon_frames": [],
            "timing": {},
            "updated_at": time.time(),
        }
    _queue.put((job_id, req.model_dump()))
    return {
        "job_id": job_id,
        "status": "queued",
        "queue_position": _queue.qsize(),
    }


@app.post("/run-multi")
def run_multi(req: RunMultiRequest) -> dict:
    """Run multiple outfits in parallel, one GPU server per outfit.

    Each outfit is assigned a dedicated FASHN server, allowing true
    parallel execution within the 90-second per-job budget.  If there
    are more outfits than servers, the extra outfits share server slots
    (round-robin); only as many outfits run fully in parallel as there
    are servers.

    Args:
        req: Multi-outfit parameters.

    Returns:
        Dict mapping outfit index to job_id, status, and queue info.

    """
    _validate_video(req.input_video)
    if not req.outfits:
        raise HTTPException(
            status_code=400, detail="outfits list cannot be empty."
        )
    for outfit in req.outfits:
        _validate_garment(outfit.garment_top)
        _validate_garment(outfit.garment_bottom)

    results: dict[str, dict] = {}

    def _spawn_outfit(idx: int, outfit: OutfitPair) -> None:
        srv_url = _SERVER_URLS[idx % len(_SERVER_URLS)]
        job_id = uuid.uuid4().hex[:10]
        with _lock:
            _jobs[job_id] = {
                "status": "running",
                "stage": "queued",
                "message": "Starting outfit pipeline",
                "progress": 5,
                "session_id": req.session_id,
                "input_video": req.input_video,
                "input_garments": [
                    outfit.garment_top,
                    outfit.garment_bottom,
                ],
                "outfit_index": idx,
                "output": None,
                "error": None,
                "keyframes": [],
                "tryon_frames": [],
                "timing": {},
                "updated_at": time.time(),
            }
        out_dir = OUTPUT_DIR / job_id
        with _lock:
            _write_job_metadata(job_id, dict(_jobs[job_id]))
        try:
            meta = run_pipeline(
                video=str(_input_path(req.input_video)),
                garment=None,
                garment_top=str(_input_path(outfit.garment_top)),
                garment_bottom=str(_input_path(outfit.garment_bottom)),
                category="both",
                server_urls=[srv_url],
                output_dir=out_dir,
                num_frames=req.num_frames
                or int(os.environ.get("NUM_FRAMES", "8")),
                candidate_frames=req.candidate_frames
                or int(os.environ.get("CANDIDATE_FRAMES", "30")),
                frame_duration=req.frame_duration
                or float(os.environ.get("FRAME_DURATION", "3.0")),
                timesteps=req.timesteps
                or int(os.environ.get("TIMESTEPS", "20")),
                seed=req.seed or int(os.environ.get("SEED", "42")),
                skip_music=req.skip_music,
                music_prompt=req.music_prompt or None,
                weights_dir=_WEIGHTS_DIR,
                llm_api_base=_LLM_API_BASE,
                llm_model=_LLM_MODEL,
                llm_api_key=_LLM_API_KEY,
                acestep_url=_ACESTEP_URL,
                acestep_key=_ACESTEP_KEY,
                progress_callback=lambda stage, message, progress: (
                    _set_job_progress(job_id, stage, message, progress)
                ),
                # Do not unload per-outfit: parallel sibling threads share
                # the same Ollama instance and a premature unload races
                # against their in-flight tips requests.  The caller unloads
                # once after all outfits are done.
                unload_llm=False,
            )
            with _lock:
                _jobs[job_id].update(
                    status="done",
                    stage="done",
                    message="Try-on video ready",
                    progress=100,
                    output=f"{job_id}/slideshow.mp4",
                    keyframes=meta.get("keyframes", []),
                    tryon_frames=meta.get("tryon_frames", []),
                    tips=meta.get("tips", []),
                    tips_source=meta.get("tips_source"),
                    music=meta.get("music", {}),
                    timing=meta.get("timing", {}),
                    updated_at=time.time(),
                )
                job_info = dict(_jobs[job_id])
            _write_job_metadata(job_id, job_info)
        except Exception as exc:  # pylint: disable=broad-except
            tb = traceback.format_exc()
            print(f"[_spawn_outfit] job {job_id} failed:\n{tb}")
            detail = f"{type(exc).__name__}: {exc}"
            with _lock:
                _jobs[job_id].update(
                    status="failed",
                    stage="failed",
                    message=detail,
                    error=detail,
                    traceback=tb[-4000:],
                    updated_at=time.time(),
                )
        results[str(idx)] = {"job_id": job_id}

    import concurrent.futures  # pylint: disable=import-outside-toplevel

    with concurrent.futures.ThreadPoolExecutor(
        max_workers=min(len(req.outfits), len(_SERVER_URLS))
    ) as pool:
        futs = [
            pool.submit(_spawn_outfit, i, outfit)
            for i, outfit in enumerate(req.outfits)
        ]
        for fut in futs:
            fut.result()

    # All outfits done — unload once so ACE-Step music tasks on GPU 3 are
    # not blocked by Ollama's residual VRAM allocation.
    _unload_ollama_model(_LLM_API_BASE, _LLM_MODEL)

    return {
        "total_outfits": len(req.outfits),
        "jobs": {
            k: {
                "job_id": v["job_id"],
                "status": _jobs[v["job_id"]]["status"],
                "output": _jobs[v["job_id"]].get("output"),
            }
            for k, v in results.items()
        },
    }


@app.get("/jobs/{job_id}")
def job_status(job_id: str) -> dict:
    """Return status and result of a job.

    Args:
        job_id: Job identifier returned by /run or /run-multi.

    Raises:
        HTTPException: 404 if job_id is unknown.

    """
    if job_id not in _jobs:
        raise HTTPException(status_code=404, detail="Job not found")
    return _jobs[job_id]


@app.get("/jobs")
def list_jobs() -> dict:
    """Return all known jobs and their statuses."""
    return {
        jid: {
            "status": info["status"],
            "stage": info.get("stage"),
            "message": info.get("message"),
            "progress": info.get("progress", 0),
            "session_id": info.get("session_id"),
            "output": info.get("output"),
            "timing": info.get("timing", {}),
        }
        for jid, info in _jobs.items()
    }


@app.get("/sessions/{session_id}/jobs")
def list_session_jobs(session_id: str) -> list[dict]:
    """Return completed pipeline jobs generated in a UI session."""
    with _lock:
        items = [
            {
                "job_id": jid,
                "status": info.get("status"),
                "output": info.get("output"),
                "input_video": info.get("input_video"),
                "input_garments": info.get("input_garments", []),
                "updated_at": info.get("updated_at", 0),
            }
            for jid, info in _jobs.items()
            if info.get("session_id") == session_id
            and info.get("status") == "done"
            and info.get("output")
        ]
    seen = {item["job_id"] for item in items}
    for path in OUTPUT_DIR.glob("*/session.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        job_id = data.get("job_id")
        if (
            data.get("session_id") == session_id
            and job_id
            and job_id not in seen
            and (OUTPUT_DIR / job_id / "slideshow.mp4").exists()
        ):
            items.append(data)
    return sorted(items, key=lambda item: item["updated_at"], reverse=True)


@app.delete("/sessions/{session_id}/outputs")
def delete_session_outputs(session_id: str) -> dict:
    """Delete pipeline outputs generated by a UI session."""
    removed = []
    output_root = OUTPUT_DIR.resolve()
    with _lock:
        job_ids = [
            jid
            for jid, info in _jobs.items()
            if info.get("session_id") == session_id
        ]
    for path in OUTPUT_DIR.glob("*/session.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        job_id = data.get("job_id")
        if data.get("session_id") == session_id and job_id not in job_ids:
            job_ids.append(job_id)
    for job_id in job_ids:
        path = (OUTPUT_DIR / job_id).resolve()
        try:
            path.relative_to(output_root)
        except ValueError as exc:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid output path for job: {job_id}",
            ) from exc
        if path.exists():
            shutil.rmtree(path)
            removed.append(job_id)
    with _lock:
        for job_id in job_ids:
            _jobs.pop(job_id, None)
        active_job_ids = set(_jobs)

    # Sweep orphaned dirs: no session.json and not an active/queued job.
    # These are left over from old server runs or pre-tracking job attempts.
    for orphan_dir in OUTPUT_DIR.iterdir():
        if not orphan_dir.is_dir():
            continue
        orphan_id = orphan_dir.name
        if (
            orphan_id in active_job_ids
            or (orphan_dir / "session.json").exists()
        ):
            continue
        resolved = orphan_dir.resolve()
        try:
            resolved.relative_to(output_root)
        except ValueError:
            continue
        shutil.rmtree(resolved)
        removed.append(f"orphan:{orphan_id}")

    return {"session_id": session_id, "removed": removed}


# ---------------------------------------------------------------------------
# Input validation helpers
# ---------------------------------------------------------------------------


def _validate_video(filename: str) -> None:
    """Raise HTTPException 400 if the video file does not exist."""
    if not _input_path(filename).exists():
        raise HTTPException(
            status_code=400,
            detail=f"Video not found: {filename}. "
            f"Available: {list_videos()}",
        )


def _validate_garment(filename: str | None) -> None:
    """Raise HTTPException 400 if the garment file does not exist."""
    if filename and not _input_path(filename).exists():
        raise HTTPException(
            status_code=400,
            detail=f"Garment not found: {filename}. "
            f"Available: {list_garments()}",
        )

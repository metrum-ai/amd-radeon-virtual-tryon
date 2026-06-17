# Copyright Advanced Micro Devices, Inc.
# 
# SPDX-License-Identifier: MIT

"""ACE-Step music generation client.

Queues a music generation task, polls for completion, downloads
the result, and overlays it on a video via ffmpeg.
"""

import json
import os
import socket
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from urllib.parse import urljoin, urlparse

def _assert_http_url(url: str) -> None:
    """Raise ValueError if *url* does not use http or https."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(
            f"URL must use http or https scheme, got {parsed.scheme!r}: {url}"
        )


MUSIC_PROMPTS: dict[str, str] = {
    "tops": (
        "upbeat fashion runway show, electronic pop, trendy and energetic, "
        "modern synthesizers, 120 BPM, no vocals"
    ),
    "bottoms": (
        "chic boutique ambience, smooth jazz fusion, stylish and "
        "sophisticated, light percussion, 100 BPM, no vocals"
    ),
    "one-pieces": (
        "elegant fashion gala, orchestral pop, glamorous and powerful, "
        "cinematic strings, 110 BPM, no vocals"
    ),
    "both": (
        "modern fashion editorial, indie pop fusion, stylish and confident, "
        "rhythmic beat, 115 BPM, no vocals"
    ),
}


def _http_post_json(
    url: str, payload: dict, api_key: str | None = None
) -> dict:
    """POST JSON payload and return parsed response dict.

    Args:
        url: Full request URL.
        payload: Dict to serialize as JSON body.
        api_key: Optional Bearer token.

    Returns:
        Parsed response dict.

    """
    _assert_http_url(url)
    data = json.dumps(payload).encode()
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=30) as resp:  # nosec B310
        return json.loads(resp.read())


def queue_music(
    category: str,
    duration_secs: int,
    acestep_url: str,
    api_key: str | None = None,
    custom_prompt: str | None = None,
) -> str:
    """Submit a music generation task to ACE-Step and return task_id.

    Args:
        category: Garment category for prompt selection.
        duration_secs: Target audio duration in seconds.
        acestep_url: Base URL of the ACE-Step server.
        api_key: Optional ACE-Step API key.
        custom_prompt: Override the default category prompt.

    Returns:
        Task ID string for polling.

    """
    prompt = custom_prompt or MUSIC_PROMPTS.get(
        category,
        "upbeat modern fashion music, electronic, stylish, no vocals",
    )
    print(f"  ACE-Step prompt: {prompt[:70]}...")
    task_payload: dict = {
        "prompt": prompt,
        "audio_duration": duration_secs,
        "model": "acestep-v15-turbo",
        "audio_format": "flac",
        "inference_steps": 8,
        "thinking": True,
    }
    if api_key:
        task_payload["ai_token"] = api_key
    resp = _http_post_json(f"{acestep_url}/release_task", task_payload, api_key)
    task_id = resp["data"]["task_id"]
    print(f"  Task {task_id} queued")
    return task_id


def poll_music(
    task_id: str,
    acestep_url: str,
    api_key: str | None = None,
    tmp_path: str | None = None,
    poll_interval: int = 5,
    max_polls: int = 120,
) -> str | None:
    """Poll ACE-Step for a completed task and return the local audio path.

    Returns ``None`` on timeout or task failure so the caller can fall
    back to the raw slideshow without music rather than blocking.

    Args:
        task_id: Task ID returned by ``queue_music``.
        acestep_url: Base URL of the ACE-Step server.
        api_key: Optional ACE-Step API key.
        tmp_path: Local path to write the downloaded audio.  If ``None``,
            a secure temporary file is created.
        poll_interval: Seconds between polls.
        max_polls: Maximum polls before giving up (default 120 × 5s = 10 min).

    Returns:
        Path to downloaded audio file, or ``None`` if timed out / failed.

    """
    if tmp_path is None:
        fd, tmp_path = tempfile.mkstemp(suffix=".flac", prefix="fashion_bgm_")
        os.close(fd)
    print(f"  Polling ACE-Step every {poll_interval}s (max {max_polls}) ...")
    audio_url = ""
    for _ in range(max_polls):
        time.sleep(poll_interval)
        try:
            resp = _http_post_json(
                f"{acestep_url}/query_result",
                {"task_id_list": [task_id]},
                api_key,
            )
        except (
            urllib.error.URLError,
            urllib.error.HTTPError,
            TimeoutError,
            socket.timeout,
        ) as exc:
            print(f"  ACE-Step poll error: {exc}")
            continue
        item = resp["data"][0]
        if item["status"] == 1:
            inner = item.get("result", {})
            if isinstance(inner, str):
                inner = json.loads(inner)[0]
            audio_url = (
                inner.get("first_audio_path")
                or inner.get("file")
                or inner.get("wave")
                or ""
            )
            break
        if item["status"] == 2:
            print(f"  ACE-Step task {task_id} failed — skipping music")
            return None
    else:
        print(
            f"  ACE-Step timed out after {max_polls * poll_interval}s"
            " — skipping music"
        )
        return None

    if not audio_url:
        print("  ACE-Step returned no audio URL — skipping music")
        return None

    parsed_audio = urlparse(audio_url)
    parsed_base = urlparse(acestep_url)
    if parsed_audio.scheme or parsed_audio.netloc:
        # Absolute URL — validate it belongs to the expected ACE-Step host
        if parsed_audio.netloc != parsed_base.netloc:
            print(
                "  ACE-Step returned unexpected audio host "
                f"{parsed_audio.netloc} (expected {parsed_base.netloc})"
                " — skipping music"
            )
            return None
        full_url = audio_url
    else:
        # Relative path — resolve safely against the base ACE-Step URL
        full_url = urljoin(acestep_url, audio_url)
    _assert_http_url(full_url)
    print(f"  Downloading music from {full_url}")
    try:
        req = urllib.request.Request(full_url)
        if api_key:
            req.add_header("Authorization", f"Bearer {api_key}")
        with (
            urllib.request.urlopen(req, timeout=60) as r,  # nosec B310
            open(tmp_path, "wb") as f,
        ):
            f.write(r.read())
    except Exception as exc:  # pylint: disable=broad-except
        print(f"  Music download failed: {exc} — skipping music")
        return None
    return tmp_path


def add_music(video_path: str, music_path: str, out_path: str) -> None:
    """Mux background audio onto a video with ffmpeg.

    Args:
        video_path: Input video file path.
        music_path: Audio file path (any ffmpeg-compatible format).
        out_path: Output MP4 file path.

    Raises:
        RuntimeError: If ffmpeg exits with a non-zero code.

    """
    result = subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-i",
            video_path,
            "-i",
            music_path,
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-shortest",
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            out_path,
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print(f"  ffmpeg stderr:\n{result.stderr[-600:]}")
        raise RuntimeError("ffmpeg music overlay failed")

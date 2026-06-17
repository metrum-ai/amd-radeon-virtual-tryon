# Copyright Advanced Micro Devices, Inc.
# 
# SPDX-License-Identifier: MIT

"""LLM-based fashion styling tips.

Calls an OpenAI-compatible endpoint to generate slide-level styling tips.
"""

import json
import re
import threading
import urllib.error
import urllib.request
from urllib.parse import urlparse

_LLM_REQUEST_LOCK = threading.Lock()


def _assert_http_url(url: str) -> None:
    """Raise ValueError if *url* does not use http or https."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(
            f"URL must use http or https scheme, got {parsed.scheme!r}: {url}"
        )


def _extract_json(text: str) -> dict:
    """Strip <think> blocks then parse first JSON object.

    Args:
        text: Raw LLM response text.

    Returns:
        Parsed JSON dict.

    Raises:
        ValueError: If no JSON object is found.

    """
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
    start = text.find("{")
    end = text.rfind("}") + 1
    if start == -1 or end == 0:
        raise ValueError(f"No JSON object in response: {text[:300]!r}")
    return json.loads(text[start:end])


def generate_fashion_tips_llm(
    category: str,
    num_frames: int,
    api_key: str,
    api_base: str,
    model: str,
    garment_desc: str = "",
    garment_metadata: str = "",
) -> list[list[str]]:
    """Call an OpenAI-compatible endpoint to generate styling tips.

    Args:
        category: Garment category (tops/bottoms/one-pieces/both).
        num_frames: Number of tip sets to generate (one per slide).
        api_key: Bearer token; may be empty for unauthenticated servers.
        api_base: Base URL of the OpenAI-compatible API.
        model: Model name to request.
        garment_desc: Human-readable garment description from filenames.
        garment_metadata: Structured garment metadata from catalog fields.

    Returns:
        List of ``num_frames`` tip sets, each a list with one string.

    Raises:
        Exception: On HTTP or parsing error (caller should handle).

    """
    garment_line = (
        f'The garment(s) in question: "{garment_desc}".\n'
        if garment_desc
        else ""
    )
    metadata_line = (
        f'Known garment metadata: "{garment_metadata}".\n'
        if garment_metadata
        else ""
    )
    category_label = {
        "tops": "a top garment",
        "bottoms": "a bottom garment",
        "one-pieces": "a one-piece outfit",
        "both": "a complete top + bottom outfit",
    }.get(category, f"a {category} garment")

    prompt = (
        "/no_think\n"
        "You are a senior editorial fashion stylist writing one-line "
        "captions for a retail virtual try-on video.\n"
        f"{garment_line}"
        f"{metadata_line}"
        f"Generate {num_frames} unique styling tips for a person "
        f"wearing {category_label}.\n\n"
        "Rules:\n"
        "- Each tip must be 4–7 words (concise and punchy).\n"
        "- Tips must be SPECIFIC to this garment's colour, texture, "
        "or silhouette.\n"
        "- Vary the category of advice across: layering, pairing, "
        "color, accessories, occasion, texture, fit.\n"
        "- No duplicate tips. Each must be distinct.\n\n"
        "Return ONLY valid JSON (no explanation): "
        '{"tips": ["tip1", "tip2", ...]}'
    )

    headers: dict[str, str] = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    if "ollama" in api_base:
        base = api_base.rstrip("/")
        if base.endswith("/v1"):
            base = base[:-3]
        payload = json.dumps(
            {
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "format": "json",
                "options": {
                    "num_predict": 256,
                    "temperature": 0.2,
                },
                "stream": False,
                "think": False,
            }
        ).encode()
        _assert_http_url(base)
        req = urllib.request.Request(
            base + "/api/chat",
            data=payload,
            headers=headers,
            method="POST",
        )
        with _LLM_REQUEST_LOCK:
            with urllib.request.urlopen(req, timeout=120) as resp:  # nosec B310
                result = json.loads(resp.read())
        content = result["message"]["content"]
    else:
        payload = json.dumps(
            {
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "chat_template_kwargs": {"enable_thinking": False},
                "max_tokens": 256,
                "response_format": {"type": "json_object"},
                "temperature": 0.2,
            }
        ).encode()
        url = api_base.rstrip("/") + "/chat/completions"
        _assert_http_url(url)
        req = urllib.request.Request(
            url, data=payload, headers=headers, method="POST"
        )
        with _LLM_REQUEST_LOCK:
            with urllib.request.urlopen(req, timeout=120) as resp:  # nosec B310
                result = json.loads(resp.read())
        content = result["choices"][0]["message"]["content"]
    raw_tips = _extract_json(content)["tips"]

    tips: list[list[str]] = []
    for item in raw_tips:
        if isinstance(item, str):
            tips.append([item])
        elif isinstance(item, list):
            for sub in item:
                tips.append([str(sub)])
        else:
            tips.append([str(item)])

    original_count = max(1, len(tips))
    while len(tips) < num_frames:
        tips.append(tips[len(tips) % original_count])
    return tips[:num_frames]

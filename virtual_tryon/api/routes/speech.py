# Copyright Advanced Micro Devices, Inc.
# 
# SPDX-License-Identifier: MIT

"""Text-to-speech proxy for agent responses."""

from __future__ import annotations

import os
import re
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel, Field

router = APIRouter(prefix="/api/v1/vto/agent", tags=["agent"])

_TTS_MODEL = "kokoro-v1"
_TTS_VOICE = "af_bella"
_DECORATIVE_SYMBOL_RE = re.compile("[\U0001f000-\U0001faff\u2600-\u27bf\ufe0f]")
_MARKDOWN_CODE_BLOCK_RE = re.compile(r"```[\s\S]*?```")
_MARKDOWN_SYMBOL_RE = re.compile(r"[*_`#>-]")
_WHITESPACE_RE = re.compile(r"\s+")


class SpeechRequest(BaseModel):
    """Request body for agent response speech synthesis."""

    input: str = Field(min_length=1, max_length=4000)
    voice: str = _TTS_VOICE
    language: str = "English"
    response_format: str = "wav"
    speed: float = Field(default=1.25, ge=0.25, le=4.0)
    instructions: str | None = None


@router.get("/voices")
async def tts_voices() -> dict[str, Any]:
    """List voices exposed by the configured TTS service.

    Returns:
        The upstream voices response, or a default voice fallback when the
        TTS service does not expose a voices endpoint.

    Raises:
        HTTPException: If the TTS service is unavailable.

    """
    try:
        async with httpx.AsyncClient(timeout=_tts_timeout()) as client:
            response = await client.get(_tts_url("/audio/voices"))
        if response.status_code == httpx.codes.NOT_FOUND:
            return {"voices": [_tts_default_voice()], "uploaded_voices": []}
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict):
            return {"voices": [_tts_default_voice()], "uploaded_voices": []}
        return data
    except (httpx.HTTPStatusError, httpx.RequestError):
        return {"voices": [_tts_default_voice()], "uploaded_voices": []}


@router.post("/speech")
async def tts_speech(request: SpeechRequest) -> Response:
    """Synthesize speech for an agent text response.

    Args:
        request: Speech synthesis request from the dashboard.

    Returns:
        Binary audio from the configured TTS service.

    Raises:
        HTTPException: If the TTS service is unavailable.

    """
    input_text = _sanitize_speech_text(request.input)
    if not input_text:
        raise HTTPException(
            status_code=400,
            detail="Speech input is empty after sanitization",
        )

    payload = _speech_payload(request, input_text)
    try:
        async with httpx.AsyncClient(timeout=_tts_timeout()) as client:
            response = await client.post(
                _tts_url("/audio/speech"),
                json=payload,
            )
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"TTS service error {exc.response.status_code}",
        ) from exc
    except httpx.RequestError as exc:
        raise HTTPException(
            status_code=502, detail="TTS service unreachable"
        ) from exc

    media_type = response.headers.get("content-type", "audio/wav")
    media_type = media_type.split(",", maxsplit=1)[0].strip()
    return Response(content=response.content, media_type=media_type)


def _speech_payload(request: SpeechRequest, input_text: str) -> dict[str, Any]:
    """Build the configured TTS speech request payload."""
    payload = _dump_model(request)
    payload["input"] = input_text
    payload["model"] = os.environ.get("VTO_TTS_MODEL", _TTS_MODEL)
    if request.voice == _TTS_VOICE:
        payload["voice"] = _tts_default_voice()
    task_type = os.environ.get("VTO_TTS_TASK_TYPE")
    if task_type:
        payload["task_type"] = task_type
    if not request.instructions:
        payload.pop("instructions", None)
    return payload


def _sanitize_speech_text(text: str) -> str:
    """Return text safe to send to the speech synthesizer.

    Args:
        text: Agent response text.

    Returns:
        Text with markdown and decorative symbols removed.

    """
    text = _MARKDOWN_CODE_BLOCK_RE.sub("", text)
    text = _DECORATIVE_SYMBOL_RE.sub("", text)
    text = _MARKDOWN_SYMBOL_RE.sub(" ", text)
    return _WHITESPACE_RE.sub(" ", text).strip()


def _tts_url(path: str) -> str:
    """Return a configured TTS API URL for a path under the API prefix."""
    prefix = os.environ.get("VTO_TTS_API_PREFIX", "/api/v1").strip("/")
    return f"{_tts_base_url()}/{prefix}{path}"


def _tts_base_url() -> str:
    """Return the configured TTS base URL."""
    return os.environ.get("VTO_TTS_BASE_URL", "http://vto-tts:8091").rstrip("/")


def _tts_default_voice() -> str:
    """Return the configured default TTS voice."""
    return os.environ.get("VTO_TTS_VOICE", _TTS_VOICE)


def _tts_timeout() -> httpx.Timeout:
    """Return the timeout for TTS service calls."""
    timeout_seconds = float(os.environ.get("VTO_TTS_TIMEOUT", "300"))
    return httpx.Timeout(timeout_seconds)


def _dump_model(model: BaseModel) -> dict[str, Any]:
    """Return a Pydantic model as a dict for v1/v2 compatibility."""
    if hasattr(model, "model_dump"):
        return model.model_dump()
    return model.dict()

# Copyright Advanced Micro Devices, Inc.
# 
# SPDX-License-Identifier: MIT

"""FastAPI routes: runtime context lifecycle (POST / GET / DELETE)."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from virtual_tryon.storage.runtime_context_store import RuntimeContextStore

router = APIRouter(prefix="/api/v1/vto/runtime", tags=["runtime"])


# ------------------------------------------------------------------ #
# Response model
# ------------------------------------------------------------------ #


class RuntimeContextResponse(BaseModel):
    """Runtime context state returned to clients."""

    session_id: str
    created_at: str
    last_activity: str
    state: dict[str, Any]


# ------------------------------------------------------------------ #
# Dependency placeholder — wired in app.py
# ------------------------------------------------------------------ #


def _get_store() -> RuntimeContextStore:
    """FastAPI dependency — replaced in app startup with real instance."""
    raise NotImplementedError("RuntimeContextStore not wired")


# ------------------------------------------------------------------ #
# Routes
# ------------------------------------------------------------------ #


@router.post("", response_model=RuntimeContextResponse, status_code=201)
async def create_runtime_context(
    store: RuntimeContextStore = Depends(_get_store),
) -> RuntimeContextResponse:
    """Create a new lightweight runtime context.

    Args:
        store: Injected RuntimeContextStore.

    Returns:
        RuntimeContextResponse with new session_id.

    """
    ctx = await store.create()
    return _to_response(ctx)


@router.get("/{session_id}", response_model=RuntimeContextResponse)
async def get_runtime_context(
    session_id: uuid.UUID,
    store: RuntimeContextStore = Depends(_get_store),
) -> RuntimeContextResponse:
    """Retrieve an existing runtime context.

    Args:
        session_id: Runtime context ID.
        store: Injected RuntimeContextStore.

    Returns:
        RuntimeContextResponse.

    Raises:
        HTTPException: 404 if context not found.

    """
    ctx = await store.get(str(session_id))
    if ctx is None:
        raise HTTPException(status_code=404, detail="Runtime context not found")
    return _to_response(ctx)


@router.delete("/{session_id}", status_code=204)
async def delete_runtime_context(
    session_id: uuid.UUID,
    store: RuntimeContextStore = Depends(_get_store),
) -> None:
    """End (delete) a runtime context.

    Args:
        session_id: Runtime context ID.
        store: Injected RuntimeContextStore.

    """
    ctx = await store.get(str(session_id))
    if ctx is None:
        raise HTTPException(status_code=404, detail="Runtime context not found")
    await store.delete(str(session_id))


# ------------------------------------------------------------------ #
# Helper
# ------------------------------------------------------------------ #


def _to_response(ctx: dict[str, Any]) -> RuntimeContextResponse:
    return RuntimeContextResponse(
        session_id=ctx["session_id"],
        created_at=ctx["created_at"],
        last_activity=ctx["last_activity"],
        state=ctx.get("state", {}),
    )

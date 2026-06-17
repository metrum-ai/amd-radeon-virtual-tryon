# Copyright Advanced Micro Devices, Inc.
# 
# SPDX-License-Identifier: MIT

"""FastAPI routes: try-on history (GET) and customer feedback (POST)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, field_validator

from virtual_tryon.storage.customer_feedback_store import CustomerFeedbackStore
from virtual_tryon.storage.tryon_history import TryOnHistory

router = APIRouter(prefix="/api/v1/vto/history", tags=["history"])


# ------------------------------------------------------------------ #
# Request / response models
# ------------------------------------------------------------------ #


class CustomerFeedbackRequest(BaseModel):
    """Five-dimension customer feedback for a try-on session."""

    session_id: str
    garment_id: str
    snapshot_id: str | None = None
    overlay_quality_rating: int | None = None
    fit_rating: int | None = None
    logistics_usefulness_rating: int | None = None
    availability_accuracy_rating: int | None = None
    shopping_experience_rating: int | None = None
    feedback: str | None = Field(None, max_length=5000)

    @field_validator(
        "overlay_quality_rating",
        "fit_rating",
        "logistics_usefulness_rating",
        "availability_accuracy_rating",
        "shopping_experience_rating",
        mode="before",
    )
    @classmethod
    def _validate_rating(cls, v: int | None) -> int | None:
        if v is not None and not (1 <= v <= 5):
            raise ValueError("Rating must be between 1 and 5")
        return v

    @field_validator("session_id", "garment_id", "snapshot_id")
    @classmethod
    def _validate_uuid(cls, v: str | None) -> str | None:
        """Reject non-UUID identifiers so they fail as 422, not a 500.

        The persistence layer casts these to ``uuid.UUID`` for asyncpg;
        validating here keeps malformed IDs from reaching the database.
        """
        if v is None:
            return v
        try:
            uuid.UUID(v)
        except (ValueError, AttributeError, TypeError) as exc:
            raise ValueError("must be a valid UUID") from exc
        return v


class CustomerFeedbackResponse(BaseModel):
    """Confirmation of persisted feedback."""

    feedback_id: str
    session_id: str
    garment_id: str
    snapshot_id: str | None = None


class TryOnHistoryItem(BaseModel):
    """Single try-on history record."""

    tryon_id: str
    session_id: str
    garment_id: str | None
    input_frame_ref: str | None
    output_image_ref: str | None
    created_at: str | None
    rating: int | None
    feedback: str | None


# ------------------------------------------------------------------ #
# Dependencies — wired in app.py
# ------------------------------------------------------------------ #


def _get_history() -> TryOnHistory:
    """FastAPI dependency — replaced in app startup with real instance."""
    raise NotImplementedError("TryOnHistory not wired")


def _get_feedback_store() -> CustomerFeedbackStore:
    """FastAPI dependency — replaced in app startup with real instance."""
    raise NotImplementedError("CustomerFeedbackStore not wired")


# ------------------------------------------------------------------ #
# Routes
# ------------------------------------------------------------------ #


@router.get("", response_model=list[TryOnHistoryItem])
async def get_history(
    session_id: uuid.UUID = Query(..., description="Runtime context UUID"),
    limit: int = Query(20, ge=1, le=100),
    history: TryOnHistory = Depends(_get_history),
) -> list[TryOnHistoryItem]:
    """List try-on history records for a session.

    Args:
        session_id: Runtime context UUID.
        limit: Max records to return (default 20, max 100).
        history: Injected TryOnHistory store.

    Returns:
        List of TryOnHistoryItem ordered by created_at DESC.

    """
    rows = await history.list_for_session(str(session_id), limit=limit)
    return [TryOnHistoryItem(**r) for r in rows]


@router.post("/{tryon_id}/feedback", response_model=CustomerFeedbackResponse)
async def submit_feedback(
    tryon_id: uuid.UUID,
    body: CustomerFeedbackRequest,
    feedback_store: CustomerFeedbackStore = Depends(_get_feedback_store),
) -> CustomerFeedbackResponse:
    """Submit five-dimension customer feedback for a try-on.

    The tryon_id in the path becomes the snapshot_id in the feedback record
    if not explicitly provided in the request body.

    Args:
        tryon_id: UUID of the try-on history record (snapshot).
        body: CustomerFeedbackRequest with ratings and optional notes.
        feedback_store: Injected CustomerFeedbackStore.

    Returns:
        CustomerFeedbackResponse with the new feedback_id.

    Raises:
        HTTPException: 400 if no rating dimension is provided.

    """
    has_rating = any(
        v is not None
        for v in (
            body.overlay_quality_rating,
            body.fit_rating,
            body.logistics_usefulness_rating,
            body.availability_accuracy_rating,
            body.shopping_experience_rating,
        )
    )
    if not has_rating and body.feedback is None:
        raise HTTPException(
            status_code=400,
            detail="At least one rating or feedback text must be provided",
        )

    # Use path tryon_id as snapshot_id when not explicitly set in body
    resolved_snapshot_id = body.snapshot_id or str(tryon_id)

    feedback_id = await feedback_store.record(
        body.session_id,
        body.garment_id,
        snapshot_id=resolved_snapshot_id,
        overlay_quality_rating=body.overlay_quality_rating,
        fit_rating=body.fit_rating,
        logistics_usefulness_rating=body.logistics_usefulness_rating,
        availability_accuracy_rating=body.availability_accuracy_rating,
        shopping_experience_rating=body.shopping_experience_rating,
        feedback=body.feedback,
    )

    return CustomerFeedbackResponse(
        feedback_id=feedback_id,
        session_id=body.session_id,
        garment_id=body.garment_id,
        snapshot_id=resolved_snapshot_id,
    )

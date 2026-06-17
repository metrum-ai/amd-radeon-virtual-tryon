# Copyright Advanced Micro Devices, Inc.
# 
# SPDX-License-Identifier: MIT

"""Customer feedback persistence (PostgreSQL) for F9."""

from __future__ import annotations

import uuid
from typing import Any

from virtual_tryon.storage.postgres import PostgresPool


class CustomerFeedbackStore:
    """Persist and query multi-dimension customer feedback.

    Args:
        db: PostgresPool instance.

    """

    def __init__(self, db: PostgresPool) -> None:
        """Initialize the CustomerFeedbackStore."""
        self._db = db

    async def record(
        self,
        session_id: str,
        garment_id: str,
        *,
        snapshot_id: str | None = None,
        overlay_quality_rating: int | None = None,
        fit_rating: int | None = None,
        logistics_usefulness_rating: int | None = None,
        availability_accuracy_rating: int | None = None,
        shopping_experience_rating: int | None = None,
        feedback: str | None = None,
    ) -> str:
        """Insert a feedback row and return its feedback_id.

        Args:
            session_id: Runtime context ID.
            garment_id: Catalog item ID the feedback is about.
            snapshot_id: Optional tryon_id of the overlay snapshot.
            overlay_quality_rating: 1-5 rating for live overlay visual quality.
            fit_rating: 1-5 rating for garment fit / size accuracy.
            logistics_usefulness_rating: 1-5 rating for price and branch info usefulness.
            availability_accuracy_rating: 1-5 rating for stock status accuracy.
            shopping_experience_rating: 1-5 rating for overall shopping experience.
            feedback: Free-text notes.

        Returns:
            UUID string of the created feedback_id.

        """
        feedback_id = str(uuid.uuid4())
        await self._db.execute(
            """
            INSERT INTO vto_customer_feedback (
                feedback_id, session_id, garment_id, snapshot_id,
                overlay_quality_rating, fit_rating,
                logistics_usefulness_rating, availability_accuracy_rating,
                shopping_experience_rating, feedback
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
            """,
            uuid.UUID(feedback_id),
            uuid.UUID(session_id),
            uuid.UUID(garment_id),
            uuid.UUID(snapshot_id) if snapshot_id else None,
            overlay_quality_rating,
            fit_rating,
            logistics_usefulness_rating,
            availability_accuracy_rating,
            shopping_experience_rating,
            feedback,
        )
        return feedback_id

    async def list_for_garment(
        self,
        garment_id: str,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Fetch recent feedback rows for a garment.

        Args:
            garment_id: Catalog item UUID.
            limit: Max rows to return.

        Returns:
            List of row dicts ordered by created_at DESC.

        """
        rows = await self._db.fetch(
            """
            SELECT feedback_id, session_id, garment_id, snapshot_id,
                   overlay_quality_rating, fit_rating,
                   logistics_usefulness_rating, availability_accuracy_rating,
                   shopping_experience_rating, feedback, created_at
            FROM vto_customer_feedback
            WHERE garment_id = $1
            ORDER BY created_at DESC
            LIMIT $2
            """,
            uuid.UUID(garment_id),
            limit,
        )
        return [_normalise(r) for r in rows]

    async def negative_summary(
        self, garment_id: str, threshold: int = 2
    ) -> dict[str, Any]:
        """Aggregate negative ratings (≤ threshold) for catalog QA.

        Args:
            garment_id: Catalog item UUID.
            threshold: Ratings at or below this value are counted as negative.

        Returns:
            Dict with counts per dimension and free-text sample.

        """
        rows = await self._db.fetch(
            """
            SELECT
                COUNT(*) FILTER (WHERE overlay_quality_rating <= $2)     AS negative_overlay,
                COUNT(*) FILTER (WHERE fit_rating <= $2)                 AS negative_fit,
                COUNT(*) FILTER (WHERE logistics_usefulness_rating <= $2) AS negative_logistics,
                COUNT(*) FILTER (WHERE availability_accuracy_rating <= $2) AS negative_availability,
                COUNT(*) FILTER (WHERE shopping_experience_rating <= $2)  AS negative_experience,
                COUNT(*)                                                  AS total
            FROM vto_customer_feedback
            WHERE garment_id = $1
            """,
            uuid.UUID(garment_id),
            threshold,
        )
        summary = dict(rows[0]) if rows else {}

        text_samples = await self._db.fetch(
            """
            SELECT feedback
            FROM vto_customer_feedback
            WHERE garment_id = $1 AND feedback IS NOT NULL
            ORDER BY created_at DESC
            LIMIT 5
            """,
            uuid.UUID(garment_id),
        )
        summary["feedback_samples"] = [r["feedback"] for r in text_samples]
        return summary


def _normalise(row: dict[str, Any]) -> dict[str, Any]:
    return {
        **row,
        "feedback_id": str(row["feedback_id"]),
        "session_id": str(row["session_id"]) if row.get("session_id") else None,
        "garment_id": str(row["garment_id"]) if row.get("garment_id") else None,
        "snapshot_id": (
            str(row["snapshot_id"]) if row.get("snapshot_id") else None
        ),
        "created_at": (
            row["created_at"].isoformat() if row.get("created_at") else None
        ),
    }

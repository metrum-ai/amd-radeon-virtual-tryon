# Copyright Advanced Micro Devices, Inc.
# 
# SPDX-License-Identifier: MIT

"""Try-on history persistence (PostgreSQL)."""

from __future__ import annotations

import json
import uuid
from typing import Any

from virtual_tryon.storage.postgres import PostgresPool


class TryOnHistory:
    """Persist and retrieve try-on records.

    Args:
        db: PostgresPool instance.

    """

    def __init__(self, db: PostgresPool) -> None:
        """Initialize the TryOnHistory."""
        self._db = db

    async def record(
        self,
        session_id: str,
        garment_id: str,
        *,
        input_frame_ref: str | None = None,
        output_image_ref: str | None = None,
        pose_data: dict | None = None,
        overlay_params: dict | None = None,
    ) -> str:
        """Insert a try-on record and return its tryon_id.

        Args:
            session_id: Runtime context ID.
            garment_id: Catalog item ID.
            input_frame_ref: Optional artifact ref for input frame.
            output_image_ref: Optional artifact ref for composite image.
            pose_data: Optional pose keypoints dict.
            overlay_params: Optional TPS warp params dict.

        Returns:
            UUID string of the created tryon_id.

        """
        tryon_id = str(uuid.uuid4())
        await self._db.execute(
            """
            INSERT INTO vto_tryon_history
                (tryon_id, session_id, garment_id,
                 input_frame_ref, output_image_ref,
                 pose_data, overlay_params)
            VALUES ($1, $2, $3, $4, $5, $6::jsonb, $7::jsonb)
            """,
            uuid.UUID(tryon_id),
            uuid.UUID(session_id),
            uuid.UUID(garment_id),
            input_frame_ref,
            output_image_ref,
            json.dumps(pose_data) if pose_data is not None else None,
            json.dumps(overlay_params) if overlay_params is not None else None,
        )
        return tryon_id

    async def list_for_session(
        self, session_id: str, limit: int = 20
    ) -> list[dict[str, Any]]:
        """Fetch recent try-on records for a session.

        Args:
            session_id: Runtime context ID.
            limit: Max rows to return.

        Returns:
            List of row dicts ordered by created_at DESC.

        """
        rows = await self._db.fetch(
            """
            SELECT tryon_id, session_id, garment_id,
                   input_frame_ref, output_image_ref,
                   pose_data, overlay_params, created_at,
                   rating, feedback
            FROM vto_tryon_history
            WHERE session_id = $1
            ORDER BY created_at DESC
            LIMIT $2
            """,
            uuid.UUID(session_id),
            limit,
        )
        return [_normalise(r) for r in rows]

    async def add_feedback(
        self,
        tryon_id: str,
        rating: int,
        feedback: str | None = None,
    ) -> None:
        """Attach a 1-5 rating (and optional text) to a try-on record.

        Args:
            tryon_id: Try-on record ID.
            rating: Integer 1-5.
            feedback: Optional free-text feedback.

        """
        await self._db.execute(
            """
            UPDATE vto_tryon_history
            SET rating = $2, feedback = $3
            WHERE tryon_id = $1
            """,
            uuid.UUID(tryon_id),
            rating,
            feedback,
        )


def _normalise(row: dict[str, Any]) -> dict[str, Any]:
    return {
        **row,
        "tryon_id": str(row["tryon_id"]),
        "session_id": str(row["session_id"]),
        "garment_id": str(row["garment_id"]) if row.get("garment_id") else None,
        "created_at": (
            row["created_at"].isoformat() if row.get("created_at") else None
        ),
    }

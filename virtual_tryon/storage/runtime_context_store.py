# Copyright Advanced Micro Devices, Inc.
# 
# SPDX-License-Identifier: MIT

"""Runtime context store: Redis-first with PostgreSQL fallback.

Key pattern : vto:runtime:{session_id}
TTL         : 30 minutes, refreshed on every write/restore
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

import redis.asyncio as aioredis

from virtual_tryon.storage.postgres import PostgresPool

logger = logging.getLogger(__name__)

_TTL_SECONDS = 1800  # 30 min


class RuntimeContextStore:
    """Manage VTO runtime contexts in Redis with PostgreSQL persistence.

    Args:
        redis: Async Redis client.
        db: PostgresPool instance.

    """

    def __init__(self, redis: aioredis.Redis, db: PostgresPool) -> None:
        """Initialize the RuntimeContextStore."""
        self._redis = redis
        self._db = db

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    async def create(self, user_id: str | None = None) -> dict[str, Any]:
        """Create a new runtime context.

        Args:
            user_id: Optional user identifier.

        Returns:
            Context dict with session_id, created_at, state.

        """
        session_id = str(uuid.uuid4())
        now_dt = datetime.now(timezone.utc)
        now = now_dt.isoformat()
        ctx: dict[str, Any] = {
            "session_id": session_id,
            "user_id": user_id,
            "created_at": now,
            "last_activity": now,
            "state": {},
        }
        await self._db.execute(
            """
            INSERT INTO vto_runtime_contexts
                (session_id, user_id, created_at, last_activity, state)
            VALUES ($1, $2, $3::timestamptz, $4::timestamptz, $5::jsonb)
            """,
            uuid.UUID(session_id),
            user_id,
            now_dt,
            now_dt,
            json.dumps(ctx["state"]),
        )
        await self._write_redis(session_id, ctx)
        return ctx

    async def get(self, session_id: str) -> dict[str, Any] | None:
        """Restore context from Redis, falling back to PostgreSQL.

        Args:
            session_id: Runtime context ID.

        Returns:
            Context dict or None if not found / expired.

        """
        ctx = await self._read_redis(session_id)
        if ctx is not None:
            return ctx

        row = await self._db.fetchrow(
            "SELECT * FROM vto_runtime_contexts WHERE session_id = $1",
            uuid.UUID(session_id),
        )
        if row is None:
            return None

        ctx = _row_to_ctx(row)
        await self._write_redis(session_id, ctx)
        return ctx

    async def update_state(
        self, session_id: str, patch: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Merge patch into context state and persist.

        Args:
            session_id: Runtime context ID.
            patch: Fields to merge into state.

        Returns:
            Updated context dict or None if not found.

        """
        ctx = await self.get(session_id)
        if ctx is None:
            return None

        ctx["state"].update(patch)
        now_dt = datetime.now(timezone.utc)
        ctx["last_activity"] = now_dt.isoformat()

        await self._db.execute(
            """
            UPDATE vto_runtime_contexts
            SET state = $2::jsonb, last_activity = $3::timestamptz
            WHERE session_id = $1
            """,
            uuid.UUID(session_id),
            json.dumps(ctx["state"]),
            now_dt,
        )
        await self._write_redis(session_id, ctx)
        return ctx

    async def delete(self, session_id: str) -> None:
        """Delete runtime context from Redis and PostgreSQL.

        Args:
            session_id: Runtime context ID.

        """
        await self._redis.delete(_key(session_id))
        await self._db.execute(
            "DELETE FROM vto_runtime_contexts WHERE session_id = $1",
            uuid.UUID(session_id),
        )

    # ------------------------------------------------------------------ #
    # Internal
    # ------------------------------------------------------------------ #

    async def _write_redis(self, session_id: str, ctx: dict[str, Any]) -> None:
        await self._redis.setex(_key(session_id), _TTL_SECONDS, json.dumps(ctx))

    async def _read_redis(self, session_id: str) -> dict[str, Any] | None:
        raw = await self._redis.get(_key(session_id))
        if raw is None:
            return None
        try:
            return json.loads(raw)
        except Exception:
            logger.warning(
                "Corrupt Redis context for %s — dropping", session_id
            )
            return None


def _key(session_id: str) -> str:
    return f"vto:runtime:{session_id}"


def _row_to_ctx(row: dict[str, Any]) -> dict[str, Any]:
    state = row.get("state") or {}
    if isinstance(state, str):
        state = json.loads(state)
    return {
        "session_id": str(row["session_id"]),
        "user_id": row.get("user_id"),
        "created_at": _iso(row.get("created_at")),
        "last_activity": _iso(row.get("last_activity")),
        "state": state,
    }


def _iso(dt: Any) -> str:
    if isinstance(dt, datetime):
        return dt.isoformat()
    return str(dt) if dt else ""

# Copyright Advanced Micro Devices, Inc.
# 
# SPDX-License-Identifier: MIT

"""Minimal asyncpg connection pool helper for VTO services."""

from __future__ import annotations

import asyncpg


class PostgresPool:
    """Thin wrapper around asyncpg pool with convenience query methods.

    Args:
        dsn: PostgreSQL DSN string.

    """

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    @classmethod
    async def connect(cls, dsn: str) -> PostgresPool:
        """Create and return a connected pool.

        Args:
            dsn: PostgreSQL connection string.

        Returns:
            Initialised PostgresPool instance.

        """
        pool = await asyncpg.create_pool(dsn, min_size=2, max_size=10)
        return cls(pool)

    async def execute(self, query: str, *args: object) -> str:
        """Execute a write query.

        Args:
            query: SQL query string.
            *args: Positional parameters.

        Returns:
            Status string from asyncpg.

        """
        async with self._pool.acquire() as conn:
            return await conn.execute(query, *args)

    async def fetch(self, query: str, *args: object) -> list[dict]:
        """Fetch multiple rows.

        Args:
            query: SQL query string.
            *args: Positional parameters.

        Returns:
            List of row dicts.

        """
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(query, *args)
            return [dict(r) for r in rows]

    async def fetchrow(self, query: str, *args: object) -> dict | None:
        """Fetch a single row.

        Args:
            query: SQL query string.
            *args: Positional parameters.

        Returns:
            Row dict or None if no result.

        """
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(query, *args)
            return dict(row) if row else None

    async def fetchval(self, query: str, *args: object) -> object:
        """Fetch a single scalar value.

        Args:
            query: SQL query string.
            *args: Positional parameters.

        Returns:
            Scalar value.

        """
        async with self._pool.acquire() as conn:
            return await conn.fetchval(query, *args)

    async def close(self) -> None:
        """Close the connection pool."""
        await self._pool.close()

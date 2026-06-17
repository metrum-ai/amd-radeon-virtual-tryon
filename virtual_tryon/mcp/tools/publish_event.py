# Copyright Advanced Micro Devices, Inc.
#
# SPDX-License-Identifier: MIT

"""publish_event MCP tool — publish structured events to Redis pub/sub."""

from __future__ import annotations

import json
import time
from typing import Any

import redis.asyncio as aioredis

# Only the canonical VTO event bus channel is permitted.
_ALLOWED_CHANNELS: frozenset[str] = frozenset({"vto:events"})

# Known workflow event types; prevents typo / injection.
_ALLOWED_EVENT_TYPES: frozenset[str] = frozenset({
    "session.created",
    "recommendations.ready",
    "garment.select",
    "overlay.updated",
    "overlay.snapshot_saved",
    "feedback.submitted",
})

# Max JSON payload size (256 KiB) to avoid Redis buffer abuse.
_MAX_PAYLOAD_BYTES = 256 * 1024


async def run(
    redis_client: aioredis.Redis,
    event_type: str,
    payload: dict[str, Any],
    channel: str = "vto:events",
) -> dict[str, Any]:
    """Publish a typed event to the VTO Redis pub/sub channel.

    Args:
        redis_client: Async Redis client.
        event_type: Event identifier (e.g. 'recommendations.ready').
            Must be one of the known VTO workflow events.
        payload: Event data dict (must be JSON-serialisable).
            Serialized size must not exceed 256 KiB.
        channel: Redis channel to publish on (default 'vto:events').
            Only 'vto:events' is allowed.

    Returns:
        Dict with keys: event_type, channel, published_at, subscriber_count.

    Raises:
        ValueError: If channel, event_type, or payload size is invalid.

    """
    if channel not in _ALLOWED_CHANNELS:
        raise ValueError(
            f"Invalid channel '{channel}'. Allowed: {sorted(_ALLOWED_CHANNELS)}"
        )
    if event_type not in _ALLOWED_EVENT_TYPES:
        raise ValueError(
            f"Invalid event_type '{event_type}'. Allowed: {sorted(_ALLOWED_EVENT_TYPES)}"
        )

    message = json.dumps(
        {
            "event_type": event_type,
            "payload": payload,
            "published_at": time.time(),
        }
    )
    if len(message.encode("utf-8")) > _MAX_PAYLOAD_BYTES:
        raise ValueError(
            f"Payload too large ({len(message)} bytes); max {_MAX_PAYLOAD_BYTES} bytes"
        )

    count = await redis_client.publish(channel, message)
    return {
        "event_type": event_type,
        "channel": channel,
        "published_at": time.time(),
        "subscriber_count": count,
    }

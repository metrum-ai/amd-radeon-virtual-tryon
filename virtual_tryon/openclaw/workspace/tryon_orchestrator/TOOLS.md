<!--
Copyright Advanced Micro Devices, Inc.

SPDX-License-Identifier: MIT
-->

# TryOnOrchestrator — Available MCP Tools

## Single-Turn Chat Mode (Demo)

In direct chat mode you have NO tool access. Respond conversationally using
any catalog or logistics data already present in the conversation. If data is
missing, ask one short clarifying question — do not call any tools.

## publish_event_tool (multi-agent runtime only)

Publish a workflow event to the VTO event bus. Use ONLY when running inside
the OpenClaw multi-agent runtime (not single-turn chat).

```
event_type: "session.created" | "recommendations.ready" | "overlay.updated"
              | "overlay.snapshot_saved"
payload: { session_id, garment_ids?, snapshot_ref? }
channel: "vto:events" (default)
```

Call at most ONCE per turn. Do not call search, catalog, logistics, or pgvector tools.
Delegate catalog search to fashion-stylist. Delegate logistics enrichment to logistics-agent.

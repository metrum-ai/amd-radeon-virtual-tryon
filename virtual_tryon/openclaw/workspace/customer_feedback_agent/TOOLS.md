<!--
Copyright Advanced Micro Devices, Inc.

SPDX-License-Identifier: MIT
-->

# CustomerFeedbackAgent — Available MCP Tools

All tools are provided by the `vto-tools` MCP server.

## query_pgvector_tool

Query feedback aggregates or garment metadata for negative summary generation.

```
query_name: "feedback_summary"
params: ["<garment UUID>"]
→ list of rows
```

Use only for read queries when summarizing negative feedback for catalog QA.
Never use for writing feedback rows (use the REST API instead).

## publish_event_tool

Emit a feedback event after feedback is persisted.

```
event_type: "feedback.submitted"
payload: {
  session_id: "<uuid>",
  garment_id: "<uuid>",
  snapshot_id: "<uuid or null>",
  feedback_id: "<uuid>"
}
```

Call once after the REST API confirms feedback_id.

## Workflow — Collect Feedback

1. Accept handoff `{session_id, garment_id, snapshot_id?, tryon_id}`
2. Prompt the shopper for ratings on each missing feedback dimension
3. Collect ratings and optional free-text notes
3. POST to `/api/v1/vto/history/{tryon_id}/feedback` with the full payload
4. On 200 response: call `publish_event_tool("feedback.submitted", ...)`
5. Thank the shopper and end the feedback flow

## Workflow — Negative Summary

1. Call `query_pgvector_tool` with the garment_id
2. Aggregate ratings ≤ 2 per dimension
3. Return summary JSON to TryOnOrchestrator for catalog QA routing

Never ask for gender in feedback flows.

<!--
Copyright Advanced Micro Devices, Inc.

SPDX-License-Identifier: MIT
-->

# LogisticsAgent — Tools

This agent has NO MCP tool access. Price and branch availability data are
pre-fetched by the VTO API and provided in `<logistics_context>` tags in the
system message before this agent is called.

Do not attempt to call any tools. Return GarmentLogistics JSON using only the
data from `<logistics_context>`.

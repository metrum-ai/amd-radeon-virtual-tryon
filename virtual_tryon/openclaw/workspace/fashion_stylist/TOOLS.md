<!--
Copyright Advanced Micro Devices, Inc.

SPDX-License-Identifier: MIT
-->

# FashionStylistAgent — Tools

This agent has NO MCP tool access. Catalog items are pre-fetched by the VTO API and
provided in `<catalog_context>` tags in the system message before this agent is called.

Do not attempt to call any tools. Return your JSON recommendations using only the items
from `<catalog_context>`.

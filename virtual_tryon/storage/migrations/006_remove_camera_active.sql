-- Copyright Advanced Micro Devices, Inc.
--
-- SPDX-License-Identifier: MIT
--
-- F11: Remove camera_active column from vto_runtime_contexts.
-- Webcam capture feature removed; column is unused.
ALTER TABLE vto_runtime_contexts DROP COLUMN IF EXISTS camera_active;

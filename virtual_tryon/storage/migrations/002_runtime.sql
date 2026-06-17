-- Copyright Advanced Micro Devices, Inc.
--
-- SPDX-License-Identifier: MIT
--
-- F6: Runtime Contexts and Try-On History

CREATE TABLE IF NOT EXISTS vto_runtime_contexts (
    session_id UUID PRIMARY KEY,
    user_id TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_activity TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    state JSONB,
    camera_active BOOLEAN DEFAULT FALSE
);

CREATE INDEX IF NOT EXISTS idx_vto_runtime_user
    ON vto_runtime_contexts (user_id);
CREATE INDEX IF NOT EXISTS idx_vto_runtime_activity
    ON vto_runtime_contexts (last_activity);

CREATE TABLE IF NOT EXISTS vto_tryon_history (
    tryon_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id UUID REFERENCES vto_runtime_contexts(session_id) ON DELETE SET NULL,
    garment_id UUID REFERENCES vto_catalog(item_id) ON DELETE SET NULL,
    input_frame_ref TEXT,
    output_image_ref TEXT,
    pose_data JSONB,
    overlay_params JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    rating INTEGER,
    feedback TEXT
);

CREATE INDEX IF NOT EXISTS idx_vto_history_session
    ON vto_tryon_history (session_id, created_at DESC);

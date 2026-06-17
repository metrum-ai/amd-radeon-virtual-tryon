-- Copyright Advanced Micro Devices, Inc.
--
-- SPDX-License-Identifier: MIT
--
-- F9: Customer Feedback Collection

CREATE TABLE IF NOT EXISTS vto_customer_feedback (
    feedback_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id UUID,  -- no FK: feedback can be submitted without an active runtime session
    garment_id UUID REFERENCES vto_catalog(item_id) ON DELETE SET NULL,
    -- snapshot_id references vto_tryon_history.tryon_id; no FK to allow feedback without a snapshot
    snapshot_id UUID,
    overlay_quality_rating INTEGER CHECK (overlay_quality_rating BETWEEN 1 AND 5),
    fit_rating INTEGER CHECK (fit_rating BETWEEN 1 AND 5),
    logistics_usefulness_rating INTEGER CHECK (logistics_usefulness_rating BETWEEN 1 AND 5),
    availability_accuracy_rating INTEGER CHECK (availability_accuracy_rating BETWEEN 1 AND 5),
    shopping_experience_rating INTEGER CHECK (shopping_experience_rating BETWEEN 1 AND 5),
    feedback TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_vto_customer_feedback_session
    ON vto_customer_feedback (session_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_vto_customer_feedback_garment
    ON vto_customer_feedback (garment_id, created_at DESC);

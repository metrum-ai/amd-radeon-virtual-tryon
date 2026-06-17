#!/usr/bin/env bash

# Copyright Advanced Micro Devices, Inc.
# 
# SPDX-License-Identifier: MIT

# Entrypoint for the pipeline Docker service.
# In API mode (default): launches uvicorn.
# In CLI mode (MODE=cli): translates env vars into run_pipeline.py args.
set -euo pipefail

MODE="${MODE:-api}"

if [[ "$MODE" == "api" ]]; then
    exec python -m uvicorn pipeline_api:app --host 0.0.0.0 --port 8080
fi

# ── CLI mode ─────────────────────────────────────────────────────────────────
VIDEO="${VIDEO:-}"
GARMENT="${GARMENT:-}"
GARMENT_TOP="${GARMENT_TOP:-}"
GARMENT_BOTTOM="${GARMENT_BOTTOM:-}"
CATEGORY="${CATEGORY:-tops}"
SERVER_URLS="${FASHN_SERVER_URLS:-http://fashn0:7860}"
NUM_FRAMES="${NUM_FRAMES:-4}"
CANDIDATE_FRAMES="${CANDIDATE_FRAMES:-12}"
FRAME_DURATION="${FRAME_DURATION:-2.5}"
TIMESTEPS="${TIMESTEPS:-8}"
SEED="${SEED:-42}"
OUTPUT_SUBDIR="${OUTPUT_SUBDIR:-default}"

if [[ -z "$VIDEO" ]]; then
    echo "ERROR: VIDEO env var is required in CLI mode" >&2
    exit 1
fi

ARGS=(
    --video "/app/input/${VIDEO}"
    --server-urls "$SERVER_URLS"
    --num-frames "$NUM_FRAMES"
    --candidate-frames "$CANDIDATE_FRAMES"
    --frame-duration "$FRAME_DURATION"
    --timesteps "$TIMESTEPS"
    --seed "$SEED"
    --output-dir "/app/output/${OUTPUT_SUBDIR}"
)

if [[ -n "$GARMENT_TOP" && -n "$GARMENT_BOTTOM" ]]; then
    ARGS+=(
        --garment-top "/app/input/${GARMENT_TOP}"
        --garment-bottom "/app/input/${GARMENT_BOTTOM}"
    )
elif [[ -n "$GARMENT" ]]; then
    ARGS+=(
        --garment "/app/input/${GARMENT}"
        --category "$CATEGORY"
    )
else
    echo "ERROR: Set GARMENT or both GARMENT_TOP and GARMENT_BOTTOM" >&2
    exit 1
fi

if [[ "${SKIP_MUSIC:-0}" == "1" ]]; then
    ARGS+=(--skip-music)
fi

if [[ -n "${MUSIC_PROMPT:-}" ]]; then
    ARGS+=(--music-prompt "$MUSIC_PROMPT")
fi

exec python /app/run_pipeline.py "${ARGS[@]}"

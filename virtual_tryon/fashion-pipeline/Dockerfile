# Copyright Advanced Micro Devices, Inc.
# 
# SPDX-License-Identifier: MIT

# Fashion pipeline — AMD ROCm 7.2.2 / gfx1201 (RDNA4)
# Build context: retail-virtual-tryon/ (repo root)
#
#   docker compose build pipeline
#   docker compose up -d

FROM rocm/pytorch:rocm7.2.2_ubuntu24.04_py3.12_pytorch_release_2.10.0

LABEL maintainer="fashion-pipeline" \
      version="1.0" \
      description="Slideshow-based fashion VTO pipeline with multi-job API"

WORKDIR /app

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        ffmpeg \
        libgl1 \
        libglib2.0-0 \
        fonts-dejavu-core \
        curl && \
    rm -rf /var/lib/apt/lists/*

# ── fashn_vton (no-deps install + manual dep pin) ─────────────────────────────
RUN pip install --no-cache-dir --no-deps \
        "https://github.com/fashn-AI/fashn-vton-1.5/archive/7c0f10af3f91ad4048fe9729c470a13ef905d25a.zip" && \
    pip install --no-cache-dir \
        "safetensors>=0.3.0" \
        "huggingface_hub>=0.20.0" \
        "pillow>=9.0.0" \
        "numpy>=1.21.0,<2.0" \
        "opencv-python-headless>=4.5.0" \
        "tqdm>=4.65.0" \
        "einops>=0.6.0" \
        "matplotlib>=3.5.0" \
        "onnxruntime>=1.16.0" && \
    pip install --no-cache-dir \
        "https://repo.radeon.com/rocm/manylinux/rocm-rel-7.2.2/onnxruntime_migraphx-1.23.2-cp312-cp312-manylinux_2_27_x86_64.manylinux_2_28_x86_64.whl" && \
    pip install --no-cache-dir \
        "fastapi>=0.111.0" \
        "uvicorn[standard]>=0.29.0" \
        "python-multipart>=0.0.9"

# ── DWPose ROCm execution-provider patch ──────────────────────────────────────
# fashn_vton's DWPose only ever requests "CUDAExecutionProvider", which ROCm's
# onnxruntime doesn't have (it exposes "MIGraphXExecutionProvider" instead), so DWPose ran on CPU.
COPY virtual_tryon/fashion-pipeline/patch_dwpose_provider.py /tmp/patch_dwpose_provider.py
RUN python3 /tmp/patch_dwpose_provider.py && rm /tmp/patch_dwpose_provider.py

# ── Commercially clean human-parser compatibility wrapper ────────────────────
COPY virtual_tryon/fashion-pipeline/fashn_human_parser /app/fashn_human_parser
ENV PYTHONPATH="/app:${PYTHONPATH}"

# ── Download Apache-2.0 human-parsing ONNX weights ───────────────────────────
RUN mkdir -p /app/models && \
    python3 -c \
    "from huggingface_hub import hf_hub_download; \
     hf_hub_download(repo_id='basso4/humanparsing', filename='parsing_atr.onnx', local_dir='/app/models', local_dir_use_symlinks=False)"

# ── Pipeline scripts ─────────────────────────────────────────────────────────
COPY virtual_tryon/fashion-pipeline/fashn_server.py      /app/fashn_server.py
COPY virtual_tryon/fashion-pipeline/fashn_client.py      /app/fashn_client.py
COPY virtual_tryon/fashion-pipeline/frame_selector.py    /app/frame_selector.py
COPY virtual_tryon/fashion-pipeline/slideshow_assembler.py /app/slideshow_assembler.py
COPY virtual_tryon/fashion-pipeline/fashion_tips.py      /app/fashion_tips.py
COPY virtual_tryon/fashion-pipeline/music_client.py      /app/music_client.py
COPY virtual_tryon/fashion-pipeline/run_pipeline.py      /app/run_pipeline.py
COPY virtual_tryon/fashion-pipeline/pipeline_api.py      /app/pipeline_api.py
COPY virtual_tryon/fashion-pipeline/entrypoint.sh        /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

# Non-root user with ROCm GPU group membership.
# video (GID 44) and render (GID 109) give access to /dev/kfd and /dev/dri.
# docker-compose group_add mirrors these GIDs at runtime for belt-and-suspenders.

RUN (getent group video >/dev/null || groupadd -g 44 video) && \
    (getent group render >/dev/null || groupadd -g 109 render) && \
    (_u1000="$(getent passwd 1000 | cut -d: -f1)"; \
     [ -n "$_u1000" ] && [ "$_u1000" != "appuser" ] && userdel -r "$_u1000" 2>/dev/null || true) && \
    useradd -m -u 1000 -s /bin/bash appuser && \
    usermod -aG video,render appuser && \
    mkdir -p /home/appuser/.cache/huggingface \
             /app/weights /app/input /app/output && \
    chown -R appuser:appuser /app /home/appuser

VOLUME ["/app/weights", "/app/input", "/app/output"]

HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=5 \
    CMD curl -f http://localhost:8080/health || exit 1

ENV ACESTEP_URL="http://acestep:8001" \
    FASHN_SERVER_URLS="http://fashn0:7860" \
    WEIGHTS_DIR="/app/weights" \
    NUM_FRAMES="4" \
    CANDIDATE_FRAMES="12" \
    FRAME_DURATION="2.5" \
    TIMESTEPS="8" \
    SEED="42" \
    SKIP_MUSIC="0" \
    HSA_OVERRIDE_GFX_VERSION="12.0.1" \
    ROCR_VISIBLE_DEVICES="0,1" \
    TORCH_ROCM_AOTRITON_ENABLE_EXPERIMENTAL="1"

USER appuser

ENTRYPOINT ["/app/entrypoint.sh"]

# Copyright Advanced Micro Devices, Inc.
# 
# SPDX-License-Identifier: MIT

# ACE-Step music generation service — AMD ROCm 7.2.2 / gfx1201 (RDNA4)
# acestep-v15-turbo + acestep-5Hz-lm-1.7B, pt backend

FROM rocm/pytorch:rocm7.2.2_ubuntu24.04_py3.12_pytorch_release_2.10.0

RUN apt-get update && \
    apt-get install -y --no-install-recommends curl git && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install ACE-Step from source (not on PyPI).
# For ROCm, nano-vllm is installed from the bundled subpackage; the
# requirements-rocm-linux.txt explicitly excludes it from PyPI.
RUN git clone --depth 1 https://github.com/ace-step/ACE-Step-1.5.git /tmp/acestep && \
    pip install --no-cache-dir -r /tmp/acestep/requirements-rocm-linux.txt && \
    pip install --no-cache-dir -e /tmp/acestep/acestep/third_parts/nano-vllm && \
    pip install --no-cache-dir --no-deps /tmp/acestep && \
    pip install --no-cache-dir hf_transfer && \
    rm -rf /tmp/acestep

# Patch hardcoded checkpoint_dir so ACESTEP_CHECKPOINTS_DIR is respected.
RUN sed -i 's|checkpoint_dir = os.path.join(project_root, "checkpoints")|checkpoint_dir = os.environ.get("ACESTEP_CHECKPOINTS_DIR", os.path.join(project_root, "checkpoints"))|' \
    /opt/venv/lib/python3.12/site-packages/acestep/api/startup_model_init.py \
    /opt/venv/lib/python3.12/site-packages/acestep/api/http/model_init_service.py

# gfx1201 = RDNA4 (RX 9070 / W9070)
ENV ACESTEP_MODE=api \
    ACESTEP_LM_BACKEND=pt \
    ACESTEP_CONFIG_PATH=/app/acestep-models/acestep-v15-turbo \
    ACESTEP_LM_MODEL_PATH=/app/acestep-models/acestep-5Hz-lm-1.7B \
    ACESTEP_API_HOST=0.0.0.0 \
    ACESTEP_API_PORT=8001 \
    PORT=8001 \
    TOKENIZERS_PARALLELISM=false \
    HF_HUB_ENABLE_HF_TRANSFER=0 \
    HSA_OVERRIDE_GFX_VERSION=12.0.1 \
    TORCH_ROCM_AOTRITON_ENABLE_EXPERIMENTAL=1 \
    ROCR_VISIBLE_DEVICES=2

VOLUME ["/root/.cache/huggingface"]

EXPOSE 8001

HEALTHCHECK --interval=30s --timeout=10s --start-period=300s --retries=10 \
    CMD curl -f http://localhost:${ACESTEP_API_PORT}/health || exit 1

CMD ["acestep-api"]

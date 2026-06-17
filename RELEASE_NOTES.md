<!--
Copyright Advanced Micro Devices, Inc.

SPDX-License-Identifier: MIT
-->
# Release Notes

## v1.0

### Features

* **Privacy-Preserving On-Prem AI Infrastructure**: All AI workloads run locally on 4× **AMD Radeon AI PRO R9700S** GPUs, keeping customer videos, images, and shopping interactions within the retailer's infrastructure while delivering high-performance inference without external data processing.

* **Diffusion-Powered Virtual Try-On**: Dual `FASHN` inference servers generate photo-realistic garment composites from customer demo videos; DWPose-powered frame selection extracts the sharpest, most pose-diverse keyframes (front, back, side) to maximise showcase quality.

* **AI-Styled Slideshow with Dynamic Music**: Selected keyframes are stitched into a crossfade slideshow with LLM-written styling-tip text overlays and `ACE-Step` AI-generated background music, producing a polished, shareable try-on video.

* **Voice-First Agentic Shopping**: OpenClaw agents powered by `Qwen 3.6 27B` drive preference discovery, logistics-enriched recommendations, and customer feedback collection via natural voice conversation; `Kokoro TTS` reads agent replies aloud.

* **Gender-Scoped Catalogue with AI-Powered Discovery**: Four customer profiles scope the catalogue and agent recommendations by gender; BGE text embeddings and DINOv3 visual similarity search over Milvus enable natural-language and image-based discovery; every recommendation card displays live branch stock, pricing, and fulfilment status queried through MCP tools.

* **Session History and Comparison**: All try-on generations in a session are preserved with side-by-side comparison; shoppers can step through multiple attempts or view them in a thumbnail history.

---

### Components

#### Python Libraries

| Component | Version | License |
|-----------|---------|---------|
| FastAPI | 0.137.1 | [MIT](https://spdx.org/licenses/MIT.html) |
| Uvicorn | 0.49.0 | [BSD-3-Clause](https://spdx.org/licenses/BSD-3-Clause.html) |
| Pydantic | 2.13.4 | [MIT](https://spdx.org/licenses/MIT.html) |
| pydantic-settings | 2.14.1 | [MIT](https://spdx.org/licenses/MIT.html) |
| asyncpg | 0.31.0 | [Apache-2.0](https://spdx.org/licenses/Apache-2.0.html) |
| httpx | 0.28.1 | [BSD-3-Clause](https://spdx.org/licenses/BSD-3-Clause.html) |
| redis[asyncio] | 8.0.0 | [MIT](https://spdx.org/licenses/MIT.html) |
| Pillow | 12.2.0 | [HPND](https://spdx.org/licenses/HPND.html) |
| pymilvus | 2.3.8 | [Apache-2.0](https://spdx.org/licenses/Apache-2.0.html) |
| torch | 2.5.1 | [BSD-3-Clause](https://spdx.org/licenses/BSD-3-Clause.html) |
| torchvision | 0.20.1 | [BSD-3-Clause](https://spdx.org/licenses/BSD-3-Clause.html) |
| transformers | 4.57.6 | [Apache-2.0](https://spdx.org/licenses/Apache-2.0.html) |
| fastmcp | 3.4.2 | [Apache-2.0](https://spdx.org/licenses/Apache-2.0.html) |
| prometheus-client | 0.25.0 | [Apache-2.0](https://spdx.org/licenses/Apache-2.0.html) |
| marshmallow | 3.26.2 | [MIT](https://spdx.org/licenses/MIT.html) |
| numpy | 1.26.4 | [BSD-3-Clause](https://spdx.org/licenses/BSD-3-Clause.html) |
| opencv-python-headless | 4.11.0.86 | [Apache-2.0](https://spdx.org/licenses/Apache-2.0.html) |
| onnxruntime | 1.26.0 | [MIT](https://spdx.org/licenses/MIT.html) |
| onnxruntime-migraphx | 1.23.2 | [MIT](https://spdx.org/licenses/MIT.html) |
| einops | 0.8.2 | [MIT](https://spdx.org/licenses/MIT.html) |
| matplotlib | 3.10.8 | [matplotlib License](https://matplotlib.org/stable/users/project/license.html) (PSF-compatible) |
| safetensors | 0.8.0 | [Apache-2.0](https://spdx.org/licenses/Apache-2.0.html) |
| huggingface-hub | 1.19.0 | [Apache-2.0](https://spdx.org/licenses/Apache-2.0.html) |
| tqdm | 4.67.3 | [MPL-2.0](https://spdx.org/licenses/MPL-2.0.html) AND [MIT](https://spdx.org/licenses/MIT.html) |
| python-multipart | 0.0.32 | [Apache-2.0](https://spdx.org/licenses/Apache-2.0.html) |
| DWPose | bundled in fashn_vton | [Apache-2.0](https://spdx.org/licenses/Apache-2.0.html) |

#### System Libraries

| Component | Version | License |
|-----------|---------|---------|
| FFmpeg | system apt | [LGPL-2.1+](https://spdx.org/licenses/LGPL-2.1-or-later.html) |
| ROCm / HIP runtime | 7.2.3 | [MIT](https://spdx.org/licenses/MIT.html) |
| DejaVu Fonts (`fonts-dejavu-core`) | system apt | [Bitstream Vera License](https://github.com/dejavu-fonts/dejavu-fonts/blob/master/LICENSE) |
| Mesa / libgl1 | system apt | [MIT](https://spdx.org/licenses/MIT.html) |
| GLib / libglib2.0-0 | system apt | [LGPL-2.1+](https://spdx.org/licenses/LGPL-2.1-or-later.html) |

#### Frontend

| Component | Version | License |
|-----------|---------|---------|
| React | 18.3.1 | [MIT](https://spdx.org/licenses/MIT.html) |
| react-dom | 18.3.1 | [MIT](https://spdx.org/licenses/MIT.html) |
| lucide-react | 1.17.0 | [ISC](https://spdx.org/licenses/ISC.html) (Feather-derived icons: [MIT](https://spdx.org/licenses/MIT.html)) |
| react-markdown | 10.1.0 | [MIT](https://spdx.org/licenses/MIT.html) |
| remark-gfm | 4.0.1 | [MIT](https://spdx.org/licenses/MIT.html) |
| TypeScript | 5.9.3 | [Apache-2.0](https://spdx.org/licenses/Apache-2.0.html) |
| Vite | 5.4.21 | [MIT](https://spdx.org/licenses/MIT.html) |
| pnpm | 9 | [MIT](https://spdx.org/licenses/MIT.html) |

#### Base Docker Images

| Image | License |
|-------|---------|
| `python:3.10-slim` | [PSF-2.0](https://spdx.org/licenses/PSF-2.0.html) |
| `rocm/pytorch:rocm7.2.2_ubuntu24.04_py3.12_pytorch_release_2.10.0` | [MIT](https://spdx.org/licenses/MIT.html) (ROCm) + [BSD-3-Clause](https://spdx.org/licenses/BSD-3-Clause.html) (PyTorch) |
| `node:22-alpine` | [MIT](https://spdx.org/licenses/MIT.html) |
| `nginx:1.31.1-alpine3.23-slim` | [BSD-2-Clause](https://spdx.org/licenses/BSD-2-Clause.html) |

#### Infrastructure Services

| Component | Version | License |
|-----------|---------|---------|
| TimescaleDB | 2.27.2 / PG 16 | [Apache-2.0](https://spdx.org/licenses/Apache-2.0.html) / [PostgreSQL](https://www.postgresql.org/about/licence/) |
| Valkey | 8-alpine | [BSD-3-Clause](https://spdx.org/licenses/BSD-3-Clause.html) |
| Milvus | v2.4.15 | [Apache-2.0](https://spdx.org/licenses/Apache-2.0.html) |
| RustFS | 1.0.0-alpha.89 | [Apache-2.0](https://spdx.org/licenses/Apache-2.0.html) |
| etcd | v3.5.5 | [Apache-2.0](https://spdx.org/licenses/Apache-2.0.html) |
| Ollama | 0.30.6-rocm | [MIT](https://spdx.org/licenses/MIT.html) |
| Lemonade Server | v10.6.0 | [Apache-2.0](https://spdx.org/licenses/Apache-2.0.html) |
| Prometheus | v2.51.2 | [Apache-2.0](https://spdx.org/licenses/Apache-2.0.html) |
| node-exporter | v1.8.2 | [Apache-2.0](https://spdx.org/licenses/Apache-2.0.html) |
| AMD Device Metrics Exporter | v1.4.2 | [Apache-2.0](https://spdx.org/licenses/Apache-2.0.html) |
| Ollama Metrics Proxy | `02911ce` | [MIT](https://spdx.org/licenses/MIT.html) |
| OpenClaw | 2026.6.5 | [MIT](https://spdx.org/licenses/MIT.html) |

#### Models

| Component | License |
|-----------|---------|
| `Qwen 3.6 27B` (LLM, via Ollama) | [Apache-2.0](https://spdx.org/licenses/Apache-2.0.html) |
| `Kokoro TTS` (via Lemonade Server) | [Apache-2.0](https://spdx.org/licenses/Apache-2.0.html) |
| `ACE-Step` (music generation) | [Apache-2.0](https://spdx.org/licenses/Apache-2.0.html) |
| `BAAI/bge-small-en-v1.5` (text embeddings) | [MIT](https://spdx.org/licenses/MIT.html) |
| `facebook/dinov3-vits16-pretrain-lvd1689m` (visual embeddings) | [DINOv3 License (Meta)](https://ai.meta.com/resources/models-and-libraries/dinov3-license/) |
| `FASHN-VTON-1.5` (garment diffusion) | [Apache-2.0](https://spdx.org/licenses/Apache-2.0.html) |
| `basso4/humanparsing` (human parsing ONNX model) | [Apache-2.0](https://spdx.org/licenses/Apache-2.0.html) |

---

### Known Issues

#### Application

- **First agent interaction is slow** — Ollama pulls `qwen3.6:27b` (~15 GB) on first call if not already cached.
- **First TTS request is slow** — Lemonade downloads the Kokoro model (~500 MB) on first use.
- **ACE-Step cold-start** — The `acestep` container has a 5-minute startup window while the model loads into GPU 3 memory.
- **First pipeline generation is slow** — The first try-on generation after stack startup typically takes 3–4 minutes as the FASHN diffusion models are loaded into GPU memory for the first time.

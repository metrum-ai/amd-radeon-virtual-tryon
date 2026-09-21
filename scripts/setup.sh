#!/usr/bin/env bash

# Copyright Advanced Micro Devices, Inc.
#
# SPDX-License-Identifier: MIT

# =============================================================================
# Retail Virtual Try-On Platform — Setup & Launch
# Checks prerequisites, configures environment, builds images, starts services,
# and seeds the garment catalogue.
# =============================================================================
set -euo pipefail

RED='\033[0;31m'; YELLOW='\033[1;33m'; GREEN='\033[0;32m'; CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'
info()  { echo -e "${CYAN}[INFO]${NC}  $*"; }
ok()    { echo -e "${GREEN}[ OK ]${NC}  $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
error() { echo -e "${RED}[FAIL]${NC}  $*"; }
die()   { error "$*"; exit 1; }
hr()    { echo -e "${BOLD}────────────────────────────────────────────────────${NC}"; }

retry_command() {
    local label="$1" attempts="${2:-3}" delay_s="${3:-15}"
    shift 3
    local attempt=1
    while true; do
        if "$@"; then return 0; fi
        [ "$attempt" -ge "$attempts" ] && return 1
        warn "${label} failed (attempt ${attempt}/${attempts}); retrying in ${delay_s}s..."
        sleep "$delay_s"
        attempt=$((attempt + 1))
    done
}

SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$SCRIPT_DIR"

DISK_MIN_GB=60
RAM_MIN_GB=64
MIN_REQUIRED_GPUS=4
HSA_VERSION="12.0.1"
SETUP_DOCKER_RETRY_ATTEMPTS="${SETUP_DOCKER_RETRY_ATTEMPTS:-3}"
SETUP_DOCKER_RETRY_DELAY_S="${SETUP_DOCKER_RETRY_DELAY_S:-20}"

# ---------------------------------------------------------------------------
# Helpers sourced from setup_runtime_defaults.sh (physical core detection)
# ---------------------------------------------------------------------------
physical_core_count_from_lscpu() {
    local lscpu_output="$1"
    printf '%s\n' "$lscpu_output" | awk -F, '
        $0 !~ /^#/ && NF >= 2 {
            key = $(NF - 1) "," $NF
            seen[key] = 1
        }
        END { for (key in seen) count++; print count + 0 }
    '
}

detect_cpu_core_count() {
    local physical_cores
    if command -v lscpu >/dev/null 2>&1; then
        physical_cores="$(physical_core_count_from_lscpu "$(lscpu -p=Core,Socket 2>/dev/null)")"
        [ "${physical_cores:-0}" -gt 0 ] && echo "$physical_cores" && return
    fi
    command -v nproc >/dev/null 2>&1 && nproc && return
    echo 1
}

add_or_replace() {
    local key="$1" value="$2"
    if [ -f .env ] && awk -F= -v k="$key" '$1 == k { found=1 } END { exit found ? 0 : 1 }' .env; then
        sed -i "s|^${key}=.*|${key}=${value}|" .env
    else
        [ -s .env ] && printf '\n' >> .env
        printf '%s=%s\n' "$key" "$value" >> .env
    fi
}

# env_file_value <key> <file> — print a key's value from an env file, or
# nothing (exit 1) if the file/key doesn't exist.
env_file_value() {
    local key="$1" file="${2:-.env}"
    [ -f "$file" ] || return 1
    awk -F= -v key="$key" '
        $1 == key { sub(/^[^=]*=/, ""); print; found = 1; exit }
        END { exit found ? 0 : 1 }
    ' "$file"
}

# docker_volume_exists <suffix> — true if any Docker volume name ends with
# <suffix> (compose prefixes volume names with the project name, which
# varies by clone directory, so we match on the compose-file volume name's
# suffix rather than requiring an exact match).
docker_volume_exists() {
    local suffix="$1"
    docker volume ls --format '{{.Name}}' 2>/dev/null | grep -q -- "${suffix}\$"
}

# setup_tls — generate a self-signed cert for optional HTTPS, so mic/voice
# input works over a LAN IP. nginx.tls.conf sniffs each connection and serves
# http:// and https:// on the same frontend port, so this needs no extra port.
setup_tls() {
    local tls_dir="${HOME}/.cache/vto-tls"
    mkdir -p "$tls_dir"
    local lan_ip
    lan_ip="$(ip -4 -o addr show scope global 2>/dev/null | awk 'NR==1{print $4}' | cut -d/ -f1)"
    local san="DNS:localhost,IP:127.0.0.1"
    [ -n "$lan_ip" ] && san="${san},IP:${lan_ip}"

    openssl req -x509 -nodes -newkey rsa:2048 -days 825 \
        -keyout "${tls_dir}/tls.key" -out "${tls_dir}/tls.crt" \
        -subj "/CN=${lan_ip:-localhost}" \
        -addext "subjectAltName=${san}" >/dev/null 2>&1 \
        || die "Self-signed certificate generation failed (is openssl installed?)"
    chmod 644 "${tls_dir}/tls.key" "${tls_dir}/tls.crt"

    add_or_replace "TLS_ENABLED" "1"
    add_or_replace "TLS_CERT_DIR" "${tls_dir}"
    add_or_replace "NGINX_CONF" "${SCRIPT_DIR}/frontend/nginx.tls.conf"
    ok "Self-signed TLS cert generated in ${tls_dir}"
    warn "Browsers will show a 'not private' warning on first visit — click through it once."

    local _port
    _port="$(awk -F= '/^GATEWAY_HTTP_ALT_PORT=/{print $2}' .env 2>/dev/null | tr -d ' ')"
    _port="${_port:-5173}"
    info "  HTTPS access: https://localhost:${_port}/ (same port as HTTP)"
    [ -n "$lan_ip" ] && info "  HTTPS LAN access: https://${lan_ip}:${_port}/"
}

# gen_token — emit a strong random secret (hex via openssl, base64 fallback).
gen_token() {
    if command -v openssl >/dev/null 2>&1; then
        openssl rand -hex 24
    else
        head -c 24 /dev/urandom | base64 | tr -dc 'A-Za-z0-9' | head -c 32
    fi
}

# find_free_port <preferred> [max_scan]
# Returns the first TCP port >= preferred that is not bound on 0.0.0.0 or 127.0.0.1.
# Scans at most max_scan ports (default 20) before giving up and returning preferred.
find_free_port() {
    local preferred="$1" max_scan="${2:-20}"
    local port="$preferred" checked=0
    while [ "$checked" -lt "$max_scan" ]; do
        # ss is fastest; fall back to /proc/net/tcp* if unavailable
        if command -v ss >/dev/null 2>&1; then
            ss -tnl 2>/dev/null | awk '{print $4}' | grep -qE ":${port}$" || { echo "$port"; return; }
        else
            grep -qE "$(printf '%04X' "$port")" /proc/net/tcp /proc/net/tcp6 2>/dev/null || { echo "$port"; return; }
        fi
        port=$((port + 1))
        checked=$((checked + 1))
    done
    echo "$preferred"
}

echo ""
hr
echo -e "  ${BOLD}Retail Virtual Try-On Platform — Setup & Launch${NC}"
hr
echo ""

# =============================================================================
# STEP 1: PREREQUISITES
# =============================================================================
echo -e "${BOLD}[1/4] Checking prerequisites...${NC}"
echo ""
PREREQ_FAIL=0

# --- Docker ---
if ! command -v docker &>/dev/null; then
    error "docker not found — install: https://docs.docker.com/get-docker/"
    PREREQ_FAIL=1
elif ! docker info &>/dev/null 2>&1; then
    error "Docker daemon not running — start: sudo systemctl start docker"
    PREREQ_FAIL=1
else
    DOCKER_VER=$(docker --version | awk '{print $3}' | tr -d ',')
    DOCKER_MAJOR=$(echo "$DOCKER_VER" | cut -d. -f1)
    if [ "${DOCKER_MAJOR:-0}" -lt 25 ]; then
        error "Docker $DOCKER_VER — version 25.0+ required"
        PREREQ_FAIL=1
    else
        ok "Docker $DOCKER_VER"
    fi
fi

# --- Docker Compose v2 ---
if docker compose version &>/dev/null 2>&1; then
    ok "Docker Compose $(docker compose version --short 2>/dev/null || echo 'v2')"
else
    error "Docker Compose v2 not found — install: https://docs.docker.com/compose/install/"
    PREREQ_FAIL=1
fi

# --- AMD GPUs ---
DETECTED_GPU_COUNT=0
if command -v rocm-smi &>/dev/null; then
    DETECTED_GPU_COUNT=$(rocm-smi --showid 2>/dev/null | grep -o "GPU\[[0-9]*\]" | sort -u | wc -l || echo 0)
fi
if [ "$DETECTED_GPU_COUNT" -eq 0 ] && [ -d /sys/class/drm ]; then
    DETECTED_GPU_COUNT=$(for f in /sys/class/drm/card*/device/vendor; do
        [ -f "$f" ] && cat "$f" 2>/dev/null; done | grep -c "0x1002" || echo 0)
fi

if [ "$DETECTED_GPU_COUNT" -lt "$MIN_REQUIRED_GPUS" ]; then
    error "${DETECTED_GPU_COUNT} AMD GPU(s) detected — minimum ${MIN_REQUIRED_GPUS} required"
    error "  GPU 0,1 → FASHN inference  |  GPU 2 → Ollama LLM  |  GPU 3 → ACE-Step music"
    PREREQ_FAIL=1
else
    GPU_NAMES=""
    command -v rocm-smi &>/dev/null && \
        GPU_NAMES=$(rocm-smi --showproductname 2>/dev/null | grep "Card Series" \
            | sed 's/.*: *//' | tr '\n' ',' | sed 's/,$//' || true)
    if [ -n "$GPU_NAMES" ]; then
        ok "${DETECTED_GPU_COUNT} AMD GPU(s): ${GPU_NAMES}"
    else
        ok "${DETECTED_GPU_COUNT} AMD GPU(s) detected"
    fi
fi

# --- ROCm device nodes ---
if [ ! -e /dev/kfd ]; then
    error "/dev/kfd not found — ROCm kernel driver not loaded"
    PREREQ_FAIL=1
elif [ ! -d /dev/dri ]; then
    error "/dev/dri not found — DRM subsystem not available"
    PREREQ_FAIL=1
else
    ok "ROCm device nodes (/dev/kfd, /dev/dri)"
fi

# --- Disk ---
FREE_GB=$(df -BG "$SCRIPT_DIR" | awk 'NR==2{gsub("G","",$4); print $4}')
if [ "${FREE_GB:-0}" -lt "$DISK_MIN_GB" ]; then
    error "Only ${FREE_GB} GB free — minimum ${DISK_MIN_GB} GB required (model weights)"
    PREREQ_FAIL=1
else
    ok "${FREE_GB} GB free disk"
fi

# --- RAM ---
TOTAL_RAM_GB=$(awk '/MemTotal/{printf "%d", $2/1024/1024}' /proc/meminfo 2>/dev/null || echo 0)
if [ "${TOTAL_RAM_GB:-0}" -lt "$RAM_MIN_GB" ]; then
    error "${TOTAL_RAM_GB} GB RAM — minimum ${RAM_MIN_GB} GB required"
    PREREQ_FAIL=1
else
    ok "${TOTAL_RAM_GB} GB total RAM"
fi

# --- CPU ---
CPU_CORE_COUNT="$(detect_cpu_core_count)"
ok "${CPU_CORE_COUNT} physical CPU core(s) — TTS pin range auto-detected below"

echo ""
[ "$PREREQ_FAIL" -ne 0 ] && die "Fix the errors above then re-run."

# =============================================================================
# STEP 2: ENVIRONMENT CONFIGURATION
# =============================================================================
echo -e "${BOLD}[2/4] Environment configuration${NC}"
echo ""

SKIP_ENV=0

# Capture whatever secrets the *current* .env holds before it is possibly
# deleted below. Postgres and RustFS/MinIO only apply the credentials baked
# into their env vars the first time their data volume is initialized —
# writing a new password/key into .env afterwards does NOT change the
# already-initialized store, it just makes DATABASE_URL / RUSTFS_* stop
# matching reality. We capture the prior values here (before any rm/cp
# touches .env) so that later, if the corresponding volume already exists,
# we can detect that and reuse the working credentials instead of silently
# handing out ones that will never authenticate.
_prior_pg_pw="$(env_file_value POSTGRES_PASSWORD .env 2>/dev/null || true)"
_prior_rustfs_key="$(env_file_value RUSTFS_ACCESS_KEY .env 2>/dev/null || true)"
_prior_rustfs_secret="$(env_file_value RUSTFS_SECRET_KEY .env 2>/dev/null || true)"

if [ -f .env ]; then
    warn ".env already exists."
    read -rp "  Overwrite it? [y/N]: " _ow || _ow="N"
    if [[ ! "${_ow:-N}" =~ ^[Yy]$ ]]; then
        info "Keeping existing .env"
        SKIP_ENV=1
        echo ""
    else
        rm .env
    fi
fi

if [ "$SKIP_ENV" -eq 0 ]; then
    [ -f .env.example ] || die ".env.example missing — cannot scaffold .env"
    cp .env.example .env

    echo "  Press Enter to accept the default shown in [brackets]."
    echo ""

    # --- Database ---
    echo -e "  ${BOLD}Database${NC}"
    if docker_volume_exists "_vto_pgdata" && [ -n "$_prior_pg_pw" ]; then
        warn "  An existing 'vto_pgdata' Docker volume was found."
        warn "  Postgres only applies POSTGRES_PASSWORD the first time it initializes"
        warn "  a fresh volume — entering a new password now would NOT update the"
        warn "  already-initialized database, and would silently break every"
        warn "  service's DATABASE_URL authentication (they'd all fail to connect)."
        warn "  Reusing the existing Postgres password from the previous .env."
        warn "  To set a genuinely new password, first wipe the volume (this"
        warn "  deletes all Postgres data): docker compose down -v"
        _pg_pw="$_prior_pg_pw"
        info "  Postgres password: (reused from previous .env)"
    else
        while true; do
            read -rsp "  Postgres password (will not be echoed): " _pg_pw; echo ""
            [ -z "${_pg_pw:-}" ] && { error "  Cannot be empty."; continue; }
            [ "$_pg_pw" = "password" ] || [ "$_pg_pw" = "changeme" ] && { warn "  Weak default — choose stronger."; continue; }
            read -rsp "  Confirm password: " _pg_pw2; echo ""
            [ "$_pg_pw" != "$_pg_pw2" ] && { error "  Passwords do not match."; continue; }
            break
        done
    fi
    sed -i "s|^POSTGRES_PASSWORD=.*|POSTGRES_PASSWORD=${_pg_pw}|" .env
    add_or_replace "DATABASE_URL" "postgresql://shared:${_pg_pw}@postgres:5432/shared_platform"
    echo ""

    # --- RustFS object store ---
    echo -e "  ${BOLD}RustFS / Milvus object store${NC}"
    if docker_volume_exists "_vto_milvus_minio" && [ -n "$_prior_rustfs_key" ] && [ -n "$_prior_rustfs_secret" ]; then
        warn "  An existing 'vto_milvus_minio' Docker volume was found."
        warn "  RustFS only applies its access/secret key the first time it"
        warn "  initializes a fresh volume — entering new ones now would NOT update"
        warn "  the already-initialized store, and Milvus would silently fail to"
        warn "  authenticate against it."
        warn "  Reusing the existing RustFS credentials from the previous .env."
        warn "  To set genuinely new credentials, first wipe the volume (this"
        warn "  deletes all Milvus/RustFS data): docker compose down -v"
        _rs_key="$_prior_rustfs_key"
        _rs_secret="$_prior_rustfs_secret"
        info "  RustFS access/secret key: (reused from previous .env)"
    else
        while true; do
            read -rp "  RustFS access key: " _rs_key || _rs_key=""
            [ -z "${_rs_key:-}" ] && { error "  Cannot be empty."; continue; }
            [ "$_rs_key" = "minioadmin" ] && { warn "  minioadmin is insecure — choose unique key."; continue; }
            break
        done
        while true; do
            read -rsp "  RustFS secret key (will not be echoed): " _rs_secret; echo ""
            [ -z "${_rs_secret:-}" ] && { error "  Cannot be empty."; continue; }
            [ "${#_rs_secret}" -lt 8 ] && { error "  Minimum 8 characters."; continue; }
            [ "$_rs_secret" = "minioadmin" ] && { warn " Choose a unique secret key."; continue; }
            read -rsp "  Confirm secret key: " _rs_secret2; echo ""
            [ "$_rs_secret" != "$_rs_secret2" ] && { error "  Do not match."; continue; }
            break
        done
    fi
    sed -i "s|^RUSTFS_ACCESS_KEY=.*|RUSTFS_ACCESS_KEY=${_rs_key}|" .env
    sed -i "s|^RUSTFS_SECRET_KEY=.*|RUSTFS_SECRET_KEY=${_rs_secret}|" .env
    echo ""

    # --- Host cache directories ---
    DEFAULT_HF="${HOME}/.cache/huggingface"
    echo -e "  ${BOLD}Host cache directories${NC}"
    read -rp "  HuggingFace cache dir [${DEFAULT_HF}]: " _hf_dir || _hf_dir=""
    _hf_dir="${_hf_dir:-${DEFAULT_HF}}"
    [ -d "$_hf_dir" ] || { mkdir -p "$_hf_dir"; ok "  Created ${_hf_dir}"; }
    sed -i "s|^HF_CACHE_DIR=.*|HF_CACHE_DIR=${_hf_dir}|" .env

    DEFAULT_OLLAMA="${HOME}/.ollama"
    read -rp "  Ollama data dir [${DEFAULT_OLLAMA}]: " _ollama_dir || _ollama_dir=""
    _ollama_dir="${_ollama_dir:-${DEFAULT_OLLAMA}}"
    [ -d "$_ollama_dir" ] || { mkdir -p "$_ollama_dir"; ok "  Created ${_ollama_dir}"; }
    sed -i "s|^OLLAMA_DATA_DIR=.*|OLLAMA_DATA_DIR=${_ollama_dir}|" .env

    # Kept separate from HF_CACHE_DIR — vto-tts is a pre-built root-running
    # image, and sharing a cache dir with the non-root services would put us
    # right back in Issue 6's root-owned-files trap.
    DEFAULT_LEMONADE="${HOME}/.cache/lemonade-hf"
    read -rp "  Lemonade/Kokoro TTS cache dir [${DEFAULT_LEMONADE}]: " _lemonade_dir || _lemonade_dir=""
    _lemonade_dir="${_lemonade_dir:-${DEFAULT_LEMONADE}}"
    [ -d "$_lemonade_dir" ] || { mkdir -p "$_lemonade_dir"; ok "  Created ${_lemonade_dir}"; }
    sed -i "s|^LEMONADE_CACHE_DIR=.*|LEMONADE_CACHE_DIR=${_lemonade_dir}|" .env

    # --- ACE-Step paths ---
    echo ""
    echo -e "  ${BOLD}ACE-Step build & model paths${NC}"
    _actx="${SCRIPT_DIR}/virtual_tryon/fashion-pipeline"
    ok "  ACE-Step build context: ${_actx}"
    sed -i "s|^ACESTEP_BUILD_CONTEXT=.*|ACESTEP_BUILD_CONTEXT=${_actx}|" .env

    _acache="${HOME}/.cache/acestep"
    [ -d "$_acache" ] || { mkdir -p "$_acache"; ok "  Created ${_acache}"; }
    ok "  ACE-Step model cache: ${_acache} (models auto-downloaded on first run)"
    sed -i "s|^ACESTEP_CACHE_DIR=.*|ACESTEP_CACHE_DIR=${_acache}|" .env
    echo ""

    # --- Ports ---
    echo -e "  ${BOLD}Frontend port${NC}"
    _alt_port_free="$(find_free_port 5173)"
    if [ "$_alt_port_free" != "5173" ]; then
        warn "  Port 5173 is in use — next free port is ${_alt_port_free}"
    fi
    read -rp "  Frontend port [${_alt_port_free}]: " _alt_port || _alt_port=""
    _alt_port="${_alt_port:-${_alt_port_free}}"
    if [ "$_alt_port" != "$_alt_port_free" ]; then
        _check="$(find_free_port "$_alt_port" 1)"
        [ "$_check" != "$_alt_port" ] && warn "  Port ${_alt_port} appears occupied — continuing anyway."
    fi
    add_or_replace "GATEWAY_HTTP_ALT_PORT" "${_alt_port}"
    ok "  Frontend port set to ${_alt_port}"
    echo ""

    # --- Gateway token ---
    # Reject the known-insecure default and require ≥16 chars so the agent
    # gateway is never left fail-open. Pressing Enter mints a strong token.
    echo -e "  ${BOLD}Security${NC}"
    _gw_default="$(gen_token)"
    while true; do
        read -rp "  OpenClaw gateway token [auto-generated strong token]: " _token || _token=""
        _token="${_token:-$_gw_default}"
        [ "$_token" = "vto-dev-token" ] && { error "  'vto-dev-token' is the insecure default — leave blank to auto-generate."; continue; }
        [ "${#_token}" -lt 16 ] && { error "  Minimum 16 characters — leave blank to auto-generate."; continue; }
        break
    done
    sed -i "s|^OPENCLAW_GATEWAY_TOKEN=.*|OPENCLAW_GATEWAY_TOKEN=${_token}|" .env

    # --- Ollama API key ---
    _ollama_default="$(gen_token)"
    while true; do
        read -rp "  Ollama API key [auto-generated strong key]: " _ollama_key || _ollama_key=""
        _ollama_key="${_ollama_key:-$_ollama_default}"
        [ "$_ollama_key" = "ollama-dev-token" ] && { error "  'ollama-dev-token' is the insecure default — leave blank to auto-generate."; continue; }
        [ "${#_ollama_key}" -lt 16 ] && { error "  Minimum 16 characters — leave blank to auto-generate."; continue; }
        break
    done
    add_or_replace "OLLAMA_API_KEY" "${_ollama_key}"

    # --- ACE-Step API key ---
    _acestep_default="$(gen_token)"
    while true; do
        read -rp "  ACE-Step API key [auto-generated strong key]: " _acestep_key || _acestep_key=""
        _acestep_key="${_acestep_key:-$_acestep_default}"
        [ "$_acestep_key" = "fashion-dev-token" ] && { error "  'fashion-dev-token' is the insecure default — leave blank to auto-generate."; continue; }
        [ "${#_acestep_key}" -lt 16 ] && { error "  Minimum 16 characters — leave blank to auto-generate."; continue; }
        break
    done
    sed -i "s|^ACESTEP_API_KEY=.*|ACESTEP_API_KEY=${_acestep_key}|" .env
    echo ""

    # --- Hugging Face token (compulsory) ---
    echo -e "  ${BOLD}Hugging Face${NC}"
    while true; do
        read -rsp "  Hugging Face token (will not be echoed): " _hf_token; echo ""
        [ -z "${_hf_token:-}" ] && { error "  Hugging Face token is required."; continue; }
        break
    done
    add_or_replace "HF_TOKEN" "${_hf_token}"
    ok "  Hugging Face token configured"
    echo ""

    # --- LLM model ---
    echo -e "  ${BOLD}LLM (Ollama)${NC}"
    read -rp "  Ollama model [qwen3.6:27b]: " _llm || _llm=""
    _llm="${_llm:-qwen3.6:27b}"
    sed -i "s|^OLLAMA_MODEL=.*|OLLAMA_MODEL=${_llm}|" .env
    sed -i "s|^OPENCLAW_LLM_MODEL=.*|OPENCLAW_LLM_MODEL=${_llm}|" .env
    sed -i "s|^LLM_MODEL=.*|LLM_MODEL=${_llm}|" .env
    echo ""

    # --- Optional HTTPS (self-signed), on by default — needed for mic/voice
    # input over a LAN IP, and shares the same port as HTTP (no extra port).
    echo -e "  ${BOLD}HTTPS${NC}"
    read -rp "  Enable self-signed HTTPS (recommended, needed for LAN mic/voice access)? [Y/n]: " _tls || _tls="Y"
    if [[ ! "${_tls:-Y}" =~ ^[Nn]$ ]]; then
        setup_tls
    fi
    echo ""

    ok ".env written"
    echo ""
fi

# Always stamp ROCm version and GPU visibility
add_or_replace "HSA_OVERRIDE_GFX_VERSION" "${HSA_VERSION}"
add_or_replace "ROCR_VISIBLE_DEVICES" "0,1,2,3"

# --- GPU device group GIDs (video / render) ---
# Docker group_add needs the host's actual numeric GIDs so GPU containers
# can access /dev/kfd and /dev/dri.  Auto-detect; fall back to common defaults.
_VIDEO_GID="$(getent group video 2>/dev/null | cut -d: -f3)"
_RENDER_GID="$(getent group render 2>/dev/null | cut -d: -f3)"
[ -z "${_VIDEO_GID:-}" ] && _VIDEO_GID="44"
[ -z "${_RENDER_GID:-}" ] && _RENDER_GID="109"
add_or_replace "VIDEO_GID" "${_VIDEO_GID}"
add_or_replace "RENDER_GID" "${_RENDER_GID}"
ok "GPU groups detected (video=${_VIDEO_GID}, render=${_RENDER_GID})"

# --- TTS CPU pinning (Kokoro/Lemonade is CPU-only) ---
# Auto-detect a safe range for this host instead of a hardcoded one; reserve
# core 0 for the OS and pin starting from core 1.
_nproc="$(nproc 2>/dev/null || echo 1)"
_tts_want=16
_existing_cpuset="$(awk -F= '/^TTS_CPUSET=/{print $2}' .env 2>/dev/null | tr -d ' ')"
if [ -z "${_existing_cpuset:-}" ]; then
    if [ "$_nproc" -gt "$_tts_want" ]; then
        _tts_default="1-${_tts_want}"
    else
        _tts_default=""
        warn "Only ${_nproc} logical CPU(s) detected — leaving TTS unpinned (unrestricted)."
    fi
    echo -e "  ${BOLD}TTS CPU pin${NC} (${_nproc} logical CPUs detected)"
    read -rp "  Cores to pin Lemonade/Kokoro TTS to [${_tts_default:-none}]: " _tts_cpuset || _tts_cpuset=""
    _tts_cpuset="${_tts_cpuset:-$_tts_default}"
    add_or_replace "TTS_CPUSET" "${_tts_cpuset}"
    [ -n "$_tts_cpuset" ] && ok "TTS pinned to cores ${_tts_cpuset}" || ok "TTS left unpinned"
fi

# When keeping an existing .env, still verify the alt port is free and update if not.
if [ "$SKIP_ENV" -eq 1 ]; then
    _existing_alt="$(awk -F= '/^GATEWAY_HTTP_ALT_PORT/{print $2}' .env 2>/dev/null | tr -d ' ')"
    _existing_alt="${_existing_alt:-5173}"
    _free_alt="$(find_free_port "$_existing_alt")"
    if [ "$_free_alt" != "$_existing_alt" ]; then
        warn "Existing alt port ${_existing_alt} is occupied — reassigning to ${_free_alt}"
        add_or_replace "GATEWAY_HTTP_ALT_PORT" "$_free_alt"
    fi
fi

ok "ROCm settings applied (HSA_OVERRIDE_GFX_VERSION=${HSA_VERSION}, ROCR_VISIBLE_DEVICES=0,1,2,3)"
echo ""

# =============================================================================
# STEP 3: BUILD & DOWNLOAD
# =============================================================================
echo -e "${BOLD}[3/5] Building images & downloading models...${NC}"
echo ""

info "Building service images one at a time (first run takes ~10–20 min for GPU images)..."
# A single `docker compose build` bakes every target together and cancels all
# in-flight/pending targets the moment any one fails, even independent ones.
# Building per-service keeps unrelated images building even if one Dockerfile is broken.
_build_failed=""
for _svc in $(docker compose config --services 2>/dev/null); do
    info "  Building ${_svc}..."
    if ! retry_command "Build ${_svc}" "$SETUP_DOCKER_RETRY_ATTEMPTS" "$SETUP_DOCKER_RETRY_DELAY_S" \
        docker compose build "$_svc"; then
        error "  ${_svc} failed to build"
        _build_failed="${_build_failed} ${_svc}"
    fi
done
[ -n "$_build_failed" ] && die "Docker build failed for:${_build_failed} — check output above."
ok "All images built"
echo ""

# ---------------------------------------------------------------------------
# ACE-Step model pre-download (one-time)
# ---------------------------------------------------------------------------
_acestep_cache="${ACESTEP_CACHE_DIR:-${HOME}/.cache/acestep}"
_hf_cache="${HF_CACHE_DIR:-${HOME}/.cache/huggingface}"
if [ ! -f "${_acestep_cache}/acestep-v15-turbo/model.safetensors" ]; then
    info "Downloading ACE-Step models (one-time, ~5 GB)..."
    if ! docker run --rm \
        -v "${_acestep_cache}:/app/acestep-models" \
        -v "${_hf_cache}:/root/.cache/huggingface" \
        -e HF_TOKEN="${HF_TOKEN}" \
        fashion-acestep:latest \
        python3 -c "
from huggingface_hub import snapshot_download
# Flat cache for docker-compose ACESTEP_CONFIG_PATH / ACESTEP_CHECKPOINTS_DIR
snapshot_download(
    'ACE-Step/Ace-Step1.5',
    local_dir='/app/acestep-models',
    local_dir_use_symlinks=False,
)
# HF hub cache for ACE-Step's internal from_pretrained() calls
snapshot_download('ACE-Step/Ace-Step1.5')
snapshot_download('Qwen/Qwen3-Embedding-0.6B')
print('ACE-Step models downloaded.')
"; then
        warn "ACE-Step model download failed — music generation may be unavailable."
        warn "You can retry later by running:"
        warn "  docker run --rm -v ${_acestep_cache}:/app/acestep-models -v ${_hf_cache}:/root/.cache/huggingface -e HF_TOKEN=... fashion-acestep:latest python3 -c \"from huggingface_hub import snapshot_download; snapshot_download('ACE-Step/Ace-Step1.5', local_dir='/app/acestep-models', local_dir_use_symlinks=False); snapshot_download('ACE-Step/Ace-Step1.5'); snapshot_download('Qwen/Qwen3-Embedding-0.6B')\""
    else
        ok "ACE-Step models ready in ${_acestep_cache}"
    fi
else
    ok "ACE-Step models already present in ${_acestep_cache}"
fi
echo ""

# ---------------------------------------------------------------------------
# Pre-create writable bind-mount host dirs. Docker creates a missing bind-mount
# source as root:root, which then blocks the non-root containers that need to
# write there; chmod run as the invoking host user can't fix that (only root
# can change another user's files), so this runs chmod inside a throwaway root container instead.
ensure_writable_dir() {
    local dir="$1"
    mkdir -p "$dir"
    docker run --rm -v "${dir}:/target" alpine:3.19 chmod -R 777 /target \
        || warn "  Could not fix permissions on ${dir} via a root container — check Docker is usable."
}

_output_dir="$(awk -F= '/^OUTPUT_DIR=/{print $2}' .env 2>/dev/null | tr -d ' ')"
_hf_cache_dir="$(awk -F= '/^HF_CACHE_DIR=/{print $2}' .env 2>/dev/null | tr -d ' ')"
_ollama_data_dir="$(awk -F= '/^OLLAMA_DATA_DIR=/{print $2}' .env 2>/dev/null | tr -d ' ')"
_acestep_cache_dir="$(awk -F= '/^ACESTEP_CACHE_DIR=/{print $2}' .env 2>/dev/null | tr -d ' ')"
ensure_writable_dir "${_output_dir:-./output}"
ensure_writable_dir "${_hf_cache_dir:-${HOME}/.cache/huggingface}"
ensure_writable_dir "${_ollama_data_dir:-${HOME}/.ollama}"
ensure_writable_dir "${_acestep_cache_dir:-${HOME}/.cache/acestep}"
ok "Bind-mount host directories ready and writable"
echo ""

info "Starting services..."
if ! retry_command "Docker Compose startup" "$SETUP_DOCKER_RETRY_ATTEMPTS" "$SETUP_DOCKER_RETRY_DELAY_S" \
    docker compose up -d; then
    die "Docker Compose startup failed — check output above."
fi
ok "Services started"
echo ""

# =============================================================================
# STEP 4: DATABASE SEED
# =============================================================================
echo -e "${BOLD}[4/5] Seeding database...${NC}"
echo ""

info "Waiting for Postgres to be ready..."
for i in $(seq 1 30); do
    docker compose exec -T postgres pg_isready -U shared -d shared_platform &>/dev/null && break
    [ "$i" -eq 30 ] && die "Postgres did not become ready in time"
    sleep 3
done
ok "Postgres ready"

# Run migrations and seed catalogue via the vto-api container which already
# has DATABASE_URL wired and all Python dependencies installed.
info "Running schema migrations..."
if ! docker compose exec -T vto-api python virtual_tryon/catalog/init_db.py; then
    die "Database init failed — check output above."
fi
ok "Schema applied"

ok "Catalogue seeded (60 items from catalogue/dataset.json via init_db.py)"

# =============================================================================
# STEP 5: ACE-STEP MODEL WARM-UP
# =============================================================================
echo ""
echo -e "${BOLD}[5/5] ACE-Step model warm-up...${NC}"
echo ""

info "Waiting for ACE-Step to load models (eager init)..."
for i in $(seq 1 60); do
    health_json=$(docker compose exec -T acestep curl -s http://localhost:8001/health 2>/dev/null || echo "")
    if echo "$health_json" | grep -q '"status":"ok"'; then
        if echo "$health_json" | grep -q '"models_initialized":true'; then
            ok "ACE-Step ready — models initialized"
            break
        fi
        if [ "$i" -eq 60 ]; then
            warn "ACE-Step health OK but models not initialized — continuing anyway"
        fi
        printf '.'
    else
        if [ "$i" -eq 60 ]; then
            warn "ACE-Step did not report healthy in time — music generation may be unavailable"
        fi
        printf '.'
    fi
    sleep 10
done
echo ""

echo ""
hr
ok "Platform is up."
hr
echo ""
echo "  GPU layout:"
echo "    GPU 0  →  FASHN inference server 0 (virtual try-on)"
echo "    GPU 1  →  FASHN inference server 1 (virtual try-on)"
echo "    GPU 2  →  Ollama LLM (OpenClaw agents)"
echo "    GPU 3  →  ACE-Step (music generation)"
_final_cpuset="$(awk -F= '/^TTS_CPUSET=/{print $2}' .env 2>/dev/null | tr -d ' ')"
echo "    CPU ${_final_cpuset:-unpinned} → Lemonade TTS (Kokoro)"
echo ""
echo "  Endpoints:"

# Resolve ports from .env
FRONTEND_PORT="$(awk -F= '/^GATEWAY_HTTP_ALT_PORT/{print $2}' .env 2>/dev/null | tr -d ' ')"
FRONTEND_PORT="${FRONTEND_PORT:-5173}"
LAN_IP="$(ip -4 -o addr show scope global 2>/dev/null | awk 'NR==1{print $4}' | cut -d/ -f1)"

echo "    Frontend   →  http://localhost:${FRONTEND_PORT}"
[ -n "$LAN_IP" ] && echo "    LAN        →  http://${LAN_IP}:${FRONTEND_PORT}"
_final_tls="$(awk -F= '/^TLS_ENABLED=/{print $2}' .env 2>/dev/null | tr -d ' ')"
if [ "${_final_tls:-0}" = "1" ]; then
    echo "    HTTPS      →  https://localhost:${FRONTEND_PORT}  (same port as HTTP)"
    [ -n "$LAN_IP" ] && echo "    HTTPS LAN  →  https://${LAN_IP}:${FRONTEND_PORT}  (voice input)"
fi
echo ""
echo "  First boot: the ollama-init service auto-pulls the LLM (~15 GB)."
echo "  Lemonade pulls Kokoro on first TTS request (~500 MB)."
echo ""
echo "  Useful commands:"
echo "    docker compose logs -f vto-api"
echo "    docker compose logs -f vto-tts"
echo "    docker compose logs -f openclaw"
echo "    docker compose down"
echo ""

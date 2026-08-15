#!/usr/bin/env bash
# serve_ltx.sh — Installe SGLang Diffusion et lance un serveur vidéo LTX (OpenAI-compatible
# /v1/videos), consommé par content_creator/agentic/sglang_video_client.py.
#
# Usage :
#   chmod +x serve_ltx.sh
#   export HF_TOKEN=hf_xxx          # requis (modèles LTX gated) — JAMAIS en dur ici
#   ./serve_ltx.sh                  # install (1re fois) puis serve LTX-2.3 two-stage resident
#   SKIP_INSTALL=1 ./serve_ltx.sh   # relance rapide sans réinstaller
#
# Tout est paramétrable par variables d'env (voir defaults ci-dessous). La doc SGLang couvre
# LTX-2 et LTX-2.3 ; pour un snapshot LTX-2.5, passe LTX_MODEL=Lightricks/LTX-2.5.
set -euo pipefail

# ---------------------------------------------------------------------------
# Configuration (surchargeable par l'env)
# ---------------------------------------------------------------------------
SKIP_INSTALL="${SKIP_INSTALL:-0}"
VENV="${VENV:-$HOME/sglang-env}"
HF_HOME="${HF_HOME:-/scratch/hf}"

LTX_MODEL="${LTX_MODEL:-Lightricks/LTX-2.3}"          # ou Lightricks/LTX-2 (voir doc)
LTX_PIPELINE="${LTX_PIPELINE:-LTX2TwoStagePipeline}"  # LTX2Pipeline | LTX2TwoStagePipeline | LTX2TwoStageHQPipeline
LTX_DEVICE_MODE="${LTX_DEVICE_MODE:-resident}"        # resident (VRAM haute) | original (VRAM serrée)
LTX_PORT="${LTX_PORT:-30000}"
LTX_NUM_GPUS="${LTX_NUM_GPUS:-1}"                     # 2 / 4 -> CFG parallel (cf. doc)
LTX_TP_SIZE="${LTX_TP_SIZE:-}"                        # ex. 2 pour un layout 4 GPU (TP2 + CFG)
LTX_CFG_PARALLEL="${LTX_CFG_PARALLEL:-auto}"          # auto (on si >=2 GPU) | 1 | 0
LTX_LORA_PATH="${LTX_LORA_PATH:-}"                    # ex. valiantcat/LTX-2.3-Transition-LORA
LTX_LORA_WEIGHT="${LTX_LORA_WEIGHT:-}"               # ex. ltx2.3-transition.safetensors

export HF_HOME
: "${HF_TOKEN:?HF_TOKEN non defini - exporte-le (modeles LTX gated) avant de lancer ce script}"
export HF_TOKEN

# ---------------------------------------------------------------------------
# 1) Installation (système + CUDA + uv + venv + sglang[diffusion] + ffmpeg)
# ---------------------------------------------------------------------------
if [ "$SKIP_INSTALL" != "1" ]; then
  echo "==> Installation des dépendances (SKIP_INSTALL=1 pour sauter)"
  apt-get update
  apt-get install -y python3.12-full ffmpeg nvitop
  apt -o Acquire::ForceIPv4=true install -y curl wget

  if ! command -v uv >/dev/null 2>&1; then
    curl -LsSf https://astral.sh/uv/install.sh | sh
  fi
  # shellcheck disable=SC1090
  source "$HOME/.local/bin/env"

  # CUDA toolkit 13.0 (aligné sur start_server.sh)
  if [ ! -d /usr/local/cuda-13.0 ]; then
    wget -q https://developer.download.nvidia.com/compute/cuda/repos/ubuntu2404/x86_64/cuda-keyring_1.1-1_all.deb
    dpkg -i cuda-keyring_1.1-1_all.deb
    [ -f /etc/apt/sources.list.d/cuda.list ] && mv /etc/apt/sources.list.d/cuda.list /root/cuda.list.bak || true
    apt -o Acquire::ForceIPv4=true update
    apt -o Acquire::ForceIPv4=true install -y cuda-toolkit-13-0
  fi

  # Environnement virtuel + SGLang Diffusion
  [ -d "$VENV" ] || uv venv "$VENV" --python 3.12
  # shellcheck disable=SC1091
  source "$VENV/bin/activate"
  uv pip install "sglang[diffusion]" --prerelease=allow
else
  echo "==> SKIP_INSTALL=1 : on saute l'installation"
  # shellcheck disable=SC1090
  [ -f "$HOME/.local/bin/env" ] && source "$HOME/.local/bin/env" || true
  # shellcheck disable=SC1091
  source "$VENV/bin/activate"
fi

# CUDA dans le PATH (pour deep_gemm / kernels)
export CUDA_HOME="${CUDA_HOME:-/usr/local/cuda-13.0}"
export PATH="$CUDA_HOME/bin:$PATH"
export LD_LIBRARY_PATH="$CUDA_HOME/lib64:${LD_LIBRARY_PATH:-}"
mkdir -p "$HF_HOME"

# ---------------------------------------------------------------------------
# 2) Construction de la commande `sglang serve` (selon le hardware / pipeline)
# ---------------------------------------------------------------------------
args=(serve
  --model-path "$LTX_MODEL"
  --pipeline-class-name "$LTX_PIPELINE"
  --port "$LTX_PORT"
)

# CFG parallel : recommandé dès 2 GPU (cf. doc, préféré à la sequence-parallel pour LTX).
if [ "$LTX_CFG_PARALLEL" = "auto" ]; then
  [ "$LTX_NUM_GPUS" -ge 2 ] && LTX_CFG_PARALLEL=1 || LTX_CFG_PARALLEL=0
fi
[ "$LTX_NUM_GPUS" -gt 1 ] && args+=(--num-gpus "$LTX_NUM_GPUS")
[ -n "$LTX_TP_SIZE" ] && args+=(--tp-size "$LTX_TP_SIZE")
[ "$LTX_CFG_PARALLEL" = "1" ] && args+=(--enable-cfg-parallel)

# device-mode : uniquement pour les pipelines two-stage (pas pour LTX2Pipeline one-stage).
if [ "$LTX_PIPELINE" != "LTX2Pipeline" ]; then
  args+=(--ltx2-two-stage-device-mode "$LTX_DEVICE_MODE")
fi

# LoRA optionnel (ex. transition LTX-2.3).
if [ -n "$LTX_LORA_PATH" ]; then
  args+=(--lora-path "$LTX_LORA_PATH")
  [ -n "$LTX_LORA_WEIGHT" ] && args+=(--lora-weight-name "$LTX_LORA_WEIGHT")
fi

# ---------------------------------------------------------------------------
# 3) Lancement
# ---------------------------------------------------------------------------
echo "==> Modèle    : $LTX_MODEL"
echo "==> Pipeline  : $LTX_PIPELINE"
echo "==> GPUs      : $LTX_NUM_GPUS (cfg_parallel=$LTX_CFG_PARALLEL, tp_size=${LTX_TP_SIZE:-1})"
echo "==> Endpoint  : http://0.0.0.0:$LTX_PORT/v1/videos"
echo "==> sglang ${args[*]}"
exec sglang "${args[@]}"

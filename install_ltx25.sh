#!/usr/bin/env bash
# install_ltx25.sh — Installe tout pour utiliser LTX-2.5 (video+audio) via `ltx-pipelines`
# (repo Lightricks/LTX-2). Télécharge les poids splittés LTX-2.5 (distilled) + l'upscaler
# spatial LTX-2.3 requis, puis prêt à générer en CLI (`python -m ltx_pipelines.distilled`).
#
# Réf. doc officielle LTX-2.5 — Option A (Python / ltx-pipelines).
# Pré-requis : Python >= 3.12, CUDA >= 12.7, PyTorch ~= 2.7 (amenés par `uv sync`).
#
# Usage :
#   chmod +x install_ltx25.sh
#   export HF_TOKEN=hf_xxx                      # requis (poids LTX gated) — JAMAIS en dur
#   ./install_ltx25.sh                          # install + download + LANCE le serveur /v1/videos
#   SERVE_ONLY=1 ./install_ltx25.sh            # relance rapide du serveur (rien à (ré)installer)
#   SERVE=0 ./install_ltx25.sh                  # install + download seulement (pas de serveur)
#   PROMPT="A golden retriever running..." ./install_ltx25.sh   # test CLI one-shot (pas de serveur)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ---------------------------------------------------------------------------
# Configuration (surchargeable par l'env)
# ---------------------------------------------------------------------------
SKIP_INSTALL="${SKIP_INSTALL:-0}"
SKIP_DOWNLOAD="${SKIP_DOWNLOAD:-0}"
SERVE="${SERVE:-1}"                               # 1 = lancer le serveur /v1/videos à la fin
LTX25_PORT="${LTX25_PORT:-30000}"
LTX_DIR="${LTX_DIR:-$HOME/LTX-2}"                 # clone du repo ltx-pipelines
MODELS_DIR="${MODELS_DIR:-$LTX_DIR/models}"       # où atterrissent les poids
HF_HOME="${HF_HOME:-/scratch/hf}"
SEED="${SEED:-42}"
OUT="${OUT:-output_ltx25.mp4}"

# SERVE_ONLY=1 : raccourci relance serveur (saute install + download)
if [ "${SERVE_ONLY:-0}" = "1" ]; then SKIP_INSTALL=1; SKIP_DOWNLOAD=1; SERVE=1; fi

export HF_HOME
: "${HF_TOKEN:?HF_TOKEN non defini - exporte-le (poids LTX gated) avant de lancer ce script}"
export HF_TOKEN

# ---------------------------------------------------------------------------
# 1) Dépendances système (Python 3.12, CUDA, ffmpeg, git, uv)
# ---------------------------------------------------------------------------
if [ "$SKIP_INSTALL" != "1" ]; then
  echo "==> Dépendances système (SKIP_INSTALL=1 pour sauter)"
  apt-get update
  apt-get install -y git python3.12-full ffmpeg
  apt -o Acquire::ForceIPv4=true install -y curl wget

  if ! command -v uv >/dev/null 2>&1; then
    curl -LsSf https://astral.sh/uv/install.sh | sh
  fi
  # shellcheck disable=SC1091
  source "$HOME/.local/bin/env"

  # CUDA toolkit (>= 12.7 requis ; on garde 13.0, aligné sur start_server.sh)
  if [ ! -d /usr/local/cuda-13.0 ]; then
    wget -q https://developer.download.nvidia.com/compute/cuda/repos/ubuntu2404/x86_64/cuda-keyring_1.1-1_all.deb
    dpkg -i cuda-keyring_1.1-1_all.deb
    [ -f /etc/apt/sources.list.d/cuda.list ] && mv /etc/apt/sources.list.d/cuda.list /root/cuda.list.bak || true
    apt -o Acquire::ForceIPv4=true update
    apt -o Acquire::ForceIPv4=true install -y cuda-toolkit-13-0
  fi
else
  echo "==> SKIP_INSTALL=1 : on saute les déps système"
  # shellcheck disable=SC1091
  [ -f "$HOME/.local/bin/env" ] && source "$HOME/.local/bin/env" || true
fi

export CUDA_HOME="${CUDA_HOME:-/usr/local/cuda-13.0}"
export PATH="$CUDA_HOME/bin:$PATH"
export LD_LIBRARY_PATH="$CUDA_HOME/lib64:${LD_LIBRARY_PATH:-}"
mkdir -p "$HF_HOME"

# ---------------------------------------------------------------------------
# 2) Repo ltx-pipelines + environnement (uv sync)
# ---------------------------------------------------------------------------
if [ ! -d "$LTX_DIR/.git" ]; then
  echo "==> Clone Lightricks/LTX-2 -> $LTX_DIR"
  git clone https://github.com/Lightricks/LTX-2.git "$LTX_DIR"
fi
cd "$LTX_DIR"
echo "==> uv sync (Python 3.12, PyTorch ~2.7…)"
uv sync
# shellcheck disable=SC1091
source .venv/bin/activate
# Déps du serveur HTTP (ltx25_server.py) dans le MÊME venv que ltx_pipelines.
uv pip install fastapi uvicorn requests

# ---------------------------------------------------------------------------
# 3) Téléchargement des poids (LTX-2.5 distilled split + upscaler LTX-2.3)
#    `hf download` utilise HF_TOKEN de l'env (pas de login interactif).
# ---------------------------------------------------------------------------
if [ "$SKIP_DOWNLOAD" != "1" ]; then
  echo "==> Download poids LTX-2.5 (distilled) -> $MODELS_DIR/ltx-2.5"
  hf download Lightricks/LTX-2.5 \
    diffusion_models/ltx-2.5-22b-distilled-transformer-bf16.safetensors \
    text_encoders/gemma4-12b-with-proj-ltx-2.5-bf16.safetensors \
    vae/ltx-2.5-video-vae-bf16.safetensors \
    vae/ltx-2.5-audio-vae-bf16.safetensors \
    model_patches/ltx-2.5-duration-head-bf16.safetensors \
    --local-dir "$MODELS_DIR/ltx-2.5"

  echo "==> Download upscaler spatial (hébergé sur LTX-2.3, requis par le pipeline distilled)"
  hf download Lightricks/LTX-2.3 \
    ltx-2.3-spatial-upscaler-x2-1.1.safetensors \
    --local-dir "$MODELS_DIR/ltx-2.3"
else
  echo "==> SKIP_DOWNLOAD=1 : on saute le téléchargement des poids"
fi

# ---------------------------------------------------------------------------
# 4) Chemins des composants + commande de génération
# ---------------------------------------------------------------------------
TRANSFORMER="$MODELS_DIR/ltx-2.5/diffusion_models/ltx-2.5-22b-distilled-transformer-bf16.safetensors"
TEXT_ENCODER="$MODELS_DIR/ltx-2.5/text_encoders/gemma4-12b-with-proj-ltx-2.5-bf16.safetensors"
VIDEO_VAE="$MODELS_DIR/ltx-2.5/vae/ltx-2.5-video-vae-bf16.safetensors"
AUDIO_VAE="$MODELS_DIR/ltx-2.5/vae/ltx-2.5-audio-vae-bf16.safetensors"
DURATION_HEAD="$MODELS_DIR/ltx-2.5/model_patches/ltx-2.5-duration-head-bf16.safetensors"
SPATIAL_UPSAMPLER="$MODELS_DIR/ltx-2.3/ltx-2.3-spatial-upscaler-x2-1.1.safetensors"

gen_cmd=(uv run python -m ltx_pipelines.distilled
  --transformer-path       "$TRANSFORMER"
  --text-encoder-path      "$TEXT_ENCODER"
  --video-vae-path         "$VIDEO_VAE"
  --audio-vae-path         "$AUDIO_VAE"
  --duration-head-path     "$DURATION_HEAD"
  --spatial-upsampler-path "$SPATIAL_UPSAMPLER"
  --seed "$SEED"
  --output-path "$OUT"
)

echo "==> Installation LTX-2.5 terminée."

# Chemins exportés pour le serveur HTTP (ltx25_server.py les lit).
export LTX25_MODELS_DIR="$MODELS_DIR"
export LTX25_PORT

if [ -n "${PROMPT:-}" ]; then
  # Mode test CLI one-shot (pas de serveur).
  echo "==> Génération de test (CLI) : \"$PROMPT\" -> $OUT"
  # Sans --num-frames : la duration-head choisit la longueur depuis le prompt (LTX-2.5+).
  exec "${gen_cmd[@]}" --prompt "$PROMPT"
elif [ "$SERVE" = "1" ]; then
  # Mode serveur : endpoint /v1/videos consommé par content_creator (sglang_video_client).
  echo "==> Lancement du serveur LTX-2.5 sur le port $LTX25_PORT"
  echo "    -> pointe un provider content_creator sur http://<ip>:$LTX25_PORT/v1"
  exec python "$SCRIPT_DIR/ltx25_server.py"
else
  echo "SERVE=0 : rien lancé. Options :"
  echo "  - Serveur /v1/videos : LTX25_MODELS_DIR=$MODELS_DIR python $SCRIPT_DIR/ltx25_server.py"
  echo "  - Génération CLI     : ${gen_cmd[*]} --prompt \"...\""
  echo "    (i2v: --image /chemin.jpg 0 1.0 · durée fixe: --num-frames 121 [%8==1] · low-VRAM: --quantization fp8-cast --offload cpu)"
fi

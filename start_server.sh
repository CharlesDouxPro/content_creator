# chmod +x start_server.sh
# ./start_server.sh 

#  or  directly bash start_server.sh
# ssh-keygen -R 51.159.118.245


apt install nvitop
apt install python3
apt update
apt install -y python3.12-full
apt -o Acquire::ForceIPv4=true install -y curl
curl -LsSf https://astral.sh/uv/install.sh | sh
curl -LsSf https://hf.co/cli/install.sh | sh
source ~/.local/bin/env
uv venv ~/sglang-env --python 3.12
source ~/sglang-env/bin/activate
uv pip install "sglang[diffusion]" --prerelease=allow
wget https://developer.download.nvidia.com/compute/cuda/repos/ubuntu2404/x86_64/cuda-keyring_1.1-1_all.deb
dpkg -i cuda-keyring_1.1-1_all.deb
mv /etc/apt/sources.list.d/cuda.list /root/cuda.list.bak
apt -o Acquire::ForceIPv4=true update
apt -o Acquire::ForceIPv4=true install -y cuda-toolkit-13-0
apt install -y ffmpeg
mkdir -p /scratch/hf
export CUDA_HOME=/usr/local/cuda-13.0
export PATH=$CUDA_HOME/bin:$PATH
export LD_LIBRARY_PATH=$CUDA_HOME/lib64:$LD_LIBRARY_PATH
export HF_HOME=/scratch/hf
echo 'export HF_HOME=/scratch/hf' >> ~/.bashrc
export TERM=xterm-256color
nvcc --version
python -c "import deep_gemm; print('ok')"

# ============================================================================
# Block storage (sdc) -> /mnt/models : monte le disque et y installe les poids.
# Idempotent : ne formate le block QUE s'il est vierge (un re-run ne réécrase
# pas les modèles déjà téléchargés) ; monte + persiste dans fstab par UUID.
# ============================================================================
BLOCK_DEV="${BLOCK_DEV:-/dev/sdc}"
MODELS_DIR="${MODELS_DIR:-/mnt/models}"

# 1) Filesystem : formate en ext4 uniquement si le block n'en a pas déjà un.
if ! blkid "$BLOCK_DEV" >/dev/null 2>&1; then
  echo "==> $BLOCK_DEV sans filesystem -> mkfs.ext4"
  mkfs.ext4 -F "$BLOCK_DEV"
fi

# 2) Montage + persistance (nofail : ne bloque pas le boot si le block est absent).
mkdir -p "$MODELS_DIR"
mountpoint -q "$MODELS_DIR" || mount "$BLOCK_DEV" "$MODELS_DIR"
BLOCK_UUID="$(blkid -s UUID -o value "$BLOCK_DEV")"
grep -q "$BLOCK_UUID" /etc/fstab || \
  echo "UUID=$BLOCK_UUID $MODELS_DIR ext4 defaults,nofail 0 2" >> /etc/fstab

# 2b) Agrandit le filesystem à TOUTE la taille du device (online, idempotent : no-op si déjà
# plein). INDISPENSABLE quand le block a été resizé après coup (volume agrandi mais ext4 resté
# à son ancienne taille) — sinon `No space left on device` alors que le volume est grand.
resize2fs "$BLOCK_DEV" || true
df -h "$MODELS_DIR"

# 3) Auth Hugging Face (poids LTX gated). Non-interactif si HF_TOKEN est exporté.
if [ -n "$HF_TOKEN" ]; then
  hf auth login --token "$HF_TOKEN" --add-to-git-credential
else
  hf auth login
fi

# 4) Poids DANS le block. LTX-2.5 (distilled split pack).
hf download Lightricks/LTX-2.5 \
  diffusion_models/ltx-2.5-22b-distilled-transformer-bf16.safetensors \
  text_encoders/gemma4-12b-with-proj-ltx-2.5-bf16.safetensors \
  vae/ltx-2.5-video-vae-bf16.safetensors \
  vae/ltx-2.5-audio-vae-bf16.safetensors \
  model_patches/ltx-2.5-duration-head-bf16.safetensors \
  --local-dir "$MODELS_DIR/ltx-2.5"

# Spatial upscaler (encore hébergé sur LTX-2.3, requis par le pipeline distilled).
hf download Lightricks/LTX-2.3 \
  ltx-2.3-spatial-upscaler-x2-1.1.safetensors \
  --local-dir "$MODELS_DIR/ltx-2.3"

# MiniMax-H3 (model_index.json + FL2VA + Ref2VA). Patterns en POSITIONNEL (pas --include) : sinon
# hf traite FL2VA/Ref2VA comme des fichiers explicites et IGNORE le --include (model_index.json manquant).
hf download MiniMaxAI/MiniMax-H3 "model_index.json" "FL2VA/*" "Ref2VA/*" \
  --local-dir "$MODELS_DIR/MiniMax-H3"

# 5) Exporte les chemins pour install_ltx25.sh / ltx25_server.py (poids dans le block).
export MODELS_DIR
export LTX25_MODELS_DIR="$MODELS_DIR"
grep -q "LTX25_MODELS_DIR=$MODELS_DIR" ~/.bashrc || \
  echo "export LTX25_MODELS_DIR=$MODELS_DIR" >> ~/.bashrc
echo "==> Modèles installés dans le block ($BLOCK_DEV) : $MODELS_DIR"

source sglang-env/bin/activate
export HF_HUB_CACHE=/mnt/models
export HF_HOME=/mnt/models/
sglang serve   --model-path /mnt/models/MiniMax-H3   --num-gpus 4   --tp-size 2   --ulysses-degree 2   --performance-mode speed   --host 0.0.0.0   --port 30010   --model-variant fl2va
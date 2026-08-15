#!/usr/bin/env bash
# Lance `sglang serve` pour MiniMax-H3 depuis les poids LOCAUX (volume monté). Tout est
# paramétrable par variables d'env (voir defaults). Aucun re-téléchargement si le modèle
# est déjà présent ; sinon (H3_DOWNLOAD=1 ou modèle absent) on le récupère sur le volume.
set -euo pipefail

export HF_HOME="${HF_HOME:-/models/hf}"
MODEL_PATH="${H3_MODEL_PATH:-/models/MiniMax-H3}"

# Téléchargement (repo COMPLET) uniquement si demandé ou si le modèle manque. Sur le volume
# (block storage), donc pas de « no space » sur le fs du container.
if [ "${H3_DOWNLOAD:-0}" = "1" ] || [ ! -e "$MODEL_PATH/model_index.json" ]; then
  : "${HF_TOKEN:?HF_TOKEN requis pour télécharger MiniMax-H3 (modèle gated)}"
  echo "==> Modèle absent/forcé -> hf download MiniMaxAI/MiniMax-H3 -> $MODEL_PATH"
  hf download MiniMaxAI/MiniMax-H3 --local-dir "$MODEL_PATH"
fi

echo "==> sglang serve MiniMax-H3 (model_path=$MODEL_PATH, port=${H3_PORT:-30010})"
exec sglang serve \
  --model-path "$MODEL_PATH" \
  --num-gpus "${H3_NUM_GPUS:-4}" \
  --tp-size "${H3_TP_SIZE:-2}" \
  --ulysses-degree "${H3_ULYSSES:-2}" \
  --performance-mode "${H3_PERF_MODE:-speed}" \
  --host 0.0.0.0 \
  --port "${H3_PORT:-30010}" \
  --model-variant "${H3_VARIANT:-fl2va}" \
  ${H3_EXTRA:-}

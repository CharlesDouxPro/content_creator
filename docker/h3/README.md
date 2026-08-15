# MiniMax-H3 (sglang) en Docker

Image sglang qui sert **MiniMax-H3** sur `/v1/videos` (port 30010), poids **montés**
depuis le block storage `/mnt/models` → aucun re-téléchargement, plus de « no space ».

Cible : **Scaleway GPU Instance** (driver NVIDIA + Docker + NVIDIA Container Toolkit
déjà pré-installés). Les Serverless Containers Scaleway n'ont pas de GPU.

## Build
```bash
docker build -t h3-sglang docker/h3
# si le tag CUDA par défaut n'existe pas :
# docker build -t h3-sglang --build-arg CUDA_IMAGE=nvidia/cuda:12.8.0-devel-ubuntu24.04 docker/h3
```

## Run
```bash
docker run --gpus all --rm \
  --ipc=host \                      # INDISPENSABLE en multi-GPU (NCCL/shm), sinon crash
  -v /mnt/models:/models \          # poids sur le block, réutilisés tels quels
  -e HF_TOKEN=$HF_TOKEN \           # requis seulement si le modèle doit être téléchargé
  -p 30010:30010 \
  h3-sglang
```
Détacher (tourne en fond) : ajoute `-d --name h3` (logs : `docker logs -f h3`).

## Paramètres (variables d'env, valeurs par défaut = ta commande actuelle)
| Var | Défaut | Rôle |
|-----|--------|------|
| `H3_MODEL_PATH` | `/models/MiniMax-H3` | chemin LOCAL des poids (dans le volume) |
| `H3_NUM_GPUS` | `4` | `--num-gpus` |
| `H3_TP_SIZE` | `2` | `--tp-size` |
| `H3_ULYSSES` | `2` | `--ulysses-degree` |
| `H3_PERF_MODE` | `speed` | `--performance-mode` |
| `H3_PORT` | `30010` | port exposé |
| `H3_VARIANT` | `fl2va` | `--model-variant` (`fl2va` / `ref2va`) |
| `H3_EXTRA` | *(vide)* | flags sglang additionnels |
| `H3_DOWNLOAD` | `0` | `1` = force le `hf download` du repo complet sur le volume |

Le modèle n'est téléchargé (repo complet) que s'il est **absent** (`model_index.json`
manquant) ou si `H3_DOWNLOAD=1` — sinon sglang lit directement `/models/MiniMax-H3`.

## Vérifier
```bash
curl -s http://localhost:30010/health        # ou l'IP publique de l'instance
```
Puis pointe un provider content_creator sur `http://<ip>:30010/v1` (rôle `video_generator`).

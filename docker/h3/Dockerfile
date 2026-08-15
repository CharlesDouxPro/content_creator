# syntax=docker/dockerfile:1
# Image sglang pour servir MiniMax-H3 (endpoint /v1/videos).
# Réplique l'install bare-metal de start_server.sh : CUDA (devel -> nvcc pour les kernels
# JIT type deep_gemm) + uv + sglang[diffusion]. Les POIDS ne sont PAS dans l'image :
# on monte /mnt/models en volume au run (-v /mnt/models:/models). Aucun re-téléchargement.
#
# Build : docker build -t h3-sglang docker/h3
#   (si le tag CUDA n'existe pas : --build-arg CUDA_IMAGE=nvidia/cuda:12.8.0-devel-ubuntu24.04)
ARG CUDA_IMAGE=nvidia/cuda:13.0.0-devel-ubuntu24.04
FROM ${CUDA_IMAGE}

ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
      python3.12-full curl wget git ffmpeg ca-certificates && \
    rm -rf /var/lib/apt/lists/*

# uv
RUN curl -LsSf https://astral.sh/uv/install.sh | sh
ENV PATH="/root/.local/bin:${PATH}"

# venv + sglang[diffusion] + hf CLI (dans le venv)
RUN uv venv /opt/sglang-env --python 3.12
ENV VIRTUAL_ENV=/opt/sglang-env
ENV PATH="/opt/sglang-env/bin:${PATH}"
RUN uv pip install "sglang[diffusion]" --prerelease=allow "huggingface_hub[cli]"

# CUDA runtime dans le PATH (kernels JIT) + cache HF sur le volume monté (jamais le fs du container)
ENV CUDA_HOME=/usr/local/cuda
ENV PATH="${CUDA_HOME}/bin:${PATH}"
ENV LD_LIBRARY_PATH="${CUDA_HOME}/lib64:${LD_LIBRARY_PATH}"
ENV HF_HOME=/models/hf

COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

EXPOSE 30010
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]

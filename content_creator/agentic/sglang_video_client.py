#!/usr/bin/env python3
"""
sglang_video_client.py — Client de l'endpoint vidéo ASYNCHRONE OpenAI-compatible de SGLang.

Sert les modèles vidéo AUDIOVISUELS servis par un serveur SGLang distant (MiniMax-H3, LTX-2.5) :
  1. POST   {base}/v1/videos            -> {id}         (soumission d'un job)
  2. GET    {base}/v1/videos/{id}       -> {status}     (poll : completed / failed / …)
  3. GET    {base}/v1/videos/{id}/content -> MP4        (téléchargement une fois "completed")

Contrat de sortie : MP4 H.264 24 fps + une piste AAC stéréo 32 kHz (l'audio est GÉNÉRÉ par le
modèle). Les URI de conditions (`file://…`) doivent être visibles PAR le serveur SGLang ; une URL
http(s) publique (ex. GCS) est en général joignable par le serveur distant.
"""

import json
import time

import requests


def _videos_url(base_url: str) -> str:
    """Dérive l'endpoint `/v1/videos` depuis le base_url d'un provider (OpenAI-compatible).
    Tolère les formes `…`, `…/v1`, `…/v1/openai`."""
    b = base_url.rstrip("/")
    if b.endswith("/openai"):
        b = b[: -len("/openai")]
    if b.endswith("/v1"):
        return f"{b}/videos"
    return f"{b}/v1/videos"


def _headers(token: str = None) -> dict:
    h = {"Content-Type": "application/json"}
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


def submit(base_url: str, token: str, payload: dict) -> str:
    """Soumet un job de génération, retourne son `id`."""
    r = requests.post(_videos_url(base_url), json=payload, headers=_headers(token), timeout=60)
    if r.status_code >= 400:
        raise RuntimeError(f"SGLang /videos {r.status_code}: {r.text[:400]}")
    data = r.json()
    vid = data.get("id")
    if not vid:
        raise RuntimeError(f"SGLang: réponse sans id ({json.dumps(data)[:200]})")
    return vid


def wait(base_url: str, token: str, video_id: str, timeout: float = 1800, interval: float = 3) -> None:
    """Poll le statut du job jusqu'à `completed` (retour) ou `failed`/timeout (lève)."""
    url = f"{_videos_url(base_url)}/{video_id}"
    start = time.time()
    while True:
        r = requests.get(url, headers=_headers(token), timeout=30)
        if r.status_code >= 400:
            raise RuntimeError(f"SGLang status {r.status_code}: {r.text[:300]}")
        status = r.json().get("status")
        if status == "completed":
            return
        if status == "failed":
            raise RuntimeError(f"SGLang job {video_id} failed: {r.text[:300]}")
        if time.time() - start > timeout:
            raise RuntimeError(f"SGLang job {video_id} timeout après {timeout:g}s (dernier statut: {status})")
        time.sleep(interval)


def download(base_url: str, token: str, video_id: str, dest: str) -> str:
    """Télécharge le MP4 d'un job terminé vers `dest`."""
    url = f"{_videos_url(base_url)}/{video_id}/content"
    r = requests.get(url, headers=_headers(token), stream=True, timeout=300, allow_redirects=True)
    r.raise_for_status()
    with open(dest, "wb") as f:
        for chunk in r.iter_content(chunk_size=8192):
            f.write(chunk)
    return dest


def generate(base_url: str, token: str, payload: dict, dest: str,
             timeout: float = 1800, interval: float = 3) -> str:
    """Soumet -> attend -> télécharge. Retourne le chemin du MP4."""
    vid = submit(base_url, token, payload)
    wait(base_url, token, vid, timeout=timeout, interval=interval)
    return download(base_url, token, vid, dest)

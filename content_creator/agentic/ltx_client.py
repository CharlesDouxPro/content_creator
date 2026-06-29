#!/usr/bin/env python3
"""
ltx_client.py — Client HTTP pour le serveur d'inférence LTX-2.3 local.

Le serveur (cf. repo LTX-video-server, FastAPI) expose deux endpoints qui
renvoient DIRECTEMENT un fichier MP4 dans le corps de la réponse :

  - POST /generate : text-to-video / image-to-video (+ passe HDR optionnelle)
  - POST /lipsync  : re-synchro labiale sur un nouveau dialogue (vidéo source requise)

Ce module est l'alternative LOCALE aux appels DeepInfra (Wan/Pruna) de
capabilities.py. Le routage est piloté par VIDEO_BACKEND_CONFIG (flags
USE_LTX_BROLL / USE_LTX_LIPSYNC dans le .env).

Contraintes du serveur (gérées ici) :
  - résolution multiple de 64 (le serveur arrondit, mais on envoie déjà du propre),
  - num_frames au format 8k+1 (snappé ici via _snap_frames),
  - 1 seul GPU (gpu_limit=1) -> on envoie les requêtes UNE PAR UNE (pas de parallèle).
"""

import os

import requests

from content_creator.config.config import VIDEO_BACKEND_CONFIG as _CFG


def _base_url() -> str:
    return _CFG["ltx_server_url"].rstrip("/")


def _timeout() -> int:
    return _CFG["ltx_timeout"]


def _snap_frames(num_frames: int) -> int:
    """Snappe au format 8k+1 le plus proche en-dessous (contrainte LTX), min 9."""
    return max(9, ((num_frames - 1) // 8) * 8 + 1)


def frames_for_duration(duration_s: float, frame_rate: float = None) -> int:
    """Convertit une durée (s) en num_frames valide (8k+1) pour le frame_rate donné."""
    fr = frame_rate if frame_rate is not None else _CFG["ltx_frame_rate"]
    return _snap_frames(int(round(duration_s * fr)) + 1)


def health() -> dict:
    """État du serveur LTX. Lève une erreur lisible si injoignable / pas prêt."""
    try:
        r = requests.get(f"{_base_url()}/health", timeout=10)
        r.raise_for_status()
    except requests.RequestException as e:
        raise RuntimeError(
            f"Serveur LTX injoignable à {_base_url()} — est-il démarré (./run_server.sh) ? ({e})"
        ) from e
    data = r.json()
    if not data.get("ready"):
        raise RuntimeError(f"Serveur LTX pas prêt (chargement en cours) : {data}")
    return data


def _post_video(endpoint: str, payload: dict, dest: str) -> str:
    """POST vers le serveur LTX ; le corps de la réponse EST le MP4. Écrit `dest`."""
    url = f"{_base_url()}{endpoint}"
    r = requests.post(url, json=payload, timeout=_timeout())
    if r.status_code != 200:
        # Le serveur renvoie un JSON {"detail": "..."} sur erreur (507 OOM, 503, 500…).
        detail = ""
        try:
            detail = r.json().get("detail", "")
        except Exception:  # noqa: BLE001
            detail = r.text[:300]
        raise RuntimeError(f"LTX {endpoint} -> HTTP {r.status_code}: {detail}")
    os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
    with open(dest, "wb") as f:
        f.write(r.content)
    return dest


def generate(prompt: str, dest: str, image_path: str = None, seed: int = 42,
             num_frames: int = None, duration_s: float = None,
             width: int = None, height: int = None,
             frame_rate: float = None, image_strength: float = 1.0,
             num_inference_steps: int = None, hdr: bool = None,
             hdr_high_quality: bool = None) -> str:
    """text-to-video ou image-to-video via POST /generate. Écrit le MP4 dans `dest`.

    - image_path : chemin LOCAL d'une image -> active l'image-to-video (envoyé en base64).
    - num_frames OU duration_s : longueur de la vidéo (duration_s converti en 8k+1).
    Tous les `None` retombent sur les défauts (config .env ou défaut serveur LTX).
    """
    import base64

    width = width or _CFG["ltx_width"]
    height = height or _CFG["ltx_height"]
    fr = frame_rate if frame_rate is not None else _CFG["ltx_frame_rate"]
    hdr = _CFG["ltx_hdr"] if hdr is None else hdr

    if num_frames is None:
        num_frames = frames_for_duration(duration_s, fr) if duration_s else 121
    else:
        num_frames = _snap_frames(num_frames)

    payload = {
        "prompt": prompt,
        "seed": seed,
        "width": width,
        "height": height,
        "num_frames": num_frames,
        "frame_rate": fr,
        "hdr": hdr,
    }
    if num_inference_steps is not None:
        payload["num_inference_steps"] = num_inference_steps
    if hdr_high_quality is not None:
        payload["hdr_high_quality"] = hdr_high_quality
    if image_path:
        # On envoie l'image en base64 (le serveur accepte chemin local OU base64 ;
        # le base64 évite que le chemin doive exister côté serveur).
        with open(image_path, "rb") as f:
            payload["image"] = base64.b64encode(f.read()).decode("ascii")
        payload["image_strength"] = image_strength

    return _post_video("/generate", payload, dest)


def lipsync(reference_video_path: str, prompt: str, dest: str, seed: int = 42,
            width: int = None, height: int = None,
            reference_strength: float = 1.0) -> str:
    """Re-synchro labiale via POST /lipsync. La vidéo de référence (chemin local,
    envoyée en base64) fournit frames + audio ; `prompt` est le nouveau dialogue.
    num_frames/fps sont dérivés de la vidéo source par le serveur."""
    import base64

    width = width or _CFG["ltx_width"]
    height = height or _CFG["ltx_height"]
    with open(reference_video_path, "rb") as f:
        ref_b64 = base64.b64encode(f.read()).decode("ascii")
    payload = {
        "prompt": prompt,
        "reference_video": ref_b64,
        "reference_strength": reference_strength,
        "seed": seed,
        "width": width,
        "height": height,
    }
    return _post_video("/lipsync", payload, dest)

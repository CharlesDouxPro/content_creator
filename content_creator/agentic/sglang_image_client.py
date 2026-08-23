#!/usr/bin/env python3
"""
sglang_image_client.py — Client des endpoints IMAGE OpenAI-compatibles de SGLang (MiniMax-H3).

Contrairement à la vidéo (async : submit -> poll -> download, cf. sglang_video_client.py),
la génération d'image est SYNCHRONE : la réponse JSON porte directement l'image, au format
OpenAI `{"data": [{"b64_json": "..."} | {"url": "..."}]}`.

  1. POST {base}/v1/images/generations  -> {"data": [{b64_json|url}]}   (texte -> image)
  2. POST {base}/v1/images/edits         -> idem (multipart : image + prompt + mask?)

Par défaut on demande `response_format="b64_json"` : l'octet de l'image est inline dans la
réponse (aucun stockage cloud requis côté serveur), on le décode et on l'écrit sur `dest`.
Repli : si le serveur renvoie une `url` (ou un chemin `/v1/images/{id}/content`), on la
télécharge.
"""

import base64
import json

import requests


def _images_url(base_url: str, action: str) -> str:
    """Dérive `/v1/images/<action>` depuis le base_url d'un provider (OpenAI-compatible).
    Tolère les formes `…`, `…/v1`, `…/v1/openai` (comme sglang_video_client._videos_url)."""
    b = base_url.rstrip("/")
    if b.endswith("/openai"):
        b = b[: -len("/openai")]
    if b.endswith("/v1"):
        return f"{b}/images/{action}"
    return f"{b}/v1/images/{action}"


def _headers(token: str = None) -> dict:
    h = {}
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


def _write_first_image(base_url: str, token: str, data: dict, dest: str) -> str:
    """Écrit la 1ère image de la réponse OpenAI sur `dest`. Gère b64_json et url."""
    items = data.get("data") or []
    if not items:
        raise RuntimeError(f"SGLang images: réponse sans data ({json.dumps(data)[:200]})")
    item = items[0]
    b64 = item.get("b64_json")
    if b64:
        with open(dest, "wb") as f:
            f.write(base64.b64decode(b64))
        return dest
    url = item.get("url")
    if not url:
        raise RuntimeError(f"SGLang images: item sans b64_json ni url ({json.dumps(item)[:200]})")
    # url relative (`/v1/images/{id}/content`) -> préfixer le host du provider.
    if url.startswith("/"):
        root = base_url.rstrip("/")
        for suffix in ("/openai", "/v1"):
            if root.endswith(suffix):
                root = root[: -len(suffix)]
        url = f"{root}{url}"
    r = requests.get(url, headers=_headers(token), stream=True, timeout=300, allow_redirects=True)
    r.raise_for_status()
    with open(dest, "wb") as f:
        for chunk in r.iter_content(chunk_size=8192):
            f.write(chunk)
    return dest


def generate(base_url: str, token: str, payload: dict, dest: str, timeout: float = 600) -> str:
    """Génère une image (texte -> image) et l'écrit sur `dest`. Retourne le chemin.
    `payload` : au moins {model, prompt} ; `response_format` forcé à b64_json si absent."""
    body = dict(payload)
    body.setdefault("response_format", "b64_json")
    r = requests.post(_images_url(base_url, "generations"),
                      json=body, headers={**_headers(token), "Content-Type": "application/json"},
                      timeout=timeout)
    if r.status_code >= 400:
        raise RuntimeError(f"SGLang /images/generations {r.status_code}: {r.text[:400]}")
    return _write_first_image(base_url, token, r.json(), dest)


def edit(base_url: str, token: str, prompt: str, image_path: str, dest: str,
         model: str = None, mask_path: str = None, extra: dict = None,
         timeout: float = 600) -> str:
    """Édite/retouche une image (multipart `/v1/images/edits`) et l'écrit sur `dest`.
    `extra` : champs additionnels (seed, size, num_inference_steps, negative_prompt, …)."""
    data = {"prompt": prompt, "response_format": "b64_json"}
    if model:
        data["model"] = model
    for k, v in (extra or {}).items():
        if v is not None:
            data[k] = str(v)
    files = {"image": open(image_path, "rb")}
    if mask_path:
        files["mask"] = open(mask_path, "rb")
    try:
        r = requests.post(_images_url(base_url, "edits"),
                          data=data, files=files,
                          headers=_headers(token), timeout=timeout)
    finally:
        for fh in files.values():
            try:
                fh.close()
            except Exception:
                pass
    if r.status_code >= 400:
        raise RuntimeError(f"SGLang /images/edits {r.status_code}: {r.text[:400]}")
    return _write_first_image(base_url, token, r.json(), dest)

"""
providers.py — Registre des providers d'inférence (base_url + api_key), NOMMÉS.

C'est désormais LA source de vérité des providers (remplace le `PROVIDERS` codé en dur
dans content_creator/config/schema.py). On définit/nomme ses providers, on les renseigne
une seule fois depuis le front, et les channels les référencent par nom (`provider_id`).

Stockage DURABLE : un objet JSON dans le bucket GCS (survit aux redémarrages, même en
déploiement éphémère). Fallback fichier local si GCS indisponible (dev). Défauts seedés
depuis l'env pour ne pas casser les channels existants tant que rien n'est renseigné.
"""

from __future__ import annotations

import os
import json

from pydantic import BaseModel

from content_creator.config.config import API_KEYS, GCS_CONFIG, VIDEO_BACKEND_CONFIG

try:  # google-cloud-storage est optionnel (absent en dev pur local)
    from google.cloud import storage
except Exception:  # pragma: no cover
    storage = None


class Provider(BaseModel):
    base_url: str = ""
    api_key: str = ""


# Emplacements de stockage.
GCS_BLOB = os.getenv("PROVIDERS_GCS_BLOB", "config/providers.json")
LOCAL_PATH = os.path.join(os.path.dirname(__file__), "providers.json")

# Défauts seedés depuis l'env — identiques à l'ancien registre de schema.py. Servent tant
# que le front n'a rien enregistré (providers.json absent), pour ne pas casser channels.json.
DEFAULT_PROVIDERS: dict[str, Provider] = {
    "arlq_deepinfra": Provider(
        base_url="https://api.deepinfra.com/v1/openai",
        api_key=os.getenv("ARLQ_DEEPINFRA_TOKEN", ""),
    ),
    "charles_deepinfra": Provider(
        base_url="https://api.deepinfra.com/v1/openai",
        api_key=os.getenv("CHARLES_DEEPINFRA_TOKEN", ""),
    ),
    "google_tts": Provider(
        base_url="https://texttospeech.googleapis.com/v1",
        api_key=str(API_KEYS.get("google_tts_api_key") or ""),
    ),
    "elevenlabs": Provider(
        base_url="https://api.elevenlabs.io",
        api_key=os.getenv("ELEVENLABS_API_KEY", ""),
    ),
    "ltx_local": Provider(
        base_url=str(VIDEO_BACKEND_CONFIG["ltx_server_url"]),
        api_key="",
    ),
}


# ---------------------------------------------------------------------------
# Stockage : GCS (durable) prioritaire, fichier local en cache/fallback.
# ---------------------------------------------------------------------------
def _bucket():
    """Bucket GCS si dispo (lib + api-key.json présents), sinon None -> fallback local."""
    if storage is None:
        return None
    key = GCS_CONFIG.get("json_key_path")
    if not key or not os.path.exists(key):
        return None
    try:
        client = storage.Client.from_service_account_json(key)
        return client.bucket(GCS_CONFIG["bucket_name"])
    except Exception as e:  # pragma: no cover
        print(f"[providers GCS] indisponible: {e}")
        return None


def _read_gcs() -> dict | None:
    b = _bucket()
    if b is None:
        return None
    try:
        blob = b.blob(GCS_BLOB)
        if not blob.exists():
            return None
        return json.loads(blob.download_as_text())
    except Exception as e:  # pragma: no cover
        print(f"[providers GCS] lecture échouée: {e}")
        return None


def _read_local() -> dict | None:
    if not os.path.exists(LOCAL_PATH):
        return None
    with open(LOCAL_PATH, encoding="utf-8") as f:
        return json.load(f)


def _write_local(text: str) -> None:
    tmp = f"{LOCAL_PATH}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, LOCAL_PATH)


def _write_gcs(text: str) -> None:
    b = _bucket()
    if b is None:
        return
    try:
        b.blob(GCS_BLOB).upload_from_string(text, content_type="application/json")
    except Exception as e:  # pragma: no cover
        print(f"[providers GCS] écriture échouée: {e}")


# ---------------------------------------------------------------------------
# API publique.
# ---------------------------------------------------------------------------
def load_providers() -> dict[str, Provider]:
    """GCS d'abord (durable), sinon fichier local, sinon défauts env."""
    data = _read_gcs()
    if data is None:
        data = _read_local()
    if data is None:
        return {name: p.model_copy() for name, p in DEFAULT_PROVIDERS.items()}
    return {name: Provider.model_validate(cfg) for name, cfg in data.items()}


def save_providers(providers: dict[str, Provider]) -> None:
    """Écrit GCS (durable) + cache local."""
    payload = {name: p.model_dump() for name, p in providers.items()}
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    _write_local(text)
    _write_gcs(text)


def get_provider(name: str = "common") -> Provider:
    """Renvoie un provider par nom."""
    providers = load_providers()
    if name not in providers:
        raise KeyError(f"provider inconnu: {name!r} (dispo: {sorted(providers)})")
    return providers[name]


def upsert_provider(name: str, base_url: str, api_key: str) -> Provider:
    """Crée ou met à jour un provider nommé. api_key vide -> on garde l'existante."""
    providers = load_providers()
    kept = providers[name].api_key if name in providers else ""
    providers[name] = Provider(base_url=base_url, api_key=api_key or kept)
    save_providers(providers)
    return providers[name]


def delete_provider(name: str) -> bool:
    """Supprime un provider nommé. Renvoie False s'il n'existait pas."""
    providers = load_providers()
    if name not in providers:
        return False
    del providers[name]
    save_providers(providers)
    return True

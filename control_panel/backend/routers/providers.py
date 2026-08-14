"""
providers.py — CRUD des providers d'inférence (base_url + api_key), NOMMÉS.

Définis/nommés/renseignés ici (une seule fois), stockés durablement (GCS), et
référencés par les channels via `provider_id`. La clé n'est JAMAIS renvoyée en clair
(pattern `api_key_set: bool`). Source de vérité : inference_engine.providers.
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from inference_engine.providers import (
    delete_provider,
    load_providers,
    upsert_provider,
)

router = APIRouter(prefix="/api/providers", tags=["providers"])


class ProviderOut(BaseModel):
    name: str
    base_url: str
    api_key_set: bool            # la clé est-elle définie ? (jamais exposée en clair)


class ProviderIn(BaseModel):
    base_url: str = ""
    api_key: str = ""            # vide en update = on garde la clé existante


@router.get("", response_model=list[ProviderOut])
def list_providers() -> list[ProviderOut]:
    return [
        ProviderOut(name=name, base_url=p.base_url, api_key_set=bool(p.api_key))
        for name, p in load_providers().items()
    ]


@router.put("/{name}", response_model=ProviderOut)
def put_provider(name: str, body: ProviderIn) -> ProviderOut:
    """Crée OU met à jour un provider nommé (upsert)."""
    p = upsert_provider(name, base_url=body.base_url, api_key=body.api_key)
    return ProviderOut(name=name, base_url=p.base_url, api_key_set=bool(p.api_key))


@router.delete("/{name}", status_code=204)
def remove_provider(name: str) -> None:
    if not delete_provider(name):
        raise HTTPException(404, f"provider inconnu: {name}")

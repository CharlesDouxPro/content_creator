"""
channels.py — CRUD des channels. La validation Pydantic (schema.Channel) se fait à
l'écriture : un payload invalide (skill/provider inconnu, champ en trop) est rejeté en 422.
"""

from fastapi import APIRouter, HTTPException

from content_creator.config.schema import Channel
from control_panel.backend import store

router = APIRouter(prefix="/api/channels", tags=["channels"])


@router.get("", response_model=list[Channel])
def list_channels() -> list[Channel]:
    return store.list_channels()


@router.post("", response_model=Channel, status_code=201)
def create_channel(channel: Channel) -> Channel:
    if store.get_channel(channel.name) is not None:
        raise HTTPException(409, f"channel déjà existant: {channel.name}")
    return store.upsert_channel(channel)


@router.get("/{name}", response_model=Channel)
def get_channel(name: str) -> Channel:
    channel = store.get_channel(name)
    if channel is None:
        raise HTTPException(404, f"channel inconnu: {name}")
    return channel


@router.put("/{name}", response_model=Channel)
def update_channel(name: str, channel: Channel) -> Channel:
    if store.get_channel(name) is None:
        raise HTTPException(404, f"channel inconnu: {name}")
    # Renommage autorisé : refuse si le nouveau nom heurte un AUTRE channel.
    if channel.name != name and store.get_channel(channel.name) is not None:
        raise HTTPException(409, f"channel déjà existant: {channel.name}")
    return store.upsert_channel(channel, original_name=name)


@router.delete("/{name}", status_code=204)
def delete_channel(name: str) -> None:
    if not store.delete_channel(name):
        raise HTTPException(404, f"channel inconnu: {name}")

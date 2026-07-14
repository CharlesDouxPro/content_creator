"""
store.py — Accès thread-safe aux channels (source de vérité = config/channels.json).

Fine couche au-dessus de schema.load_channels/save_channels : un lock protège les
mutations concurrentes (le panneau et un run peuvent lire/écrire en parallèle).
"""

import threading

from content_creator.config.schema import Channel, load_channels, save_channels

_lock = threading.RLock()


def list_channels() -> list[Channel]:
    with _lock:
        return load_channels()


def get_channel(name: str) -> Channel | None:
    with _lock:
        return next((c for c in load_channels() if c.name == name), None)


def upsert_channel(channel: Channel, original_name: str | None = None) -> Channel:
    """Crée ou remplace un channel. `original_name` = nom avant renommage (PUT)."""
    with _lock:
        channels = load_channels()
        key = original_name or channel.name
        idx = next((i for i, c in enumerate(channels) if c.name == key), None)
        if idx is None:
            channels.append(channel)
        else:
            channels[idx] = channel
        save_channels(channels)
        return channel


def delete_channel(name: str) -> bool:
    with _lock:
        channels = load_channels()
        kept = [c for c in channels if c.name != name]
        if len(kept) == len(channels):
            return False
        save_channels(kept)
        return True

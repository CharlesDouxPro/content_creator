"""
gcs_util.py — Petits utilitaires GCS pour le panneau (bucket public).

Réutilise `GCSManager` (upload) et `GCS_CONFIG` (nom du bucket) de content_creator.
Le storage est PUBLIC (choix assumé du POC) : l'URL publique se construit directement.
"""

from content_creator.config.config import GCS_CONFIG
from content_creator.pipelines.modules import GCSManager

BUCKET = GCS_CONFIG["bucket_name"]

# Préfixe GCS où sont rangés les personnages uploadés depuis le panneau.
CHARACTERS_PREFIX = "avatars/"

IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".gif")
VIDEO_EXTS = (".mp4", ".mov", ".webm")


def public_url(blob: str) -> str:
    """URL publique d'un blob (bucket public)."""
    return f"https://storage.googleapis.com/{BUCKET}/{blob}"


def manager() -> GCSManager:
    return GCSManager()

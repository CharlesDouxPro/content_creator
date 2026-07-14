"""
characters.py — Upload d'images de personnages vers GCS (bucket public) + bibliothèque.

L'URL publique retournée se met telle quelle dans `Character.image` d'un channel :
`_resolve_characters` (video_agent) sait consommer une URL comme un chemin local.
"""

import os
import re
import shutil
import tempfile
import unicodedata

from fastapi import APIRouter, HTTPException, UploadFile, File
from pydantic import BaseModel

from control_panel.backend.gcs_util import (
    manager, public_url, CHARACTERS_PREFIX, IMAGE_EXTS,
)

router = APIRouter(prefix="/api/characters", tags=["characters"])


class CharacterAsset(BaseModel):
    name: str          # nom de fichier (sans le préfixe)
    blob: str          # chemin GCS complet
    url: str           # URL publique


def _slug(text: str) -> str:
    text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode()
    text = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    return text or "character"


@router.post("/upload", response_model=CharacterAsset)
async def upload_character(file: UploadFile = File(...)) -> CharacterAsset:
    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in IMAGE_EXTS:
        raise HTTPException(400, f"extension non supportée: {ext or '(aucune)'} (attendu: {IMAGE_EXTS})")
    base = _slug(os.path.splitext(os.path.basename(file.filename or "character"))[0])
    blob = f"{CHARACTERS_PREFIX}{base}{ext}"

    tmp = tempfile.NamedTemporaryFile(suffix=ext, delete=False)
    try:
        shutil.copyfileobj(file.file, tmp)
        tmp.close()
        up = manager().upload_file(tmp.name, blob)
        if not up:
            raise HTTPException(502, "upload GCS échoué")
    finally:
        os.unlink(tmp.name)
    return CharacterAsset(name=f"{base}{ext}", blob=blob, url=up["url"])


@router.get("/library", response_model=list[CharacterAsset])
def library() -> list[CharacterAsset]:
    blobs = manager().list_blobs_with_prefix(CHARACTERS_PREFIX)
    out = []
    for blob in blobs:
        if not blob.lower().endswith(IMAGE_EXTS) or blob.endswith("/"):
            continue
        out.append(CharacterAsset(
            name=blob[len(CHARACTERS_PREFIX):], blob=blob, url=public_url(blob),
        ))
    return out

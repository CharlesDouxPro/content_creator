#!/usr/bin/env python3
"""
asset_library.py — Bibliothèque PERSISTANTE d'assets générés (backgrounds, frames d'établissement,
images/avatars). Objectif : garder les images générées POUR TOUJOURS et les RÉUTILISER d'un run à
l'autre au lieu de les régénérer -> économie de coûts (éditions FLUX/Wan, générations d'images).

Stockage LÉGER : un simple manifeste JSON (les images vivent déjà de façon permanente sur GCS ;
on n'y garde que l'URL + la description + le prompt). Clé de dédup = (kind, character, description
normalisée) -> régénérer le même background pour le même personnage retombe sur l'entrée existante.

Emplacement : $ASSET_LIBRARY_DIR (défaut : <repo>/asset_library/library.json). Thread-safe.
"""

import hashlib
import json
import os
import re
import threading
import time

_LIB_DIR = os.getenv("ASSET_LIBRARY_DIR") or os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "asset_library"))
_MANIFEST = os.path.join(_LIB_DIR, "library.json")
_LOCK = threading.RLock()


def _norm(s: str) -> str:
    """Normalise une description pour la dédup (minuscule, espaces compactés)."""
    return re.sub(r"\s+", " ", (s or "").strip().lower())


def asset_key(kind: str, character: str, description: str) -> str:
    """Identifiant stable d'un asset = hash de (kind | personnage | description normalisée).
    Public : sert aussi à nommer le fichier uploadé (URL unique par asset)."""
    raw = f"{kind}|{character or ''}|{_norm(description)}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


_key = asset_key   # alias interne rétro-compat


def _load() -> dict:
    try:
        with open(_MANIFEST, encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, dict) and isinstance(data.get("assets"), list):
                return data
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        pass
    return {"assets": []}


def _save(data: dict) -> None:
    os.makedirs(_LIB_DIR, exist_ok=True)
    tmp = _MANIFEST + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, _MANIFEST)                         # écriture atomique


def library_find(kind: str, character: str, description: str) -> dict | None:
    """Retourne l'asset existant pour (kind, character, description) — None si absent (dédup)."""
    kid = _key(kind, character, description)
    with _LOCK:
        for a in _load()["assets"]:
            if a["id"] == kid:
                return a
    return None


def library_get(asset_id: str) -> dict | None:
    """Retourne l'asset d'id `asset_id`, ou None."""
    with _LOCK:
        for a in _load()["assets"]:
            if a["id"] == asset_id:
                return a
    return None


def library_list(kind: str | None = None, character: str | None = None) -> list:
    """Liste les assets, filtrable par `kind` et/ou `character` (plus récents d'abord)."""
    with _LOCK:
        items = list(_load()["assets"])
    items = [a for a in items
             if (kind is None or a.get("kind") == kind)
             and (character is None or a.get("character") == character)]
    return sorted(items, key=lambda a: a.get("created", 0), reverse=True)


def library_add(kind: str, description: str, url: str, *, prompt: str | None = None,
                character: str | None = None) -> dict:
    """Ajoute (ou remplace, même clé) un asset dans la bibliothèque. Retourne l'entrée."""
    entry = {"id": _key(kind, character, description), "kind": kind, "character": character,
             "description": description, "prompt": prompt, "url": url, "created": time.time()}
    with _LOCK:
        data = _load()
        data["assets"] = [a for a in data["assets"] if a["id"] != entry["id"]] + [entry]
        _save(data)
    return entry

#!/usr/bin/env python3
"""
video_skills.py — Chargeur de "skills" (types de vidéo) depuis des fichiers Markdown.

Un skill = un fichier `skills/<name>.md` :
  - frontmatter YAML-lite (entre `---`) : `description`, `tools` (liste, optionnelle).
  - corps Markdown : le system_prompt (le brief de l'agent).

Ajouter un type de vidéo = déposer un `.md` dans skills/ (aucun code à toucher). L'agent
combine ce brief avec les tools listés (ou TOUS les tools si `tools` est absent) et orchestre
librement la production.
"""

import os
from dataclasses import dataclass


SKILLS_DIR = os.path.join(os.path.dirname(__file__), "skills")


@dataclass
class Skill:
    name: str
    description: str
    system_prompt: str
    # None => l'agent a accès à TOUS les tools enregistrés (créateur libre).
    tool_names: list | None = None


# ========================
# Frontmatter (YAML-lite, sans dépendance)
# ========================
def _coerce(value: str):
    """Convertit une valeur scalaire de frontmatter (bool / int / liste inline / str)."""
    v = value.strip()
    if v.lower() in ("true", "false"):
        return v.lower() == "true"
    if v.startswith("[") and v.endswith("]"):
        return [x.strip().strip("\"'") for x in v[1:-1].split(",") if x.strip()]
    if v.isdigit():
        return int(v)
    return v.strip("\"'")


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    """Sépare le frontmatter `--- ... ---` du corps. Supporte clés scalaires, listes inline
    `[a, b]` et listes en blocs (`key:` puis lignes `  - item`). Ignore les `#` commentaires."""
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end == -1:
        return {}, text
    fm, body = text[3:end], text[end + 4:].lstrip("\n")

    meta: dict = {}
    pending_key: str | None = None        # clé d'une liste en bloc en cours de lecture
    for raw in fm.splitlines():
        line = raw.rstrip()
        if not line.strip() or line.strip().startswith("#"):
            continue
        stripped = line.strip()
        if pending_key and stripped.startswith("- "):
            meta[pending_key].append(stripped[2:].strip().strip("\"'"))
            continue
        pending_key = None
        if ":" not in line:
            continue
        key, _, val = line.partition(":")
        key = key.strip()
        if val.strip() == "":                 # début d'une liste en bloc
            meta[key] = []
            pending_key = key
        else:
            meta[key] = _coerce(val)
    return meta, body.strip("\n")


# ========================
# Chargement
# ========================
_CACHE: dict[str, Skill] = {}


def _load_skill(name: str) -> Skill:
    path = os.path.join(SKILLS_DIR, f"{name}.md")
    if not os.path.exists(path):
        available = sorted(f[:-3] for f in os.listdir(SKILLS_DIR) if f.endswith(".md")) \
            if os.path.isdir(SKILLS_DIR) else []
        raise KeyError(f"skill inconnu: {name} (dispo: {available})")
    with open(path, encoding="utf-8") as f:
        meta, body = _parse_frontmatter(f.read())
    tools = meta.get("tools")
    return Skill(
        name=name,
        description=meta.get("description", ""),
        system_prompt=body,
        tool_names=list(tools) if isinstance(tools, list) else None,
    )


def get_skill(name: str) -> Skill:
    if name not in _CACHE:
        _CACHE[name] = _load_skill(name)
    return _CACHE[name]


def list_skills() -> list[str]:
    if not os.path.isdir(SKILLS_DIR):
        return []
    return sorted(f[:-3] for f in os.listdir(SKILLS_DIR) if f.endswith(".md"))

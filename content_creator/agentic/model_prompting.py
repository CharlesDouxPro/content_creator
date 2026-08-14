#!/usr/bin/env python3
"""
model_prompting.py — "Skills" de prompting SPÉCIFIQUES AU MODÈLE de génération vidéo.

Certains modèles vidéo sont livrés avec LEURS PROPRES skills (rédaction de prompts +
skills de STYLE). Ce module les charge DEPUIS DES DOSSIERS embarqués, en fonction du
modèle réellement câblé sur le rôle `video_generator` du channel — l'agent "pioche dans
le bon dossier de skills selon le modèle avec lequel il génère".

Deux niveaux :
  - le skill de prompting de BASE (grammaire du moteur, ex. h3-prompt-writing) est
    INLINÉ d'office dans le system prompt du master (toujours nécessaire) ;
  - les skills de STYLE (ex. minimalist-product-ad, 3d-animation-short…) sont listés
    dans un CATALOGUE injecté au prompt : le master CHOISIT LUI-MÊME le plus adapté au
    brief et charge son guide complet à la demande via le tool `load_h3_style`
    (chargement paresseux — les SKILL.md de style pèsent 8–32k car. chacun, on n'en
    inline qu'UN, celui choisi).

Organisation (miroir de ltx_prompting.py + skills/) :
  - les CONTENUS vivent sous `model_skills/<model>/<skill>/…` (SKILL.md + references,
    tels que publiés en amont — ici la suite de skills MiniMax-H3) ;
  - le REGISTRE `MODEL_SKILLS` associe une empreinte de `model_name` -> le dossier, le
    guide de base à inliner, et l'exposition des skills de style.

Ajouter un modèle = déposer ses skills sous `model_skills/<model>/`, puis une entrée dans
`MODEL_SKILLS`. Aucun modèle reconnu -> None (l'appelant retombe sur le guide par défaut,
cf. ltx_prompting.build_prompt_guide).
"""

import os

from content_creator.agentic.video_skills import _parse_frontmatter

MODEL_SKILLS_DIR = os.path.join(os.path.dirname(__file__), "model_skills")

# Tool que le master appelle pour charger le guide complet du style qu'il a CHOISI.
STYLE_LOADER_TOOL = "load_h3_style"


# ============================================================================
# Registre : quel modèle -> quel dossier de skills, guide de base, skills de style.
#
# `match`         : prédicat sur le `model_name` NORMALISÉ (minuscules). Match par empreinte
#                   (sous-chaînes) pour rester robuste au préfixe provider et au versioning
#                   (ex. "MiniMaxAI/MiniMax-H3", "minimax-h3-01", …).
# `dir`           : dossier racine sous model_skills/.
# `prompt_files`  : fichiers (relatifs à `dir`) du guide de BASE, concaténés et inlinés dans
#                   le system prompt. Auto-suffisant (le master ne lit pas de fichiers à la
#                   volée) : SKILL.md donne le workflow ; base-en.txt la structure/les règles.
# `styles`        : True => exposer les sous-dossiers (hors `style_exclude`) comme skills de
#                   STYLE sélectionnables par le master (catalogue + tool load_h3_style).
# `style_exclude` : sous-dossiers qui NE SONT PAS des styles (ici le guide de base).
# ============================================================================
MODEL_SKILLS = {
    "minimax_h3": {
        "match": lambda name: "minimax" in name and "h3" in name,
        "dir": "minimax_h3",
        "prompt_files": [
            "h3-prompt-writing/SKILL.md",
            "h3-prompt-writing/references/base-en.txt",
        ],
        "styles": True,
        "style_exclude": {"h3-prompt-writing"},
    },
}


def _model_name(video_model) -> str:
    """Extrait le `model_name` d'un ModelConfig (dict) OU d'une chaîne. Normalisé minuscules."""
    if not video_model:
        return ""
    name = video_model.get("model_name") if isinstance(video_model, dict) else video_model
    return (name or "").strip().lower()


def resolve_model_skill(video_model) -> str | None:
    """Clé du registre `MODEL_SKILLS` correspondant au modèle `video_generator`, ou None."""
    name = _model_name(video_model)
    if not name:
        return None
    for key, spec in MODEL_SKILLS.items():
        if spec["match"](name):
            return key
    return None


# ============================================================================
# Frontmatter — lecture robuste de `name`/`description` (gère les scalaires bloc `|`/`>`
# utilisés par les SKILL.md de style, que le parseur YAML-lite de video_skills ne couvre pas).
# ============================================================================
def _skill_meta(path: str) -> dict:
    """{name, description, …} depuis le frontmatter d'un SKILL.md. Supporte les scalaires
    bloc (`key: |` puis lignes indentées). Ne lit que les clés de PREMIER niveau."""
    with open(path, encoding="utf-8") as f:
        text = f.read()
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    fm = text[3:end] if end != -1 else text[3:]
    lines = fm.splitlines()
    meta: dict = {}
    i = 0
    while i < len(lines):
        line = lines[i]
        i += 1
        if not line.strip() or line.strip().startswith("#") or ":" not in line:
            continue
        if line[:1] in (" ", "\t"):        # clé imbriquée (ex. metadata:) -> ignorée
            continue
        key, _, val = line.partition(":")
        key, val = key.strip(), val.strip()
        if val in ("|", ">", "|-", ">-", "|+", ">+"):   # scalaire bloc -> lignes indentées
            block = []
            while i < len(lines) and (not lines[i].strip() or lines[i][:1] in (" ", "\t")):
                block.append(lines[i].strip())
                i += 1
            meta[key] = " ".join(x for x in block if x).strip()
        else:
            meta[key] = val.strip("\"'")
    return meta


def _style_dirs(spec: dict) -> list[str]:
    """Sous-dossiers de style (contenant un SKILL.md), triés, hors `style_exclude`."""
    base = os.path.join(MODEL_SKILLS_DIR, spec["dir"])
    if not spec.get("styles") or not os.path.isdir(base):
        return []
    exclude = spec.get("style_exclude") or set()
    out = []
    for name in sorted(os.listdir(base)):
        if name in exclude:
            continue
        if os.path.isfile(os.path.join(base, name, "SKILL.md")):
            out.append(name)
    return out


def list_style_skills(video_model) -> list[dict]:
    """Catalogue des skills de STYLE du modèle : [{name, description}]. [] si aucun."""
    key = resolve_model_skill(video_model)
    if key is None:
        return []
    spec = MODEL_SKILLS[key]
    base = os.path.join(MODEL_SKILLS_DIR, spec["dir"])
    catalog = []
    for name in _style_dirs(spec):
        meta = _skill_meta(os.path.join(base, name, "SKILL.md"))
        catalog.append({"name": name, "description": meta.get("description", "").strip()})
    return catalog


def load_style_skill(video_model, skill_name: str) -> str:
    """Corps du SKILL.md du style CHOISI par le master (frontmatter strippé). Lève KeyError
    si le style est inconnu pour ce modèle (le tool renvoie alors une erreur exploitable)."""
    key = resolve_model_skill(video_model)
    spec = MODEL_SKILLS.get(key) if key else None
    if not spec:
        raise KeyError(f"aucun skill de style pour ce modèle ({_model_name(video_model)!r})")
    available = _style_dirs(spec)
    if skill_name not in available:
        raise KeyError(f"style inconnu: {skill_name!r} (dispo: {available})")
    path = os.path.join(MODEL_SKILLS_DIR, spec["dir"], skill_name, "SKILL.md")
    with open(path, encoding="utf-8") as f:
        _, body = _parse_frontmatter(f.read())
    return body


def _read_prompt_file(base_dir: str, rel: str) -> str:
    """Lit un fichier de skill ; strip le frontmatter YAML des `.md` (garde le corps utile)."""
    path = os.path.join(base_dir, rel)
    with open(path, encoding="utf-8") as f:
        text = f.read()
    if rel.endswith(".md"):
        _, body = _parse_frontmatter(text)
        return body
    return text.strip("\n")


def _style_catalog_block(video_model) -> str | None:
    """Bloc CATALOGUE des styles + consigne de choix (le master pioche LUI-MÊME). None si aucun."""
    catalog = list_style_skills(video_model)
    if not catalog:
        return None
    lines = [
        "# H3 STYLE SKILLS — pick the ONE that best fits the brief (you decide)",
        "This model ships style-specific skills. If the brief/mood clearly matches one, CHOOSE it "
        f"and call the `{STYLE_LOADER_TOOL}` tool with its `name` to load its full guide BEFORE "
        "planning shots; then follow it when you write the video prompts. Pick at most ONE. If none "
        "fits, skip this and rely on the base H3 prompting skill above.",
        "NOTE: these skills were written for MiniMax's own Hub agent — use each as STYLE & PROMPTING "
        "guidance (visual language, camera, structure, pacing). Keep orchestrating with YOUR tools "
        "(add_talking_clip / add_broll_clip / add_media_clip / assemble_video); IGNORE any mention of "
        "canvas, choice cards or hub_* tools you do not have.",
        "",
        "Available styles:",
    ]
    for c in catalog:
        desc = " ".join(c["description"].split())
        if len(desc) > 320:
            desc = desc[:317].rstrip() + "…"
        lines.append(f"- `{c['name']}`: {desc}")
    return "\n".join(lines)


def load_engine_prompt_guide(video_model) -> str | None:
    """Guide de prompting AUTO-SUFFISANT du moteur `video_generator`, prêt à injecter dans le
    system prompt — ou None si le modèle n'a pas de skill dédié (l'appelant garde le défaut).

    Inclut : le guide de BASE (inliné) + le CATALOGUE des styles (le master en choisit un et
    le charge à la demande via `load_h3_style`).
    `video_model` = ModelConfig du rôle video_generator (dict {model_name, provider, …})
    ou directement un nom de modèle."""
    key = resolve_model_skill(video_model)
    if key is None:
        return None
    spec = MODEL_SKILLS[key]
    base = os.path.join(MODEL_SKILLS_DIR, spec["dir"])
    blocks = []
    for rel in spec["prompt_files"]:
        try:
            blocks.append(_read_prompt_file(base, rel))
        except OSError as e:
            print(f"[model_prompting] skill '{key}' : {rel} illisible ({e}) — ignoré.", flush=True)
    if not blocks:
        return None
    header = (
        f"# SKILL — Video engine prompting ({key})\n"
        f"The active video-generation model ships its OWN prompt-writing skill. Follow the "
        f"structure, field names, section order and timing notation below EXACTLY when you write "
        f"the video prompts (e.g. `shot_description`) sent to the engine. The reference material "
        f"is inlined below (do not try to read files)."
    )
    parts = [header, *blocks]
    catalog = _style_catalog_block(video_model)
    if catalog:
        parts.append(catalog)
    return "\n\n".join(parts)

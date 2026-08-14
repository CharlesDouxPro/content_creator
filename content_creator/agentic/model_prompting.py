#!/usr/bin/env python3
"""
model_prompting.py — Skills de PROMPTING (+ STYLE) par MODÈLE de génération vidéo.

Chaque modèle a son dossier `model_skills/<model>/` avec des skills rangés par FINALITÉ :

  - `prompting/` : comment écrire de BONS prompts pour CE moteur (grammaire, champs, notation).
      Toujours inliné dans le system prompt du master. Le modèle sans skill dédié retombe sur
      le prompting par DÉFAUT (`ltx`, grammaire cinématographique générique — valable aussi Wan).
  - `styles/`    : skills de STYLE optionnels (un sous-dossier = un style). Listés en CATALOGUE ;
      le master CHOISIT LUI-MÊME le plus adapté et charge son guide complet À LA DEMANDE via le
      tool `load_style_skill` (chargement paresseux : les SKILL.md de style pèsent 8–32k car.).

Le FORMAT/rendu (résolution, fps, régime i2v, lip-sync…) N'est PAS ici : il dépend du BACKEND et
de `.env`, pas du modèle — il reste géré en code (cf. ltx_prompting.build_prompt_guide).

Ajouter un modèle = déposer `model_skills/<model>/prompting/…` (+ `styles/…` au besoin) et une
entrée dans `MODEL_SKILLS`. Aucun autre code à toucher.
"""

import os

from content_creator.agentic.video_skills import _parse_frontmatter

MODEL_SKILLS_DIR = os.path.join(os.path.dirname(__file__), "model_skills")

# Tool que le master appelle pour charger le guide complet du style qu'il a CHOISI.
STYLE_LOADER_TOOL = "load_style_skill"

# Skill de prompting utilisé quand le modèle `video_generator` n'en a pas de dédié (Wan, etc.).
DEFAULT_MODEL_KEY = "ltx"

# Modèles servis par l'endpoint vidéo ASYNCHRONE de SGLang (POST /v1/videos), audiovisuels
# (cf. sglang_video_client). Clés du registre MODEL_SKILLS. Aiguille le BACKEND d'inférence
# de generate_broll (distinct du prompting). NB : `ltx` générique (2.3) n'en est PAS (rendu via
# ltx_client local / Wan) ; seul LTX-2.5 passe par SGLang.
SGLANG_VIDEO_KEYS = {"minimax_h3", "ltx_2_5"}


def uses_sglang_video(video_model) -> bool:
    """True si le modèle `video_generator` est servi via l'endpoint vidéo async SGLang."""
    return resolve_model_skill(video_model) in SGLANG_VIDEO_KEYS


# ============================================================================
# Registre : quel modèle -> quel dossier + prompting à inliner + styles exposés.
#
# `match`           : prédicat sur le `model_name` NORMALISÉ (minuscules), par EMPREINTE
#                     (sous-chaînes) — robuste au préfixe provider et au versioning
#                     (ex. "MiniMaxAI/MiniMax-H3", "Lightricks/LTX-Video-2.3", "ltx-2").
# `dir`             : dossier racine sous model_skills/.
# `prompting_files` : fichiers (relatifs à `dir`) du guide de prompting, concaténés et inlinés.
#                     Auto-suffisant (le master ne lit pas de fichiers à la volée) : on inline
#                     directement le contenu de référence.
# `styles_dir`      : sous-dossier des styles (chaque sous-dossier avec un SKILL.md = un style),
#                     ou None si le modèle n'a pas de styles.
# `intro`           : (optionnel) préambule inliné avant le prompting (ex. « suis la structure
#                     exactement, tout est inliné »).
# `styles_note`     : (optionnel) mise en garde ajoutée au catalogue de styles.
# ============================================================================
MODEL_SKILLS = {
    # LTX-2.5 AVANT le générique `ltx` : le match le plus spécifique doit gagner (resolve_model_skill
    # renvoie le PREMIER match dans l'ordre d'insertion). Sans ça, "LTX-Video-2.5" tomberait sur `ltx`.
    "ltx_2_5": {
        "match": lambda name: "ltx" in name and ("2.5" in name or "2-5" in name),
        "dir": "ltx_2_5",
        "prompting_files": ["prompting/SKILL.md"],
        "styles_dir": None,
        "intro": (
            "# SKILL — Video engine prompting (LTX-2.5)\n"
            "Apply the guide below when you write each shot's video prompt (e.g. `shot_description`). "
            "IMPORTANT for THIS harness: you do NOT output a standalone prompt to the user and you do "
            "NOT paste anything into LTX yourself — you KEEP orchestrating with YOUR tools "
            "(add_talking_clip / add_broll_clip / add_media_clip / assemble_video), writing ONE "
            "Single-Shot prompt per shot. Ignore the skill's 'respond with the finished prompt' / "
            "'ask the user' framing. Write the video prompt in ENGLISH; the narration text "
            "(`text` / `narration_text`) stays in the video's language and is NOT translated."
        ),
    },
    "ltx": {
        "match": lambda name: "ltx" in name,
        "dir": "ltx",
        "prompting_files": ["prompting/SKILL.md"],
        "styles_dir": None,
    },
    "minimax_h3": {
        "match": lambda name: "minimax" in name and "h3" in name,
        "dir": "minimax_h3",
        "prompting_files": [
            "prompting/h3-prompt-writing/SKILL.md",
            "prompting/h3-prompt-writing/references/base-en.txt",
        ],
        "styles_dir": "styles",
        "intro": (
            "# SKILL — Video engine prompting (MiniMax H3)\n"
            "The active model ships its OWN prompt-writing skill. Follow the structure, field names, "
            "section order and timing notation below EXACTLY when you write the video prompts (e.g. "
            "`shot_description`). The reference material is inlined below (do not try to read files)."
        ),
        "styles_note": (
            "These skills were written for MiniMax's own Hub agent — use each as STYLE & PROMPTING "
            "guidance (visual language, camera, structure, pacing). Keep orchestrating with YOUR tools "
            "(add_talking_clip / add_broll_clip / add_media_clip / assemble_video); IGNORE any mention "
            "of canvas, choice cards or hub_* tools you do not have."
        ),
    },
}


def _model_name(video_model) -> str:
    """Extrait le `model_name` d'un ModelConfig (dict) OU d'une chaîne. Normalisé minuscules."""
    if not video_model:
        return ""
    name = video_model.get("model_name") if isinstance(video_model, dict) else video_model
    return (name or "").strip().lower()


def resolve_model_skill(video_model) -> str | None:
    """Clé du registre correspondant au modèle `video_generator`, ou None si aucun match."""
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
    """Sous-dossiers de style (contenant un SKILL.md) sous `styles_dir`, triés. [] si aucun."""
    styles_dir = spec.get("styles_dir")
    if not styles_dir:
        return []
    base = os.path.join(MODEL_SKILLS_DIR, spec["dir"], styles_dir)
    if not os.path.isdir(base):
        return []
    return [n for n in sorted(os.listdir(base))
            if os.path.isfile(os.path.join(base, n, "SKILL.md"))]


def list_style_skills(video_model) -> list[dict]:
    """Catalogue des skills de STYLE du modèle : [{name, description}]. [] si aucun."""
    key = resolve_model_skill(video_model)
    if key is None:
        return []
    spec = MODEL_SKILLS[key]
    base = os.path.join(MODEL_SKILLS_DIR, spec["dir"], spec.get("styles_dir") or "")
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
    path = os.path.join(MODEL_SKILLS_DIR, spec["dir"], spec["styles_dir"], skill_name, "SKILL.md")
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
    key = resolve_model_skill(video_model)
    spec = MODEL_SKILLS[key]
    lines = [
        f"# STYLE SKILLS ({key}) — pick the ONE that best fits the brief (you decide)",
        "This model ships style-specific skills. If the brief/mood clearly matches one, CHOOSE it "
        f"and call the `{STYLE_LOADER_TOOL}` tool with its `name` to load its full guide BEFORE "
        "planning shots; then follow it when you write the video prompts. Pick at most ONE. If none "
        "fits, skip this and rely on the base prompting skill above.",
    ]
    if spec.get("styles_note"):
        lines.append("NOTE: " + spec["styles_note"])
    lines += ["", "Available styles:"]
    for c in catalog:
        desc = " ".join(c["description"].split())
        if len(desc) > 320:
            desc = desc[:317].rstrip() + "…"
        lines.append(f"- `{c['name']}`: {desc}")
    return "\n".join(lines)


def load_prompting_guide(video_model) -> str:
    """Guide de PROMPTING auto-suffisant du moteur `video_generator`, prêt à injecter dans le
    system prompt. Retombe sur le prompting par DÉFAUT (`ltx`) si le modèle n'a pas de skill dédié
    — retourne donc TOUJOURS un guide. Inclut le CATALOGUE de styles si le modèle en a.

    `video_model` = ModelConfig du rôle video_generator (dict {model_name, provider, …}) ou un nom."""
    key = resolve_model_skill(video_model) or DEFAULT_MODEL_KEY
    spec = MODEL_SKILLS[key]
    base = os.path.join(MODEL_SKILLS_DIR, spec["dir"])
    blocks = []
    if spec.get("intro"):
        blocks.append(spec["intro"])
    for rel in spec["prompting_files"]:
        try:
            blocks.append(_read_prompt_file(base, rel))
        except OSError as e:
            print(f"[model_prompting] skill '{key}' : {rel} illisible ({e}) — ignoré.", flush=True)
    catalog = _style_catalog_block(video_model)   # None si le modèle n'a pas de styles
    if catalog:
        blocks.append(catalog)
    return "\n\n".join(blocks)

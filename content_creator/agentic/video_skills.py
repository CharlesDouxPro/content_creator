#!/usr/bin/env python3
"""
video_skills.py — Registre de "skills" (types de vidéo).

Un skill = un type de vidéo : un brief (system_prompt) + le sous-ensemble de tools
qu'il expose à l'agent. Ajouter un type de vidéo = enregistrer un Skill ici (en
réutilisant les tools partagés de video_tools.py + d'éventuels tools dédiés).
"""

from dataclasses import dataclass, field


@dataclass
class Skill:
    name: str
    description: str
    system_prompt: str
    tool_names: list = field(default_factory=list)


SKILLS = {}


def register(skill: Skill):
    SKILLS[skill.name] = skill
    return skill


def get_skill(name: str) -> Skill:
    if name not in SKILLS:
        raise KeyError(f"skill inconnu: {name} (dispo: {list(SKILLS)})")
    return SKILLS[name]


# ========================
# Skill: avatar_story
# ========================
_AVATAR_STORY_PROMPT = """Tu es un réalisateur de vidéos verticales courtes (TikTok/Instagram) avec un avatar.

Déroulé :
1) ÉCRIS LE SCRIPT — appelle `write_script` en rédigeant TOI-MÊME le `style` (ton, angle, rythme, intention) D'APRÈS LE MOOD. Le script est généré à partir de l'article et te revient. (Si `write_script` indique qu'il n'y a pas d'article, le message contient déjà le script : passe à l'étape 3.)
2) DÉCOR — appelle `set_scene_background` en INFÉRANT un décor cohérent avec le sujet et le mood (ex. football → "stade au coucher du soleil" ; tech → "studio moderne épuré, néons doux"). Décris UNIQUEMENT le décor ; l'identité de l'avatar est préservée automatiquement.
3) DÉCOUPE le script en segments et choisis, pour chaque segment, le plan :
   - `add_talking_clip` : l'avatar parle FACE CAMÉRA (lip-sync) — ACCROCHE, phrases CLÉS, CONCLUSION.
   - `add_broll_clip` : plan B-ROLL cinématographique + voix off — phrases DESCRIPTIVES / d'ambiance. `shot_description` visuel et riche (cadrage, lumière, action), en anglais.
4) `assemble_video` une fois TOUS les plans planifiés (les plans sont instantanés ; le rendu réel est parallèle à l'assemblage).
5) Ensuite seulement, et si pertinent : `add_background_music` puis `add_subtitles` (sur la vidéo finale).

Règles :
- Le MOOD prime sur TOUS tes choix : écriture du script, décor, équilibre talking/b-roll, cadrages, rythme. Sans mood → réalisation classique.
- Alterne pour garder du rythme ; ne mets pas tout en talking ni tout en b-roll.
- Couvre tout le script, dans l'ordre. Quand la vidéo finale est prête, arrête-toi (plus de tool call)."""

register(Skill(
    name="avatar_story",
    description="Vidéo créateur de contenu avec un avatar (A-roll lip-sync + B-roll cinématographique).",
    system_prompt=_AVATAR_STORY_PROMPT,
    tool_names=[
        "write_script",
        "set_scene_background",
        "add_talking_clip",
        "add_broll_clip",
        "assemble_video",
        "add_background_music",
        "add_subtitles",
    ],
))

#!/usr/bin/env python3
"""
prompt_enhancer.py — Améliore le BRIEF d'un channel via le `master_mind`, calibré sur le moteur
vidéo cible (LTX-2.5 / MiniMax H3 / …).

Le `context.prompt` d'un channel est une INTENTION (la vidéo voulue), PAS un prompt vidéo brut :
l'agent orchestrateur (cf. video_agent.run_agent) le découpe ensuite en plans. `enhance_prompt`
réécrit ce brief pour qu'il soit plus fort et cinématographiquement précis, en exploitant les
forces du moteur câblé sur `video_generator` — on lui injecte pour cela le MÊME guide de prompting
moteur que celui vu par le master (build_prompt_guide). Il renvoie un brief AMÉLIORÉ (toujours une
intention, jamais un script plan-par-plan), dans la langue d'origine.

Le LLM utilisé est celui du rôle `master_mind` du channel (même client OpenAI-compatible que
l'agent), pour rester cohérent avec le cerveau qui produira réellement la vidéo.
"""

from openai import OpenAI

from content_creator.agentic.ltx_prompting import build_prompt_guide
from content_creator.agentic.model_prompting import DEFAULT_MODEL_KEY, resolve_model_skill
from content_creator.agentic.video_skills import get_skill

# Repli si le channel ne fixe pas de model_name sur master_mind (aligné sur video_agent.AGENT_MODEL).
ENHANCER_MODEL = "anthropic/claude-opus-4-8"


ENHANCER_SYSTEM = """\
You are a senior creative director and prompt engineer for AI-generated short-form VERTICAL videos
(TikTok / Reels / Shorts). Your job: take a channel BRIEF and REWRITE it into a stronger, more
effective brief for a video produced by an autonomous agent on the "{engine}" engine (prompting
profile: {engine_key}).

The BRIEF is an INTENTION — the video the creator wants — NOT a shot-by-shot script. Keep it that
way: a downstream orchestrator agent turns it into concrete shots. So you IMPROVE the brief; you do
NOT write per-shot video prompts, timings, or a storyboard.

Make the brief:
- SHARPER intent: a strong opening hook, a clear narrative arc, a payoff / call-to-react;
- RICHER direction: tone, energy, pacing, talking-head vs b-roll balance, visual identity, light/palette;
- ENGINE-AWARE: lean into what the "{engine}" engine renders well and steer away from its weak spots
  (use the engine prompting skill provided below purely as REFERENCE about the engine's strengths and
  conventions — do not copy its wording, and ignore any instruction there addressed to a tool-calling
  agent or telling you to output a finished prompt / ask the user);
- CONCRETE but CONCISE: one or two tight paragraphs, no bullet-by-bullet storyboard.

Hard rules:
- Keep the SAME LANGUAGE as the input brief.
- Preserve the creator's core idea, subject, mood, and any named characters/resources — enrich, don't hijack.
- Output ONLY the improved brief text. No preamble, no explanation, no markdown headings, no surrounding quotes.
"""


def _render_characters(characters: dict) -> str:
    """Inventaire léger des personnages (nom + description) pour rappeler à l'enhancer de les garder."""
    if not characters:
        return ""
    lines = ["## CHARACTERS (part of the concept — keep them)"]
    for name, c in characters.items():
        desc = (c or {}).get("description")
        lines.append(f"- {name}" + (f": {desc}" if desc else ""))
    return "\n".join(lines)


def enhance_prompt(prompt: str, models_config: dict, skill_name: str | None = None,
                   mood: str | None = None, characters: dict | None = None) -> str:
    """Réécrit `prompt` (le brief du channel) en un brief AMÉLIORÉ, calibré sur le moteur vidéo cible.

    `models_config` = PoolModelConfig RÉSOLU (tokens injectés, cf. schema.resolve_pool) : `master_mind`
    pilote le LLM d'amélioration, `video_generator` aiguille le guide de prompting moteur injecté.
    `skill_name` = skill du channel (borne les tools -> sections conditionnelles du guide) ; `mood` et
    `characters` = contexte du brief, rappelés à l'enhancer pour qu'il les préserve.
    Retourne le brief amélioré (str non vide). Lève ValueError si le prompt est vide."""
    prompt = (prompt or "").strip()
    if not prompt:
        raise ValueError("prompt vide — rien à améliorer")

    master = models_config["master_mind"]
    video_model = models_config.get("video_generator")
    client = OpenAI(api_key=master["provider"]["token"], base_url=master["provider"]["base_url"])
    model = master.get("model_name") or ENHANCER_MODEL

    # Tools du skill -> borne les sections conditionnelles du guide (lip-sync, etc.). Skill inconnu
    # ou absent -> None (le guide considère alors tous les tools enregistrés).
    tool_names = None
    if skill_name:
        try:
            tool_names = get_skill(skill_name).tool_names
        except (KeyError, OSError):
            tool_names = None

    # MÊME guide de prompting moteur que celui injecté dans le system prompt du master.
    engine_guide = build_prompt_guide(tool_names, video_model=video_model)
    engine_name = (video_model or {}).get("model_name") or "—"
    engine_key = resolve_model_skill(video_model) or f"{DEFAULT_MODEL_KEY} (default)"

    system = (
        ENHANCER_SYSTEM.format(engine=engine_name, engine_key=engine_key)
        + "\n\n# TARGET ENGINE PROMPTING SKILL (reference only)\n"
        + engine_guide
    )

    parts = [f"## CURRENT BRIEF\n{prompt}"]
    if mood:
        parts.append(f"## MOOD\n{mood}")
    characters_block = _render_characters(characters or {})
    if characters_block:
        parts.append(characters_block)
    user_content = "\n\n".join(parts)

    resp = client.chat.completions.create(
        model=model,
        messages=[{"role": "system", "content": system},
                  {"role": "user", "content": user_content}],
        max_tokens=1500,
    )
    enhanced = (resp.choices[0].message.content or "").strip()
    if not enhanced:
        raise RuntimeError("le modèle n'a renvoyé aucun contenu")
    return enhanced

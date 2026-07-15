#!/usr/bin/env python3
"""
video_agent.py — Agent orchestrateur de vidéos (bibliothèque : `run_agent`).

L'agent (LLM `master_mind` via DeepInfra, endpoint OpenAI-compatible) reçoit un BRIEF + un
*skill* (type de vidéo) + des personnages/ressources, et décide lui-même quels *tools* appeler
pour construire la vidéo, plan par plan. Toutes les décisions sont tracées (cf. trace.py).

Tout est piloté par le CHANNEL CONFIG (content_creator/config/channels.py) — aucun paramètre en
ligne de commande. Lancement via la pipeline : `python -m content_creator.pipelines.pipeline_agentic`.
"""

import os
import json
import time

from openai import OpenAI

from content_creator.config.channels import default_models_config
from content_creator.pipelines.modules import GCSManager, ArticleSummarizer
from content_creator.agentic.capabilities import Ctx, download, upload_public
from content_creator.agentic.video_tools import (
    VideoSession, openai_tool_schemas, dispatch, cleanup_fetched_images,
)
from content_creator.agentic.video_skills import get_skill
from content_creator.agentic.ltx_prompting import build_prompt_guide
from content_creator.agentic.trace import Tracer

# Cerveau de l'agent : modèle par défaut (rôle `master_mind`) si le channel n'en fournit pas.
AGENT_MODEL = "anthropic/claude-opus-4-8"


def _resolve_characters(gcs: GCSManager, characters: dict, output_dir: str) -> dict:
    """Résout les personnages -> {name: {voice, style, voice_model, language, description,
    portrait_url, local_image}}. Pour un personnage avec image :
    - `portrait_url` : URL publique (upload GCS si chemin local, sinon l'URL telle quelle) — input i2v/Pruna.
    - `local_image`  : copie locale (téléchargée si URL) — requise par set_scene_background (FLUX)."""
    resolved = {}
    for name, c in (characters or {}).items():
        img = c.get("image")
        portrait_url = local_image = None
        if img:
            if str(img).startswith("http"):
                portrait_url = img
                local_image = os.path.join(output_dir, f"char_{name}_src")
                download(img, local_image)
            else:
                local_image = img
                portrait_url = upload_public(gcs, img, f"media/test/char_{name}.png")
        resolved[name] = {"voice": c.get("voice"), "style": c.get("style"),
                          "voice_model": c.get("voice_model"), "language": c.get("language"),
                          "description": c.get("description"),
                          "portrait_url": portrait_url, "local_image": local_image}
    return resolved


def build_session(output_dir: str, models: dict, ressources: dict = None,
                  characters: dict = None) -> VideoSession:
    """Prépare les ressources partagées d'un run. Aucune notion d'avatar global : l'identité
    visuelle/vocale vit dans les PERSONNAGES (résolus ici). C'est le SKILL qui décide comment les
    utiliser (ex. avatar_story : un personnage sert d'avatar face caméra).
    `models` = PoolModelConfig : `slm` -> script/titre ; `lip_sync`/`video_generator`/`voice_generator`
    propagés via la session. `ressources` = context.ressources exposés aux tools."""
    gcs = GCSManager()
    ctx = Ctx(gcs=gcs, summarizer=ArticleSummarizer(models["slm"]))
    return VideoSession(ctx=ctx, output_dir=output_dir,
                        models=models, ressources=ressources or {},
                        voice=models.get("voice_generator"),
                        characters=_resolve_characters(gcs, characters, output_dir))


def _render_ressources(ressources: dict) -> str:
    """Inventaire lisible des ressources mises à disposition de l'agent (pour le message user).
    L'agent référence ces chemins/urls tels quels dans `add_media_clip` / `add_background_music`."""
    if not ressources:
        return ""
    lines = []
    labels = {
        "urls": "URLs (pages to scrape / remote media)",
        "local_paths": "Local files (video clips / images to edit)",
        "audio_paths": "Audio tracks (music / voice-over)",
    }
    for key, label in labels.items():
        items = ressources.get(key) or []
        if items:
            lines.append(f"- {label}:")
            lines += [f"    - {it}" for it in items]
    notes = ressources.get("notes")
    if notes:
        lines.append(f"- Notes: {notes}")
    return "## AVAILABLE RESOURCES\n" + "\n".join(lines) if lines else ""


def _render_characters(characters: dict) -> str:
    """Inventaire des personnages pour l'agent : nom (à passer en `character`), description,
    et s'ils ont un avatar (lip-sync possible) ou non (b-roll / voix off seulement)."""
    if not characters:
        return ""
    lines = ["## CHARACTERS (pass the exact NAME via the tools' `character` parameter)"]
    for name, c in characters.items():
        bits = [c["description"]] if c.get("description") else []
        bits.append("avatar available (lip-sync OK)" if c.get("image")
                    else "no avatar (b-roll / voice-over)")
        lines.append(f"- {name}: {'; '.join(bits)}")
    return "\n".join(lines)


def run_agent(content: str = None, skill_name: str = "avatar_story",
              mood: str = None, article=None,
              max_steps: int = 20, label: str = None, models_config: dict = None,
              context: dict = None) -> dict:
    """Boucle tool-calling : l'agent construit la vidéo, chaque décision est tracée.
    `content` = contenu source (article à adapter OU script déjà prêt) ; optionnel.
    `context` = brief du channel {prompt, ressources, mood, characters} : `prompt` est l'INTENTION
    (la vidéo voulue), `ressources` la matière première exposée aux tools, `mood` le ton,
    `characters` l'identité visuelle/vocale (le skill décide comment les utiliser).
    `article`  = FullArticle source (permet au master d'écrire le script via write_script).
    `models_config` = PoolModelConfig du channel (rôles -> ModelConfig) ; à défaut, la
    config par défaut. `master_mind` pilote ce LLM orchestrateur, les autres rôles sont
    propagés via la session.
    Retourne {"video": chemin, "script": script écrit par le master}."""
    models = models_config or default_models_config
    context = context or {}
    prompt = context.get("prompt")
    ressources = context.get("ressources") or {}
    characters = context.get("characters") or {}
    mood = mood or context.get("mood")            # mood du channel (context)

    tracer = Tracer(label=label)
    tracer.start(content or prompt or "", skill_name, "")

    skill = get_skill(skill_name)
    session = build_session(tracer.dir, models, ressources, characters=characters)
    session.article = article
    session.name = label                          # namespace de dédup pour le tool scrape_article

    master_mind = models["master_mind"]
    client = OpenAI(api_key=master_mind["provider"]["token"],
                    base_url=master_mind["provider"]["base_url"])
    agent_model = master_mind.get("model_name") or AGENT_MODEL
    tools = openai_tool_schemas(skill.tool_names)
    # skill (réalisation) + compétence de prompting du moteur vidéo (LTX), ADAPTÉE
    # au backend actif (i2v vs t2v, résolution/fps réellement rendus).
    system = skill.system_prompt + "\n\n" + build_prompt_guide(skill.tool_names)
    if mood:   # le mood pilote les CHOIX DE RÉALISATION du master (sinon: réalisation classique)
        system += (
            f"\n\n## MOOD (high priority)\n"
            f"The mood/intent of this video is: \"{mood}\".\n"
            f"Adapt ALL your directing choices to this mood:\n"
            f"- the background (`set_scene_background`),\n"
            f"- the talking / b-roll balance and the pacing,\n"
            f"- the `shot_description` (framing, light, movement, energy),\n"
            f"- the overall tone of the staging.\n"
            f"The mood prevails over the default choices."
        )
    # Message utilisateur = BRIEF (l'intention) + CONTENU source (si fourni) + inventaire RESSOURCES.
    user_parts = []
    if prompt:
        user_parts.append(f"## BRIEF\n{prompt}")
    if content:
        user_parts.append(f"## SOURCE CONTENT\n{content}")
    ressources_block = _render_ressources(ressources)
    if ressources_block:
        user_parts.append(ressources_block)
    characters_block = _render_characters(characters)
    if characters_block:
        user_parts.append(characters_block)
    user_content = "\n\n".join(user_parts) or content or prompt or "Create the requested video."

    messages = [{"role": "system", "content": system},
                {"role": "user", "content": user_content}]

    try:
        for _ in range(max_steps):
            resp = client.chat.completions.create(
                model=agent_model, messages=messages, tools=tools,
                tool_choice="auto", max_tokens=4096,
            )
            msg = resp.choices[0].message
            tracer.on_assistant(msg.content, getattr(resp, "usage", None))

            tool_calls = msg.tool_calls or []
            assistant_msg = {"role": "assistant", "content": msg.content or ""}
            if tool_calls:
                assistant_msg["tool_calls"] = [
                    {"id": tc.id, "type": "function",
                     "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
                    for tc in tool_calls
                ]
            messages.append(assistant_msg)

            if not tool_calls:
                break   # l'agent a fini

            for tc in tool_calls:
                name = tc.function.name
                try:
                    args = json.loads(tc.function.arguments or "{}")
                except json.JSONDecodeError:
                    args = {}
                t0 = tracer.on_tool_call(name, args)
                result = dispatch(session, name, args)
                ok = result.get("status") == "ok"
                summary = (result.get("error") if not ok else
                           result.get("clip") or result.get("final_video") or result.get("note") or "ok")
                tracer.on_tool_result(name, ok, str(summary), (time.time() - t0) * 1000)
                messages.append({"role": "tool", "tool_call_id": tc.id,
                                 "content": json.dumps(result, ensure_ascii=False)})

        # FILET DE SÉCURITÉ : l'agent s'est arrêté (ou a épuisé max_steps) avec des plans
        # planifiés mais SANS produire de vidéo finale (oubli d'assemble_video, ou détournement
        # de retry_plan). On assemble automatiquement pour ne pas perdre le travail.
        if session.final_video is None and session.plan:
            print("⚠️ assemble_video non appelé par l'agent — assemblage automatique de secours.", flush=True)
            t0 = tracer.on_tool_call("assemble_video", {"auto": True})
            result = dispatch(session, "assemble_video", {})
            ok = result.get("status") == "ok"
            tracer.on_tool_result("assemble_video", ok,
                                  str(result.get("final_video") or result.get("error")),
                                  (time.time() - t0) * 1000)
    finally:
        # Fin de vidéo : supprime les images web téléchargées (search_web_image).
        n = cleanup_fetched_images(session)
        if n:
            print(f"🧹 {n} image(s) web téléchargée(s) supprimée(s).", flush=True)

    tracer.finish(session.final_video)
    article_title = session.article.link.title if session.article else None
    return {"video": session.final_video, "script": session.script, "article": article_title}

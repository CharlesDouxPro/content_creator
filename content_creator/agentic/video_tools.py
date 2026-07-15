#!/usr/bin/env python3
"""
video_tools.py — Registre de tools pour l'agent vidéo (mode "plan-then-render").

Chaque tool est une capacité atomique enregistrée via @tool(schema). Tous opèrent
sur une VideoSession partagée. Les fonctions métier sont réutilisées telles quelles
depuis avatar_story_hybrid.py.

Modèle d'exécution :
  - add_talking_clip / add_broll_clip => INSTANTANÉS : ils PLANIFIENT un plan (spec)
    dans la timeline et capturent le décor courant. Aucun appel coûteux.
  - assemble_video => REND tous les plans planifiés EN PARALLÈLE (ThreadPoolExecutor),
    dans l'ordre, puis concatène.

Ajouter une capacité = écrire une fonction décorée @tool(...).
"""

import os
import time
from dataclasses import dataclass, field
from concurrent.futures import ThreadPoolExecutor

from content_creator.agentic.capabilities import (
    Ctx,
    OUTPUT_DIR, SEED_BASE, PRUNA_MOVEMENT, BACKGROUND_TEMPLATE,
    sh, download, upload_public, ffprobe_duration,
    synthesize_audio, generate_lipsync, generate_broll,
    reframe_vertical, concat_clips, prepare_scene_portrait,
    fetch_web_image, is_image_path, image_to_clip,
)
from content_creator.config.config import VIDEO_BACKEND_CONFIG
from content_creator.pipelines.modules import VideoGenerator, NewsScraper, FullArticle
from content_creator.pipelines.processed import is_processed, mark_processed

# ========================
# Registre
# ========================
TOOLS = {}


def tool(schema: dict):
    """Enregistre une fonction comme tool. `schema` = {name, description, parameters}."""
    def deco(fn):
        TOOLS[schema["name"]] = {"schema": schema, "fn": fn}
        return fn
    return deco


def openai_tool_schemas(names=None) -> list:
    """Schémas au format OpenAI tools, filtrés sur `names` (les tools d'un skill) si fourni."""
    items = TOOLS.values() if names is None else [TOOLS[n] for n in names if n in TOOLS]
    return [{"type": "function", "function": t["schema"]} for t in items]


def dispatch(session, name: str, args: dict) -> dict:
    """Exécute un tool par nom, en injectant la session. Capture les erreurs."""
    if name not in TOOLS:
        return {"status": "error", "error": f"unknown tool: {name}"}
    try:
        return TOOLS[name]["fn"](session, **(args or {}))
    except Exception as e:
        return {"status": "error", "error": str(e)}


# ========================
# Session
# ========================
@dataclass
class VideoSession:
    """État partagé d'une vidéo en construction (muté par les tools)."""
    ctx: Ctx
    output_dir: str = OUTPUT_DIR
    name: str = None                              # nom du channel (namespace de dédup du scraping)
    models: dict = None                           # PoolModelConfig du channel (rôles -> ModelConfig)
    voice: dict = None                            # voice_generator ModelConfig (voix défaut + clé Google)
    characters: dict = None                       # {name: {voice, description, portrait_url}} résolus
    ressources: dict = None                       # context.ressources (urls/local_paths/audio_paths/notes)
    article: object = None                        # FullArticle source (pour write_script)
    script: str = None                            # script écrit par le master (write_script)
    plan: list = field(default_factory=list)     # specs planifiés, dans l'ordre
    clips: list = field(default_factory=list)    # plans rendus (rempli par render_plan)
    fetched_images: list = field(default_factory=list)  # images web téléchargées (search_web_image) -> supprimées en fin de vidéo
    web_images: dict = field(default_factory=dict)      # {query: {local_path, url}} récupérées du web
    final_video: str = None
    clip_no: int = 0


# ========================
# Helpers ffmpeg / rendu
# ========================
def mix_music(video: str, music: str, out: str, volume: float = 0.15) -> str:
    """Mixe une musique (bouclée, bas volume) sous l'audio existant de la vidéo."""
    sh(["ffmpeg", "-y", "-i", video, "-stream_loop", "-1", "-i", music,
        "-filter_complex",
        f"[1:a]volume={volume}[m];[0:a][m]amix=inputs=2:duration=first:normalize=0[a]",
        "-map", "0:v", "-map", "[a]", "-c:v", "copy", "-c:a", "aac", "-shortest", out])
    return out


def _render_spec(session: "VideoSession", spec: dict) -> dict:
    """Rend UN plan planifié -> clip final 9:16. Threadable (chemins/seed uniques par idx)."""
    t0 = time.time()
    idx = spec["idx"]
    d = session.output_dir
    narration = os.path.join(d, f"narration_{idx+1}.mp3")
    raw = os.path.join(d, f"plan_{idx+1}_raw.mp4")
    final = os.path.join(d, f"plan_{idx+1}.mp4")
    ltx_params = spec.get("ltx_params") or {}
    models = session.models or {}
    # Voix propagée depuis le channel (voice_generator) / le personnage du plan :
    # nom de voix + style (ton, Gemini) + voice_model + langue, et la clé/endpoint Google.
    _vprov = (session.voice or {}).get("provider") or {}
    _vs = spec.get("voice") or {}
    voice_kw = {"voice": _vs.get("voice"), "style": _vs.get("style"),
                "voice_model": _vs.get("voice_model"), "language": _vs.get("language"),
                "api_key": _vprov.get("token"), "base_url": _vprov.get("base_url")}
    try:
        if spec["kind"] == "talking":
            _, dur = synthesize_audio(session.ctx.summarizer, spec["text"], narration, **voice_kw)
            audio_url = upload_public(session.ctx.gcs, narration, f"media/test/narration_{idx+1}.mp3")
            generate_lipsync(spec["portrait_url"], audio_url, spec["video_prompt"],
                             spec["seed"], raw, audio_path=narration, ltx_params=ltx_params,
                             model_config=models.get("lip_sync"))
            if VIDEO_BACKEND_CONFIG["use_ltx_lipsync"]:
                # LTX i2v ne porte pas la narration : on muxe la narration TTS comme bande-son.
                reframe_vertical(raw, final, audio_in=narration)
            else:
                reframe_vertical(raw, final)                  # Pruna : audio narration déjà dans la vidéo
        elif spec["kind"] == "media":
            # Clip ou IMAGE FOURNI (chemin local ou URL des ressources) : on normalise en 9:16.
            src = spec["source"]
            local = src if os.path.exists(src) else download(src, raw)
            is_img = is_image_path(src) or is_image_path(local)
            if spec.get("narration_text"):
                _, dur = synthesize_audio(session.ctx.summarizer, spec["narration_text"], narration,
                                          **voice_kw)
                if is_img:
                    image_to_clip(local, final, duration=dur, audio_in=narration)  # image fixe sur voix off
                else:
                    reframe_vertical(local, final, audio_in=narration)   # voix off remplace l'audio source
            elif is_img:
                dur = float(spec.get("image_duration_s") or 4.0)
                image_to_clip(local, final, duration=dur)                # image fixe, durée fixe, muette
            else:
                reframe_vertical(local, final)                       # garde l'audio d'origine
                dur = ffprobe_duration(final)
        else:  # broll
            _, dur = synthesize_audio(session.ctx.summarizer, spec["narration_text"], narration,
                                      **voice_kw)
            duration = max(2, min(15, int(round(dur + 0.8))))
            generate_broll(spec["shot"], duration, spec["seed"], spec["media"], raw,
                           ltx_params=ltx_params, model_config=models.get("video_generator"))
            reframe_vertical(raw, final, audio_in=narration)  # remplace l'audio par la narration
        secs = round(time.time() - t0, 1)
        print(f"   ✓ [plan {idx+1} {spec['kind']}] {os.path.basename(final)} ({secs}s)", flush=True)
        return {"idx": idx, "kind": spec["kind"], "ok": True, "clip": final,
                "duration_s": round(dur, 1), "render_s": secs}
    except Exception as e:
        print(f"   ✗ [plan {idx+1} {spec['kind']}] {e}", flush=True)
        return {"idx": idx, "kind": spec["kind"], "ok": False, "error": str(e)}


def render_plan(session: "VideoSession", workers: int = None) -> list:
    """Rend TOUS les plans planifiés EN PARALLÈLE, remet dans l'ordre, remplit session.clips."""
    specs = session.plan
    workers = workers or max(1, len(specs))
    print(f"🚀 Rendu de {len(specs)} plans en parallèle ({workers} workers)...", flush=True)
    with ThreadPoolExecutor(max_workers=workers) as ex:
        results = list(ex.map(lambda s: _render_spec(session, s), specs))
    results.sort(key=lambda r: r["idx"])                      # ordre du script préservé
    session.clips = [r["clip"] for r in results if r.get("ok")]
    return results


# ========================
# TOOLS — planification (instantanés)
# ========================
# Propriétés de paramètres LTX réutilisées par les deux tools de planification.
# N'ONT D'EFFET QUE si le backend LTX local est actif (USE_LTX_BROLL/USE_LTX_LIPSYNC) ;
# sinon ignorées. Tous OPTIONNELS : laisser vide => défauts du .env / pipeline.
_LTX_PARAM_PROPS = {
    "duration_s": {"type": "number", "description": "Optional (LTX): target shot duration in seconds "
                   "(rounded to the 8k+1 format). Default: narration length. Keep 2–10 s."},
    "width": {"type": "integer", "description": "Optional (LTX): width in px (multiple of 64, server-rounded). "
              "Default: 9:16 format from .env. Only change for a specific need (concat consistency)."},
    "height": {"type": "integer", "description": "Optional (LTX): height in px (multiple of 64). Default: 9:16 from .env."},
    "frame_rate": {"type": "number", "description": "Optional (LTX): frames/s. Default: .env (24)."},
    "num_inference_steps": {"type": "integer", "description": "Optional (LTX): number of denoising steps "
                            "(higher = slightly better, slower). Server default: 30."},
    "image_strength": {"type": "number", "description": "Optional (LTX i2v): adherence to the reference image "
                       "0–1 (1=stick tightly, 0.7–0.85=more movement freedom)."},
    "hdr": {"type": "boolean", "description": "Optional (LTX): HDR refinement pass (≈2× slower). "
            "Reserve for KEY shots."},
}

# Clés de _LTX_PARAM_PROPS = les noms d'args LTX à extraire des kwargs d'un tool.
_LTX_PARAM_KEYS = tuple(_LTX_PARAM_PROPS.keys())


def _collect_ltx_params(kwargs: dict) -> dict:
    """Extrait les params LTX fournis (non None) d'un appel de tool -> dict propre."""
    return {k: kwargs[k] for k in _LTX_PARAM_KEYS if kwargs.get(k) is not None}


# Propriété `character` partagée par les tools de planification.
_CHARACTER_PROP = {
    "character": {"type": "string", "description": "Optional: name of a character defined for this "
                  "channel. Its VOICE, its APPEARANCE (portrait) and its DESCRIPTION are then applied. "
                  "No value: channel default voice."},
}


def _resolve_character(session: "VideoSession", character: str = None) -> tuple:
    """Résout (voice_settings, char) pour un personnage nommé.
    `voice_settings` = {voice, style, voice_model, language} (style/Gemini propagés par personnage ;
    voix par défaut = voice_generator.model_name). `char` = le personnage résolu
    {portrait_url, local_image, description, …} ou {} si aucun. Pas d'avatar global."""
    default_voice = (session.voice or {}).get("model_name")
    char = dict((session.characters or {}).get(character) or {}) if character else {}
    voice_settings = {
        "voice": char.get("voice") or default_voice,
        "style": char.get("style"),
        "voice_model": char.get("voice_model"),
        "language": char.get("language"),
    }
    return voice_settings, char


@tool({
    "name": "add_talking_clip",
    "description": "PLANS a shot FACING THE CAMERA: the avatar says `text`, lips synced (lip-sync). "
                   "Instant (rendering happens at assemble_video). Use it for the hook, "
                   "the key sentences and the conclusion.",
    "parameters": {"type": "object", "properties": {
        "text": {"type": "string", "description": "Exact text the avatar speaks (one segment/sentence)."},
        "expression": {"type": "string", "description": "Optional: tone/expression (e.g. 'warm smile')."},
        **_CHARACTER_PROP,
        **_LTX_PARAM_PROPS,
    }, "required": ["text"]},
})
def add_talking_clip(session: VideoSession, text: str, expression: str = None,
                     character: str = None, **kwargs) -> dict:
    voice, char = _resolve_character(session, character)
    portrait, description = char.get("portrait_url"), char.get("description")
    if not portrait:
        return {"status": "error", "error": "facing-camera shot (lip-sync) impossible: pass a "
                "`character` that has an image. Otherwise use add_broll_clip or add_media_clip."}
    idx = session.clip_no
    session.clip_no += 1
    video_prompt = " ".join(p for p in [expression, description, PRUNA_MOVEMENT] if p)
    session.plan.append({
        "kind": "talking", "idx": idx, "text": text, "video_prompt": video_prompt,
        "portrait_url": portrait, "voice": voice, "seed": SEED_BASE + idx,
        "ltx_params": _collect_ltx_params(kwargs),
    })
    return {"status": "ok", "queued": "talking", "slot": idx + 1, "character": character, "text": text[:60]}


@tool({
    "name": "add_broll_clip",
    "description": "PLANS a cinematic B-ROLL shot (avatar in profile/walking/ambience) with "
                   "the narration as voice-over. Instant (rendering at assemble_video). For "
                   "descriptive/ambience sentences.",
    "parameters": {"type": "object", "properties": {
        "narration_text": {"type": "string", "description": "Voice-over text for this shot."},
        "shot_description": {"type": "string", "description": "Video prompt for the engine (LTX), "
                             "written according to the prompting SKILL: a single continuous shot, chronological, "
                             "in the present tense, framing + light + action + camera, in English, reflecting the mood."},
        "reference_image": {"type": "string", "description": "Optional: URL of a reference image "
                            "to animate as the i2v INPUT of THIS shot (instead of the current background). Typically "
                            "the `url` returned by `search_web_image` for a real entity with no provided image. "
                            "The engine will start from this image."},
        **_CHARACTER_PROP,
        **_LTX_PARAM_PROPS,
    }, "required": ["narration_text", "shot_description"]},
})
def add_broll_clip(session: VideoSession, narration_text: str, shot_description: str,
                   character: str = None, reference_image: str = None, **kwargs) -> dict:
    voice, char = _resolve_character(session, character)
    description = char.get("description")
    idx = session.clip_no
    session.clip_no += 1
    # La description du personnage entre dans le prompt pour que le moteur le dessine correctement.
    shot = f"{shot_description} Character: {description}." if description else shot_description
    # Image de réf du plan (i2v) : reference_image fournie > portrait du personnage > aucune (t2v).
    ref = reference_image or char.get("portrait_url")
    media = [{"type": "reference_image", "url": ref}] if ref else []
    session.plan.append({
        "kind": "broll", "idx": idx, "narration_text": narration_text,
        "shot": shot, "media": media, "voice": voice, "seed": SEED_BASE + idx,
        "ltx_params": _collect_ltx_params(kwargs),
    })
    return {"status": "ok", "queued": "broll", "slot": idx + 1, "character": character,
            "reference_image": bool(reference_image)}


@tool({
    "name": "add_media_clip",
    "description": "PLANS a shot from a PROVIDED VIDEO CLIP or IMAGE (editing). `source` = "
                   "a local path OR a URL from the resources OR an image fetched by "
                   "`search_web_image`. The media is normalized to the SAME format as the other shots of the "
                   "video (consistency guaranteed): a video is reframed, an IMAGE becomes a still shot "
                   "(over the voice-over if provided, otherwise `image_duration_s`). Instant (rendering at "
                   "assemble_video). Key tool for editing and for ILLUSTRATING an entity with a web image.",
    "parameters": {"type": "object", "properties": {
        "source": {"type": "string", "description": "Local path OR URL of the video clip / image to "
                   "integrate (available resources or result of search_web_image)."},
        "narration_text": {"type": "string", "description": "Optional: TTS voice-over that REPLACES "
                           "the clip's audio (or comments on the image). Leave empty to keep the original audio."},
        "image_duration_s": {"type": "number", "description": "Optional (silent IMAGE only): duration "
                             "of the still shot in seconds. Default 4. Ignored for a video or if narration_text."},
        **_CHARACTER_PROP,
    }, "required": ["source"]},
})
def add_media_clip(session: VideoSession, source: str, narration_text: str = None,
                   image_duration_s: float = None, character: str = None) -> dict:
    voice, _ = _resolve_character(session, character)
    idx = session.clip_no
    session.clip_no += 1
    session.plan.append({
        "kind": "media", "idx": idx, "source": source,
        "narration_text": narration_text, "image_duration_s": image_duration_s,
        "voice": voice, "seed": SEED_BASE + idx,
    })
    return {"status": "ok", "queued": "media", "slot": idx + 1, "source": source}


# ========================
# TOOLS — acquisition de contenu (scraping)
# ========================
def _article_to_text(article) -> str:
    """Article scrapé -> texte (titre + sections), tronqué pour le contexte du master."""
    parts = [article.link.title]
    for b in article.content:
        txt = getattr(b, "content", "") or ""
        if txt:
            parts.append(txt)
    return "\n".join(parts)[:6000]


@tool({
    "name": "scrape_article",
    "description": "Scrapes the pages listed in the RESOURCES (urls), selects the FIRST "
                   "article still UNTREATED for this channel (dedup), marks it as treated and "
                   "returns its text to you. Call it FIRST for a news-based video; "
                   "then you can adapt this text with `write_script`.",
    "parameters": {"type": "object", "properties": {}},
})
def scrape_article(session: VideoSession) -> dict:
    urls = (session.ressources or {}).get("urls") or []
    if not urls:
        return {"status": "error", "error": "no url in the resources (context.ressources.urls)"}
    scraper = NewsScraper()
    articles = []
    for url in urls:
        links = scraper.scrape_links_older_than_24h(url)
        for link in {ln.href: ln for ln in links}.values():
            blocks = scraper.scrape_article(link.href)
            if blocks:
                articles.append(FullArticle(link=link, content=blocks))
    if not articles:
        return {"status": "error", "error": "no article scraped"}
    for article in articles:
        if not is_processed(session.name, article.link.href):
            session.article = article
            mark_processed(session.name, article.link.href)
            return {"status": "ok", "title": article.link.title,
                    "text": _article_to_text(article)}
    return {"status": "error", "error": "all articles already treated"}


# ========================
# TOOLS — recherche d'image web
# ========================
@tool({
    "name": "search_web_image",
    "description": "Searches and downloads an IMAGE from the web (Google Images) for a REAL and "
                   "NON-PUBLIC/non-fictional entity/subject that the engine cannot draw reliably and for "
                   "which NO image is provided in the resources: e.g. a little-known person, a "
                   "specific product/logo, a specific place, a local event. Not needed for a celebrity, "
                   "a very well-known brand or a fictional/generic subject (the engine handles those on its own). "
                   "ON SUCCESS, the tool returns `url` (+ `local_path`): reuse `url` as the "
                   "`reference_image` of an `add_broll_clip` (i2v input, the engine animates the image) OR as the "
                   "`source` of an `add_media_clip` (still illustration shot in the edit). "
                   "ON FAILURE (status=error), NO image could be fetched: CHANGE strategy — "
                   "generate the shot without a reference image by describing the whole scene in `shot_description`, "
                   "or rephrase the query once, or drop that visual.",
    "parameters": {"type": "object", "properties": {
        "query": {"type": "string", "description": "Precise search query: exact name of the entity/"
                  "subject, 2-5 words (e.g. 'Jean Dupont mayor Annecy', 'Sony WH-1000XM5 headset', 'Metz station')."},
    }, "required": ["query"]},
})
def search_web_image(session: VideoSession, query: str) -> dict:
    idx = len(session.fetched_images)
    local = fetch_web_image(query, session.output_dir, idx=idx)
    if not local:
        return {"status": "error",
                "error": f"no usable image found for \"{query}\". The image fetch "
                         "FAILED: change strategy — generate the shot without a reference image (describe the whole "
                         "scene in shot_description), rephrase the query, or drop that visual."}
    session.fetched_images.append(local)
    try:
        url = upload_public(session.ctx.gcs, local, f"media/test/web_image_{idx}.jpg")
    except Exception as e:
        # L'image locale existe (utilisable en montage), mais pas d'URL publique pour l'i2v.
        return {"status": "ok", "query": query, "local_path": local, "url": None,
                "note": f"image downloaded locally but public upload failed ({e}). Usable via "
                        "add_media_clip (`source`={local_path}); no i2v without a URL. Deleted at the end of the video."}
    session.web_images[query] = {"local_path": local, "url": url}
    return {"status": "ok", "query": query, "local_path": local, "url": url,
            "note": "image downloaded. Pass `url` as `reference_image` of add_broll_clip (i2v input) "
                    "OR as `source` of add_media_clip (illustration shot). Deleted at the end of the video."}


# ========================
# TOOLS — écriture du script
# ========================
@tool({
    "name": "write_script",
    "description": "Writes the narration SCRIPT from the provided article. YOU write `style` "
                   "(tone, angle, pacing, intent) BASED ON THE MOOD — you are the one writing the writing "
                   "prompt. Call it FIRST; the script is returned to you to split afterward.",
    "parameters": {"type": "object", "properties": {
        "style": {"type": "string", "description": "Your tone/style/angle instructions for writing the "
                  "script, derived from the mood (e.g. 'dramatic tone, short tense sentences, rising tension')."},
    }, "required": ["style"]},
})
def write_script(session: VideoSession, style: str = "") -> dict:
    if session.article is None:
        return {"status": "error",
                "error": "no article: the message already contains the script, split it directly"}
    script = session.ctx.summarizer.summarize_article(session.article, mood=style or None)
    if not script:
        return {"status": "error", "error": "script writing failed"}
    session.script = script
    return {"status": "ok", "script": script}


# ========================
# TOOLS — décor / rendu / finition
# ========================
@tool({
    "name": "set_scene_background",
    "description": "Places a CHARACTER in a coherent BACKGROUND (FLUX Kontext), preserving their "
                   "identity. Updates the character's portrait: their next shots (facing camera / "
                   "b-roll) will use this background. Call it BEFORE planning the character's shots.",
    "parameters": {"type": "object", "properties": {
        "character": {"type": "string", "description": "Character name (must have an image)."},
        "description": {"type": "string", "description": "The BACKGROUND/ambience only, inferred from "
                        "the context (e.g. 'football stadium at sunset', 'clean TV studio'). "
                        "Do NOT describe the person."},
    }, "required": ["character", "description"]},
})
def set_scene_background(session: VideoSession, character: str, description: str) -> dict:
    _, char = _resolve_character(session, character)
    local = char.get("local_image")
    if not local:
        return {"status": "error", "error": f"character '{character}' unknown or without an image: "
                "the background (FLUX Kontext) applies to a character's portrait."}
    out = os.path.join(session.output_dir, f"scene_{character}.jpg")
    prompt = BACKGROUND_TEMPLATE.format(scene=description)   # identité préservée + décor inféré
    scene = prepare_scene_portrait(regen=True, src=local, prompt=prompt, out=out)
    url = upload_public(session.ctx.gcs, scene, f"media/test/scene_{character}.jpg")
    # Met à jour le portrait du personnage -> ses prochains plans utiliseront ce décor.
    session.characters[character]["portrait_url"] = url
    return {"status": "ok", "character": character, "scene": description,
            "note": "background applied to the character; their next shots will use it"}


@tool({
    "name": "assemble_video",
    "description": "RENDERS all planned shots IN PARALLEL (in order) then assembles them into "
                   "a final video. Call it once ALL shots are planned.",
    "parameters": {"type": "object", "properties": {}},
})
def assemble_video(session: VideoSession) -> dict:
    if not session.plan:
        return {"status": "error", "error": "no shot planned (use add_talking_clip/add_broll_clip)"}
    results = render_plan(session)
    if not session.clips:
        return {"status": "error", "error": "no shot rendered successfully", "plans": results}
    out = os.path.join(session.output_dir, "final_story.mp4")
    concat_clips(session.clips, out)
    session.final_video = out
    return {"status": "ok", "final_video": out, "n_clips": len(session.clips), "plans": results}


@tool({
    "name": "add_subtitles",
    "description": "Burns animated subtitles onto the final video (Creatomate). Call it AFTER assemble_video.",
    "parameters": {"type": "object", "properties": {}},
})
def add_subtitles(session: VideoSession) -> dict:
    if not session.final_video:
        return {"status": "error", "error": "call assemble_video first"}
    url = upload_public(session.ctx.gcs, session.final_video, "media/test/final_for_subs.mp4")
    vg = VideoGenerator()
    resp = vg.add_subtitles(url)
    if not resp:
        return {"status": "error", "error": "add_subtitles failed"}
    final = vg.wait_for_render(resp.id, max_wait=120, poll_interval=3)
    if not final:
        return {"status": "error", "error": "subtitle render timeout"}
    out = os.path.join(session.output_dir, "final_subtitled.mp4")
    download(str(final.url), out)
    session.final_video = out
    return {"status": "ok", "final_video": out}


@tool({
    "name": "add_background_music",
    "description": "Adds a low-volume music bed under the narration of the final video. "
                   "Call it AFTER assemble_video.",
    "parameters": {"type": "object", "properties": {
        "source": {"type": "string", "description": "Local path or URL of a music audio file."},
        "volume": {"type": "number", "description": "Music volume 0-1 (default 0.15)."},
    }, "required": ["source"]},
})
def add_background_music(session: VideoSession, source: str, volume: float = 0.15) -> dict:
    if not session.final_video:
        return {"status": "error", "error": "call assemble_video first"}
    out = os.path.join(session.output_dir, "final_music.mp4")
    mix_music(session.final_video, source, out, volume)
    session.final_video = out
    return {"status": "ok", "final_video": out}


# ========================
# Cleanup
# ========================
def cleanup_fetched_images(session: "VideoSession") -> int:
    """Supprime les images web téléchargées par search_web_image (fichiers LOCAUX) — à appeler
    en fin de vidéo. Les copies GCS sont conservées. Retourne le nombre de fichiers supprimés."""
    removed = 0
    for path in session.fetched_images:
        try:
            if path and os.path.exists(path):
                os.remove(path)
                removed += 1
        except OSError as e:
            print(f"   ⚠ image web non supprimée ({path}): {e}", flush=True)
    session.fetched_images.clear()
    session.web_images.clear()
    return removed

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
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from content_creator.agentic.asset_library import (
    asset_key,
    library_add,
    library_find,
    library_get,
    library_list,
)
from content_creator.agentic.capabilities import (
    BACKGROUND_TEMPLATE,
    ESTABLISH_TEMPLATE,
    OUTPUT_DIR,
    PRUNA_MOVEMENT,
    SEED_BASE,
    TALKING_SEED,
    Ctx,
    _probe_size,
    burn_ass,
    burn_subtitles,
    concat_clips,
    crop_to_vertical,
    download,
    elevenlabs_forced_alignment,
    fetch_web_image,
    ffprobe_duration,
    generate_broll,
    generate_lipsync,
    image_to_clip,
    is_image_path,
    prepare_scene_portrait,
    reframe_vertical,
    sh,
    synthesize_audio,
    upload_public,
    words_to_ass,
    words_to_srt,
)
from content_creator.agentic.capabilities import (
    edit_minimax_image as _cap_minimax_edit,
)
from content_creator.agentic.capabilities import (
    generate_minimax_image as _cap_minimax_image,
)
from content_creator.agentic.capabilities import (
    generate_minimax_video as _cap_minimax_video,
)
from content_creator.config.config import VIDEO_BACKEND_CONFIG
from content_creator.pipelines.modules import FullArticle, NewsScraper, VideoGenerator
from content_creator.pipelines.processed import is_processed, mark_processed

# ========================
# Registre
# ========================
TOOLS = {}

# Tools EXCLUS de l'exposition « tous les tools » (skill.tool_names = None => créateur libre) :
# ils ne sont pertinents que dans un contexte précis (ex. load_style_skill seulement si le
# video_generator est un modèle avec des skills de style). run_agent les ajoute EXPLICITEMENT le cas échéant.
HIDDEN_TOOLS = {"load_style_skill"}


def tool(schema: dict):
    """Enregistre une fonction comme tool. `schema` = {name, description, parameters}."""

    def deco(fn):
        TOOLS[schema["name"]] = {"schema": schema, "fn": fn}
        return fn

    return deco


def openai_tool_schemas(names=None) -> list:
    """Schémas au format OpenAI tools. `names` fourni => filtre sur ces tools (dans l'ordre) ;
    None => TOUS les tools SAUF ceux masqués (HIDDEN_TOOLS), qui doivent être demandés nommément.
    """
    if names is None:
        items = [t for n, t in TOOLS.items() if n not in HIDDEN_TOOLS]
    else:
        items = [TOOLS[n] for n in names if n in TOOLS]
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
    name: str = None  # nom du channel (namespace de dédup du scraping)
    models: dict = None  # PoolModelConfig du channel (rôles -> ModelConfig)
    voice: dict = None  # voice_generator ModelConfig (voix défaut + clé Google)
    characters: dict = None  # {name: {voice, description, portrait_url}} résolus
    ressources: dict = None  # context.ressources (urls/local_paths/audio_paths/notes)
    parameters: dict = (
        None  # paramètres résolus du run {name: valeur} (défauts + overrides)
    )
    article: object = None  # FullArticle source (pour write_script)
    script: str = None  # script écrit par le master (write_script)
    plan: list = field(default_factory=list)  # specs planifiés, dans l'ordre
    clips: list = field(default_factory=list)  # plans rendus (rempli par render_plan)
    produced_clips: list = field(
        default_factory=list
    )  # clips rendus EN DIRECT (generate_minimax_video / generate_video), dans l'ordre — filet de finalisation si l'agent oublie assemble_video
    subtitled: bool = (
        False  # True une fois add_subtitles appliqué -> évite le double-burn (auto-sous-titres de finalisation)
    )
    fetched_images: list = field(
        default_factory=list
    )  # images web téléchargées (search_web_image) -> supprimées en fin de vidéo
    web_images: dict = field(
        default_factory=dict
    )  # {query: {local_path, url}} récupérées du web
    final_video: str = None
    clip_no: int = 0


# ========================
# Helpers ffmpeg / rendu
# ========================
def mix_music(video: str, music: str, out: str, volume: float = 0.15) -> str:
    """Mixe une musique (bouclée, bas volume) sous l'audio existant de la vidéo."""
    sh(
        [
            "ffmpeg",
            "-y",
            "-i",
            video,
            "-stream_loop",
            "-1",
            "-i",
            music,
            "-filter_complex",
            f"[1:a]volume={volume}[m];[0:a][m]amix=inputs=2:duration=first:normalize=0[a]",
            "-map",
            "0:v",
            "-map",
            "[a]",
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-shortest",
            out,
        ]
    )
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
    # INCRÉMENTAL : ce plan a déjà été rendu (retry d'assemble_video après un échec partiel) -> on le
    # RÉUTILISE tel quel, sans re-TTS ni re-génération vidéo. Chaque run a son output_dir horodaté,
    # donc pas de cache périmé entre runs : un retry ne refait QUE les plans manquants/échoués.
    if os.path.exists(final) and os.path.getsize(final) > 0:
        try:
            dur = ffprobe_duration(final)
        except Exception:
            dur = float(spec.get("duration_s") or 0.0)
        print(f"   ♻️ [plan {idx+1} {spec['kind']}] déjà rendu — réutilisé", flush=True)
        return {
            "idx": idx,
            "kind": spec["kind"],
            "ok": True,
            "clip": final,
            "duration_s": round(dur, 1),
            "render_s": 0.0,
        }
    models = session.models or {}
    # Voix propagée depuis le channel (voice_generator) / le personnage du plan :
    # nom de voix + style (ton, Gemini) + voice_model + langue, et la clé/endpoint Google.
    _vprov = (session.voice or {}).get("provider") or {}
    _vs = spec.get("voice") or {}
    voice_kw = {
        "voice": _vs.get("voice"),
        "style": _vs.get("style"),
        "voice_model": _vs.get("voice_model"),
        "language": _vs.get("language"),
        "api_key": _vprov.get("token"),
        "base_url": _vprov.get("base_url"),
    }
    try:
        if spec["kind"] == "talking":
            _, dur = synthesize_audio(
                session.ctx.summarizer, spec["text"], narration, **voice_kw
            )
            audio_url = upload_public(
                session.ctx.gcs, narration, f"media/test/narration_{idx+1}.mp3"
            )
            generate_lipsync(
                spec["portrait_url"],
                audio_url,
                spec["video_prompt"],
                spec["seed"],
                raw,
                audio_path=narration,
                ltx_params=ltx_params,
                model_config=models.get("video_avatar"),
            )
            if VIDEO_BACKEND_CONFIG["use_ltx_lipsync"]:
                # LTX i2v ne porte pas la narration : on muxe la narration TTS comme bande-son.
                reframe_vertical(raw, final, audio_in=narration)
            else:
                reframe_vertical(
                    raw, final
                )  # Pruna : audio narration déjà dans la vidéo
        elif spec["kind"] == "media":
            # Clip ou IMAGE FOURNI (chemin local ou URL des ressources) : on normalise en 9:16.
            src = spec["source"]
            local = src if os.path.exists(src) else download(src, raw)
            is_img = is_image_path(src) or is_image_path(local)
            if spec.get("narration_text"):
                _, dur = synthesize_audio(
                    session.ctx.summarizer,
                    spec["narration_text"],
                    narration,
                    **voice_kw,
                )
                if is_img:
                    image_to_clip(
                        local, final, duration=dur, audio_in=narration
                    )  # image fixe sur voix off
                else:
                    reframe_vertical(
                        local, final, audio_in=narration
                    )  # voix off remplace l'audio source
            elif is_img:
                dur = float(spec.get("image_duration_s") or 4.0)
                image_to_clip(
                    local, final, duration=dur
                )  # image fixe, durée fixe, muette
            else:
                reframe_vertical(local, final)  # garde l'audio d'origine
                dur = ffprobe_duration(final)
        else:  # broll
            _, dur = synthesize_audio(
                session.ctx.summarizer, spec["narration_text"], narration, **voice_kw
            )
            duration = max(2, min(15, int(round(dur + 0.8))))
            generate_broll(
                spec["shot"],
                duration,
                spec["seed"],
                spec["media"],
                raw,
                ltx_params=ltx_params,
                model_config=models.get("video_generator"),
            )
            reframe_vertical(
                raw, final, audio_in=narration
            )  # remplace l'audio par la narration
        secs = round(time.time() - t0, 1)
        print(
            f"   ✓ [plan {idx+1} {spec['kind']}] {os.path.basename(final)} ({secs}s)",
            flush=True,
        )
        return {
            "idx": idx,
            "kind": spec["kind"],
            "ok": True,
            "clip": final,
            "duration_s": round(dur, 1),
            "render_s": secs,
        }
    except Exception as e:
        print(f"   ✗ [plan {idx+1} {spec['kind']}] {e}", flush=True)
        return {"idx": idx, "kind": spec["kind"], "ok": False, "error": str(e)}


def render_plan(session: "VideoSession", workers: int = None) -> list:
    """Rend TOUS les plans planifiés EN PARALLÈLE, remet dans l'ordre, remplit session.clips."""
    specs = session.plan
    workers = workers or max(1, len(specs))
    print(
        f"🚀 Rendu de {len(specs)} plans en parallèle ({workers} workers)...",
        flush=True,
    )
    with ThreadPoolExecutor(max_workers=workers) as ex:
        results = list(ex.map(lambda s: _render_spec(session, s), specs))
    results.sort(key=lambda r: r["idx"])  # ordre du script préservé
    session.clips = [r["clip"] for r in results if r.get("ok")]
    return results


# ========================
# TOOLS — planification (instantanés)
# ========================
# Propriétés de paramètres LTX réutilisées par les deux tools de planification.
# N'ONT D'EFFET QUE si le backend LTX local est actif (USE_LTX_BROLL/USE_LTX_LIPSYNC) ;
# sinon ignorées. Tous OPTIONNELS : laisser vide => défauts du .env / pipeline.
_LTX_PARAM_PROPS = {
    "duration_s": {
        "type": "number",
        "description": "Optional (LTX): target shot duration in seconds "
        "(rounded to the 8k+1 format). Default: narration length. Keep 2–10 s.",
    },
    "width": {
        "type": "integer",
        "description": "Optional (LTX): width in px (multiple of 64, server-rounded). "
        "Default: 9:16 format from .env. Only change for a specific need (concat consistency).",
    },
    "height": {
        "type": "integer",
        "description": "Optional (LTX): height in px (multiple of 64). Default: 9:16 from .env.",
    },
    "frame_rate": {
        "type": "number",
        "description": "Optional (LTX): frames/s. Default: .env (24).",
    },
    "num_inference_steps": {
        "type": "integer",
        "description": "Optional (LTX): number of denoising steps "
        "(higher = slightly better, slower). Server default: 30.",
    },
    "image_strength": {
        "type": "number",
        "description": "Optional (LTX i2v): adherence to the reference image "
        "0–1 (1=stick tightly, 0.7–0.85=more movement freedom).",
    },
    "hdr": {
        "type": "boolean",
        "description": "Optional (LTX): HDR refinement pass (≈2× slower). "
        "Reserve for KEY shots.",
    },
}

# Clés de _LTX_PARAM_PROPS = les noms d'args LTX à extraire des kwargs d'un tool.
_LTX_PARAM_KEYS = tuple(_LTX_PARAM_PROPS.keys())


def _collect_ltx_params(kwargs: dict) -> dict:
    """Extrait les params LTX fournis (non None) d'un appel de tool -> dict propre."""
    return {k: kwargs[k] for k in _LTX_PARAM_KEYS if kwargs.get(k) is not None}


# Propriété `character` partagée par les tools de planification.
_CHARACTER_PROP = {
    "character": {
        "type": "string",
        "description": "Optional: name of a character defined for this "
        "channel. Its VOICE, its APPEARANCE (portrait) and its DESCRIPTION are then applied. "
        "No value: channel default voice.",
    },
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


@tool(
    {
        "name": "add_talking_clip",
        "description": "PLANS a shot FACING THE CAMERA: the avatar says `text`, lips synced (lip-sync). "
        "Instant (rendering happens at assemble_video). Use it for the hook, "
        "the key sentences and the conclusion.",
        "parameters": {
            "type": "object",
            "properties": {
                "text": {
                    "type": "string",
                    "description": "Exact text the avatar speaks (one segment/sentence).",
                },
                "expression": {
                    "type": "string",
                    "description": "Optional: tone/expression (e.g. 'warm smile').",
                },
                **_CHARACTER_PROP,
                **_LTX_PARAM_PROPS,
            },
            "required": ["text"],
        },
    }
)
def add_talking_clip(
    session: VideoSession,
    text: str,
    expression: str = None,
    character: str = None,
    **kwargs,
) -> dict:
    voice, char = _resolve_character(session, character)
    portrait, description = char.get("portrait_url"), char.get("description")
    if not portrait:
        return {
            "status": "error",
            "error": "facing-camera shot (lip-sync) impossible: pass a "
            "`character` that has an image. Otherwise use add_broll_clip or add_media_clip.",
        }
    idx = session.clip_no
    session.clip_no += 1
    video_prompt = " ".join(p for p in [expression, description, PRUNA_MOVEMENT] if p)
    session.plan.append(
        {
            "kind": "talking",
            "idx": idx,
            "text": text,
            "video_prompt": video_prompt,
            "portrait_url": portrait,
            "voice": voice,
            "seed": TALKING_SEED,
            "ltx_params": _collect_ltx_params(kwargs),
        }
    )
    return {
        "status": "ok",
        "queued": "talking",
        "slot": idx + 1,
        "character": character,
        "text": text[:60],
    }


@tool(
    {
        "name": "add_broll_clip",
        "description": "PLANS a cinematic B-ROLL shot (avatar in profile/walking/ambience) with "
        "the narration as voice-over. Instant (rendering at assemble_video). For "
        "descriptive/ambience sentences.",
        "parameters": {
            "type": "object",
            "properties": {
                "narration_text": {
                    "type": "string",
                    "description": "Voice-over text for this shot.",
                },
                "shot_description": {
                    "type": "string",
                    "description": "Video prompt for the engine (LTX), "
                    "written according to the prompting SKILL: a single continuous shot, chronological, "
                    "in the present tense, framing + light + action + camera, in English, reflecting the mood.",
                },
                "reference_image": {
                    "type": "string",
                    "description": "Optional: URL of a reference image "
                    "to animate as the i2v INPUT of THIS shot (instead of the current background). Typically "
                    "the `url` returned by `search_web_image` for a real entity with no provided image. "
                    "The engine will start from this image.",
                },
                **_CHARACTER_PROP,
                **_LTX_PARAM_PROPS,
            },
            "required": ["narration_text", "shot_description"],
        },
    }
)
def add_broll_clip(
    session: VideoSession,
    narration_text: str,
    shot_description: str,
    character: str = None,
    reference_image: str = None,
    **kwargs,
) -> dict:
    voice, char = _resolve_character(session, character)
    description = char.get("description")
    idx = session.clip_no
    session.clip_no += 1
    # La description du personnage entre dans le prompt pour que le moteur le dessine correctement.
    shot = (
        f"{shot_description} Character: {description}."
        if description
        else shot_description
    )
    # Image de réf du plan (i2v) : reference_image fournie > portrait du personnage > aucune (t2v).
    ref = reference_image or char.get("portrait_url")
    media = [{"type": "reference_image", "url": ref}] if ref else []
    session.plan.append(
        {
            "kind": "broll",
            "idx": idx,
            "narration_text": narration_text,
            "shot": shot,
            "media": media,
            "voice": voice,
            "seed": SEED_BASE + idx,
            "ltx_params": _collect_ltx_params(kwargs),
        }
    )
    return {
        "status": "ok",
        "queued": "broll",
        "slot": idx + 1,
        "character": character,
        "reference_image": bool(reference_image),
    }


@tool(
    {
        "name": "add_media_clip",
        "description": "PLANS a shot from a PROVIDED VIDEO CLIP or IMAGE (editing). `source` = "
        "a local path OR a URL from the resources OR an image fetched by "
        "`search_web_image`. The media is normalized to the SAME format as the other shots of the "
        "video (consistency guaranteed): a video is reframed, an IMAGE becomes a still shot "
        "(over the voice-over if provided, otherwise `image_duration_s`). Instant (rendering at "
        "assemble_video). Key tool for editing and for ILLUSTRATING an entity with a web image.",
        "parameters": {
            "type": "object",
            "properties": {
                "source": {
                    "type": "string",
                    "description": "Local path OR URL of the video clip / image to "
                    "integrate (available resources or result of search_web_image).",
                },
                "narration_text": {
                    "type": "string",
                    "description": "Optional: TTS voice-over that REPLACES "
                    "the clip's audio (or comments on the image). Leave empty to keep the original audio.",
                },
                "image_duration_s": {
                    "type": "number",
                    "description": "Optional (silent IMAGE only): duration "
                    "of the still shot in seconds. Default 4. Ignored for a video or if narration_text.",
                },
                **_CHARACTER_PROP,
            },
            "required": ["source"],
        },
    }
)
def add_media_clip(
    session: VideoSession,
    source: str,
    narration_text: str = None,
    image_duration_s: float = None,
    character: str = None,
) -> dict:
    voice, _ = _resolve_character(session, character)
    idx = session.clip_no
    session.clip_no += 1
    session.plan.append(
        {
            "kind": "media",
            "idx": idx,
            "source": source,
            "narration_text": narration_text,
            "image_duration_s": image_duration_s,
            "voice": voice,
            "seed": SEED_BASE + idx,
        }
    )
    return {"status": "ok", "queued": "media", "slot": idx + 1, "source": source}


# ========================
# TOOL — génération vidéo DIRECTE (rend et renvoie le fichier tout de suite)
# ========================
@tool(
    {
        "name": "generate_video",
        "description": "Generate a short video clip from a text prompt (and optionally a first-frame "
        "image for image-to-video) using the channel's video engine (e.g. LTX-2.5). "
        "UNLIKE add_broll_clip, this renders IMMEDIATELY and RETURNS the local path to "
        "the .mp4 — use it when you just want a video from a prompt. Write the prompt in "
        "English, cinematic and detailed.",
        "parameters": {
            "type": "object",
            "properties": {
                "prompt": {
                    "type": "string",
                    "description": "What the video shows (English, cinematic, detailed).",
                },
                "reference_image": {
                    "type": "string",
                    "description": "Optional URL (publicly reachable by the "
                    "video server) of a first-frame image for image-to-video.",
                },
                "seconds": {
                    "type": "integer",
                    "description": "Clip duration in seconds (4-15). Default 5.",
                },
            },
            "required": ["prompt"],
        },
    }
)
def generate_video(
    session: VideoSession, prompt: str, reference_image: str = None, seconds: int = 5
) -> dict:
    """Rend un clip via le moteur vidéo du channel (LTX-2.5 / H3, endpoint /v1/videos) et renvoie
    le chemin du MP4. base_url/token = provider du rôle video_generator (sinon env LTX25_URL).
    """
    from content_creator.agentic import sglang_video_client

    mc = (session.models or {}).get("video_generator") or {}
    provider = mc.get("provider") or {}
    base_url = provider.get("base_url") or os.environ.get("LTX25_URL")
    if not base_url:
        return {
            "status": "error",
            "error": "no video engine configured (set the video_generator "
            "provider on the channel, or export LTX25_URL)",
        }

    idx = session.clip_no
    session.clip_no += 1
    dest = os.path.join(session.output_dir, f"video_{idx + 1}.mp4")
    dur = max(4, min(15, int(seconds or 5)))
    conditions, task = [], "t2va"
    if reference_image:
        task = "fl2va"
        conditions = [
            {
                "type": "image",
                "uri": reference_image,
                "role": "keyframe",
                "frame_index": 0,
            }
        ]
    payload = {
        "model": mc.get("model_name") or "Lightricks/LTX-2.5",
        "prompt": prompt,
        "seconds": dur,
        "task": task,
        "conditions": conditions,
        "target": {
            "short_edge": 768,
            "aspect_ratio": "9:16",
            "duration_seconds": float(dur),
        },
        "seed": SEED_BASE + idx,
    }
    path = sglang_video_client.generate(
        base_url, provider.get("token") or "", payload, dest
    )
    session.produced_clips.append(
        path
    )  # trace pour la finalisation de secours (cf. run_agent)
    return {"status": "ok", "video": path, "seconds": dur}


# ========================
# TOOLS — MiniMax-H3 NATIF (l'audio est GÉNÉRÉ par le modèle : aucun TTS, aucun lip-sync)
# ========================
def _identity_lock_block(char: dict) -> str:
    """VERROU D'IDENTITÉ ref2va : construit un bloc `subject_definitions` + `retention_analysis`
    figeant l'apparence du personnage (champ `appearance`, à défaut `description`). Injecté EN TÊTE
    de chaque prompt et IDENTIQUE d'un clip à l'autre -> l'avatar ne dérive plus (coupe, tenue, micro,
    décor stables). Vide si aucune apparence connue (ex. reference_image brute sans personnage).
    """
    look = (char.get("appearance") or char.get("description") or "").strip()
    if not look:
        return ""
    return (
        "subject_definitions:\n"
        "<Subject 1> is the on-camera presenter from the reference image. Fixed, invariant appearance "
        f"(matches the reference exactly): {look}\n\n"
        "retention_analysis:\n"
        "<Subject 1>'s appearance is FULLY PRESERVED and UNCHANGED in every shot — face, age, hair, "
        "facial hair, skin tone, wardrobe and any worn accessories match the reference and the definition "
        "above exactly. Do NOT re-age, restyle, change the outfit, or add/remove props (glasses, hat, "
        "headphones, microphone) unless the shot description below explicitly requires it.\n\n"
    )


@tool(
    {
        "name": "generate_minimax_video",
        "description": "Generate ONE audiovisual clip with MiniMax-H3: the MODEL generates the VIDEO "
        "AND ITS AUDIO in a single pass (spoken lines + soundscape come FROM the prompt — "
        "NO TTS, NO lip-sync). Renders IMMEDIATELY and RETURNS the local .mp4 path (native "
        "audio kept). A REFERENCE IMAGE IS REQUIRED: pass a `character` (with an image) or a "
        "`reference_image` (avatar). It is used as an IDENTITY reference (ref2va). To keep the "
        "SAME look across clips (hair, wardrobe, headphones, microphone, setting), write the "
        "prompt in the H3 FULL-REFERENCE format and LOCK those traits in `subject_definitions` "
        "+ `retention_analysis` (fully preserved), reusing that block verbatim on every clip — "
        "see the minimax skill. This engine only supports ref2va: text-only generation is NOT "
        "available, every clip must have a reference image. Write the prompt in English, with "
        "the dialogue and the soundscape.",
        "parameters": {
            "type": "object",
            "properties": {
                "prompt": {
                    "type": "string",
                    "description": "H3-structured video prompt (English): scene, action, "
                    "camera, and the SPOKEN LINES + soundscape the model must generate as audio.",
                },
                "reference_image": {
                    "type": "string",
                    "description": "URL/path of an avatar or reference image "
                    "(publicly reachable by the video server). REQUIRED unless a `character` with an "
                    "image is passed. Sent as an identity reference (ref2va).",
                },
                "seconds": {
                    "type": "integer",
                    "description": "Clip duration in seconds (5-15). Default 5.",
                },
                "aspect_ratio": {
                    "type": "string",
                    "description": "Optional: 9:16 (default), 16:9, 1:1, 4:3, 3:4, 21:9.",
                },
                "num_inference_steps": {
                    "type": "integer",
                    "description": "Optional: sigma grid points (evals = "
                    "steps-1). Default 9 (8 evals) for the ref2v turbo LoRA. Leave as-is.",
                },
                "seed": {
                    "type": "integer",
                    "description": "Optional generation seed. Defaults to a FIXED seed "
                    "shared by every clip so the avatar keeps the SAME face across the whole video. Only "
                    "override it if you deliberately want a different rendering.",
                },
                **_CHARACTER_PROP,
            },
            "required": ["prompt"],
        },
    }
)
def generate_minimax_video(
    session: VideoSession,
    prompt: str,
    reference_image: str = None,
    seconds: int = 5,
    aspect_ratio: str = "9:16",
    num_inference_steps: int = 9,
    seed: int = None,
    character: str = None,
) -> dict:
    """Rend un clip audiovisuel MiniMax-H3 (audio natif) et renvoie le chemin du MP4.
    Ce moteur ne sert QUE ref2va : une image de référence est obligatoire (pas de t2va).
    """
    mc = (session.models or {}).get("video_generator") or {}
    if not (mc.get("provider") or {}).get("base_url"):
        return {
            "status": "error",
            "error": "no video engine configured (set the video_generator "
            "provider on the channel, e.g. MiniMax-H3 at http://localhost:30010)",
        }
    _, char = _resolve_character(session, character)
    ref = reference_image or char.get("portrait_url")
    if not ref:
        return {
            "status": "error",
            "error": "MiniMax-H3 only serves ref2va: a reference image is "
            "REQUIRED. Pass a `character` that has an image, or a `reference_image` URL (e.g. an "
            "avatar generated with generate_minimax_image). Text-only generation is not available.",
        }
    idx = session.clip_no
    session.clip_no += 1
    dest = os.path.join(session.output_dir, f"minimax_{idx + 1}.mp4")
    # Verrou d'identité : préfixe verbatim (identique sur tous les clips) figeant l'apparence.
    full_prompt = _identity_lock_block(char) + prompt
    path = _cap_minimax_video(
        prompt=full_prompt,
        dest=dest,
        model_config=mc,
        seconds=seconds,
        seed=TALKING_SEED if seed is None else int(seed),
        ref_url=ref,
        aspect_ratio=aspect_ratio,
        num_inference_steps=num_inference_steps,
    )
    session.produced_clips.append(
        path
    )  # trace pour la finalisation de secours (cf. run_agent)
    return {
        "status": "ok",
        "video": path,
        "seconds": max(5, min(15, int(seconds or 5))),
        "task": "ref2va",
        "character": character,
    }


@tool(
    {
        "name": "generate_minimax_image",
        "description": "Generate an IMAGE (text-to-image), e.g. to create an AVATAR or a first frame, then "
        "feed it to generate_minimax_video via `reference_image`. Renders immediately and "
        "returns the local path + a public url. Write the prompt in English. (Uses the "
        "channel's image engine — MiniMax-H3 itself does not do standalone text-to-image.) An image "
        "already generated with the SAME prompt is REUSED for free; pass `force_new=true` to regenerate.",
        "parameters": {
            "type": "object",
            "properties": {
                "prompt": {
                    "type": "string",
                    "description": "What the image shows (English, detailed).",
                },
                "aspect_ratio": {
                    "type": "string",
                    "description": "Optional: 9:16 (default), 16:9, 1:1, 4:3, 3:4.",
                },
                "force_new": {
                    "type": "boolean",
                    "description": "Optional. Regenerate even if a saved image exists for this exact "
                    "prompt (default false = reuse the saved one, free).",
                },
            },
            "required": ["prompt"],
        },
    }
)
def generate_minimax_image_tool(
    session: VideoSession, prompt: str, aspect_ratio: str = "9:16",
    force_new: bool = False
) -> dict:
    # RÉUTILISATION (gratuit) : une image générée avec le MÊME prompt existe déjà en bibliothèque.
    key_desc = f"{aspect_ratio}|{prompt}"
    if not force_new:
        hit = library_find("image", None, key_desc)
        if hit:
            return {"status": "ok", "local_path": None, "url": hit["url"], "reused": True,
                    "note": "Reused a SAVED image with the same prompt (no generation — free). "
                            "Pass `url` as `reference_image`. `force_new=true` to regenerate."}
    mc = (session.models or {}).get(
        "image_generator"
    ) or {}  # t2i engine (FLUX/SD3.5); global DeepInfra key if absent
    idx = session.clip_no
    session.clip_no += 1
    dest = os.path.join(session.output_dir, f"minimax_img_{idx + 1}.png")
    _cap_minimax_image(
        prompt=prompt,
        dest=dest,
        model_config=mc,
        aspect_ratio=aspect_ratio,
        seed=SEED_BASE + idx,
    )
    try:
        aid = asset_key("image", None, key_desc)       # nom de fichier unique par prompt
        url = upload_public(
            session.ctx.gcs, dest, f"media/test/minimax_img_{aid}.png"
        )
    except Exception as e:
        return {
            "status": "ok",
            "local_path": dest,
            "url": None,
            "note": f"image generated locally but public upload failed ({e}); usable as a local "
            "reference_image only if the video server can reach this path.",
        }
    library_add("image", key_desc, url, prompt=prompt)  # sauvegarde permanente (réutilisable)
    return {
        "status": "ok",
        "local_path": dest,
        "url": url,
        "reused": False,
        "note": "pass `url` as `reference_image` of generate_minimax_video (also saved to the library).",
    }


@tool(
    {
        "name": "edit_minimax_image",
        "description": "Edit / retouch an existing image (image edit), e.g. to adjust an avatar or produce a "
        "variation. `source` = a local path or URL. Returns the local path + a public url of "
        "the edited image. (Uses the channel's image-edit engine, not MiniMax-H3.)",
        "parameters": {
            "type": "object",
            "properties": {
                "source": {
                    "type": "string",
                    "description": "Local path or URL of the image to edit.",
                },
                "prompt": {
                    "type": "string",
                    "description": "The edit to apply (English).",
                },
            },
            "required": ["source", "prompt"],
        },
    }
)
def edit_minimax_image_tool(session: VideoSession, source: str, prompt: str) -> dict:
    mc = (session.models or {}).get(
        "image_generator"
    ) or {}  # image-edit engine (FLUX Kontext/Qwen); global key if absent
    idx = session.clip_no
    session.clip_no += 1
    local_src = (
        source
        if os.path.exists(source)
        else download(
            source, os.path.join(session.output_dir, f"minimax_edit_src_{idx + 1}")
        )
    )
    dest = os.path.join(session.output_dir, f"minimax_edit_{idx + 1}.png")
    _cap_minimax_edit(
        prompt=prompt,
        image_path=local_src,
        dest=dest,
        model_config=mc,
        seed=SEED_BASE + idx,
    )
    try:
        url = upload_public(
            session.ctx.gcs, dest, f"media/test/minimax_edit_{idx + 1}.png"
        )
    except Exception as e:
        return {
            "status": "ok",
            "local_path": dest,
            "url": None,
            "note": f"public upload failed ({e})",
        }
    return {
        "status": "ok",
        "local_path": dest,
        "url": url,
        "note": "pass `url` as `reference_image` of generate_minimax_video.",
    }


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


@tool(
    {
        "name": "scrape_article",
        "description": "Scrapes the pages listed in the RESOURCES (urls), selects the FIRST "
        "article still UNTREATED for this channel (dedup), marks it as treated and "
        "returns its text to you. Call it FIRST for a news-based video; "
        "then you can adapt this text with `write_script`.",
        "parameters": {"type": "object", "properties": {}},
    }
)
def scrape_article(session: VideoSession) -> dict:
    urls = (session.ressources or {}).get("urls") or []
    if not urls:
        return {
            "status": "error",
            "error": "no url in the resources (context.ressources.urls)",
        }
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
            return {
                "status": "ok",
                "title": article.link.title,
                "text": _article_to_text(article),
            }
    return {"status": "error", "error": "all articles already treated"}


# Longueur max du markdown renvoyé au master (garde le contexte gérable). Réglable via l'env.
_LINKUP_MAX_CHARS = int(os.getenv("LINKUP_MAX_CHARS", "12000"))


def _linkup_api_key() -> str:
    """Clé Linkup : provider `linkup` (renseigné depuis le front, stocké durablement),
    sinon repli sur l'env LINKUP_API_KEY."""
    try:
        from content_creator.config.providers import get_provider

        key = get_provider("linkup").api_key
        if key:
            return key
    except Exception:
        pass
    return os.getenv("LINKUP_API_KEY", "")


@tool(
    {
        "name": "fetch_url",
        "description": "Scrapes ANY web page URL via Linkup and returns its CLEANED content as markdown "
        "(the boilerplate — nav, ads, footers — is stripped). Use it to read a specific "
        "page you know the URL of (an article, a product page, a doc): pass the `url` and "
        "you get back readable text to base a `write_script` / your shots on. "
        "Set `extract_images=true` to also get a list of images found on the page (with their "
        "url + alt text) that you can reuse as a `reference_image`/`source`. "
        "Set `render_js=true` for pages that need JavaScript to render their content.",
        "parameters": {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "The full URL of the page to scrape (e.g. "
                    "'https://example.com/article').",
                },
                "render_js": {
                    "type": "boolean",
                    "description": "Optional: render the page's JavaScript before "
                    "extracting (slower). Use for SPA / dynamic pages that come back empty. Default false.",
                },
                "extract_images": {
                    "type": "boolean",
                    "description": "Optional: also return the images found on "
                    "the page (url + alt text). Default false.",
                },
            },
            "required": ["url"],
        },
    }
)
def fetch_url(
    session: VideoSession,
    url: str,
    render_js: bool = False,
    extract_images: bool = False,
) -> dict:
    """Scrape une page via Linkup et renvoie son markdown nettoyé (+ images optionnelles).
    Clé API : provider `linkup` (renseignable depuis le front), repli env LINKUP_API_KEY.
    """
    api_key = _linkup_api_key()
    if not api_key:
        return {
            "status": "error",
            "error": "no Linkup API key configured (set the `linkup` provider's "
            "api_key from the control panel, or LINKUP_API_KEY in .env)",
        }
    try:
        from linkup import LinkupClient
    except ImportError:
        return {
            "status": "error",
            "error": "linkup-sdk not installed (run `uv add linkup-sdk`)",
        }

    client = LinkupClient(api_key=api_key)
    resp = client.fetch(
        url=url,
        mode="standard",
        include_raw_html=False,
        render_js=render_js,
        extract_images=extract_images,
    )
    markdown = resp.markdown or ""
    result = {
        "status": "ok",
        "url": url,
        "text": markdown[:_LINKUP_MAX_CHARS],
        "length": len(markdown),
        "truncated": len(markdown) > _LINKUP_MAX_CHARS,
    }
    if extract_images and resp.images:
        result["images"] = [{"url": img.url, "alt": img.alt} for img in resp.images]
    return result


# ========================
# TOOLS — recherche d'image web
# ========================
@tool(
    {
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
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Precise search query: exact name of the entity/"
                    "subject, 2-5 words (e.g. 'Jean Dupont mayor Annecy', 'Sony WH-1000XM5 headset', 'Metz station').",
                },
            },
            "required": ["query"],
        },
    }
)
def search_web_image(session: VideoSession, query: str) -> dict:
    idx = len(session.fetched_images)
    local = fetch_web_image(query, session.output_dir, idx=idx)
    if not local:
        return {
            "status": "error",
            "error": f'no usable image found for "{query}". The image fetch '
            "FAILED: change strategy — generate the shot without a reference image (describe the whole "
            "scene in shot_description), rephrase the query, or drop that visual.",
        }
    session.fetched_images.append(local)
    try:
        url = upload_public(session.ctx.gcs, local, f"media/test/web_image_{idx}.jpg")
    except Exception as e:
        # L'image locale existe (utilisable en montage), mais pas d'URL publique pour l'i2v.
        return {
            "status": "ok",
            "query": query,
            "local_path": local,
            "url": None,
            "note": f"image downloaded locally but public upload failed ({e}). Usable via "
            "add_media_clip (`source`={local_path}); no i2v without a URL. Deleted at the end of the video.",
        }
    session.web_images[query] = {"local_path": local, "url": url}
    return {
        "status": "ok",
        "query": query,
        "local_path": local,
        "url": url,
        "note": "image downloaded. Pass `url` as `reference_image` of add_broll_clip (i2v input) "
        "OR as `source` of add_media_clip (illustration shot). Deleted at the end of the video.",
    }


# ========================
# TOOLS — écriture du script
# ========================
@tool(
    {
        "name": "write_script",
        "description": "Writes the narration SCRIPT from the provided article. YOU write `style` "
        "(tone, angle, pacing, intent) BASED ON THE MOOD — you are the one writing the writing "
        "prompt. Call it FIRST; the script is returned to you to split afterward.",
        "parameters": {
            "type": "object",
            "properties": {
                "style": {
                    "type": "string",
                    "description": "Your tone/style/angle instructions for writing the "
                    "script, derived from the mood (e.g. 'dramatic tone, short tense sentences, rising tension').",
                },
            },
            "required": ["style"],
        },
    }
)
def write_script(session: VideoSession, style: str = "") -> dict:
    if session.article is None:
        return {
            "status": "error",
            "error": "no article: the message already contains the script, split it directly",
        }
    script = session.ctx.summarizer.summarize_article(
        session.article, mood=style or None
    )
    if not script:
        return {"status": "error", "error": "script writing failed"}
    session.script = script
    return {"status": "ok", "script": script}


# ========================
# TOOLS — skill de STYLE (spécifique au modèle de génération, ex. MiniMax H3)
# ========================
@tool(
    {
        "name": "load_style_skill",
        "description": "Loads the FULL guide of ONE style skill that YOU chose from the STYLE SKILLS "
        "catalog in your instructions (e.g. 'minimalist-product-ad-generator', '3d-"
        "animation-short-generator'). Use it ONCE, BEFORE planning shots, when the brief "
        "clearly matches a style: the returned guide gives you the visual language, camera "
        "and structure to follow when writing your video prompts. Only available when the "
        "generation model ships style skills.",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Exact `name` of the style skill from the catalog.",
                },
            },
            "required": ["name"],
        },
    }
)
def load_style_skill(session: VideoSession, name: str) -> dict:
    from content_creator.agentic.model_prompting import load_style_skill as _load_style

    video_model = (session.models or {}).get("video_generator")
    try:
        guide = _load_style(video_model, name)
    except KeyError as e:
        return {"status": "error", "error": str(e)}
    return {
        "status": "ok",
        "skill": name,
        "guide": guide,
        "note": "Follow this as STYLE/prompting guidance; keep orchestrating with your own tools "
        "(add_talking_clip/add_broll_clip/add_media_clip/assemble_video).",
    }


# ========================
# TOOLS — décor / rendu / finition
# ========================
@tool(
    {
        "name": "set_scene_background",
        "description": "Places a CHARACTER in a coherent BACKGROUND (FLUX Kontext), preserving their "
        "identity. Updates the character's portrait: their next shots (facing camera / "
        "b-roll) will use this background. Call it BEFORE planning the character's shots. If the SAME "
        "character + background was already generated in a PAST run, it is REUSED for free; pass "
        "`force_new=true` to force a fresh one.",
        "parameters": {
            "type": "object",
            "properties": {
                "character": {
                    "type": "string",
                    "description": "Character name (must have an image).",
                },
                "description": {
                    "type": "string",
                    "description": "The BACKGROUND/ambience only, inferred from "
                    "the context (e.g. 'football stadium at sunset', 'clean TV studio'). "
                    "Do NOT describe the person.",
                },
                "force_new": {
                    "type": "boolean",
                    "description": "Optional. Regenerate even if a saved background exists for this "
                    "character + description (default false = reuse the saved one, free).",
                },
            },
            "required": ["character", "description"],
        },
    }
)
def set_scene_background(
    session: VideoSession, character: str, description: str, force_new: bool = False
) -> dict:
    _, char = _resolve_character(session, character)
    # RÉUTILISATION (gratuit) : même personnage + même décor déjà en bibliothèque -> on le reprend.
    if not force_new:
        hit = library_find("background", character, description)
        if hit:
            session.characters[character]["portrait_url"] = hit["url"]
            return {"status": "ok", "character": character, "scene": description,
                    "reused": True, "reference": hit["url"],
                    "note": "Reused a SAVED background from the library (no generation — free). "
                            "Pass `force_new=true` to regenerate."}
    local = char.get("local_image")
    if not local:
        return {
            "status": "error",
            "error": f"character '{character}' unknown or without an image: "
            "the background (FLUX Kontext) applies to a character's portrait.",
        }
    out = os.path.join(session.output_dir, f"scene_{character}.jpg")
    prompt = BACKGROUND_TEMPLATE.format(
        scene=description
    )  # identité préservée + décor inféré
    # URL publique de la source : requise par les modèles d'édition "inference" (Wan) que DashScope
    # télécharge. On réutilise le portrait_url déjà uploadé, sinon on uploade le fichier local.
    src_url = char.get("portrait_url")
    if not src_url:
        src_url = upload_public(
            session.ctx.gcs, local, f"media/test/char_{character}_src.png"
        )
    # Provider/endpoint issu du rôle image_generator du channel (sinon repli global dans capabilities).
    scene = prepare_scene_portrait(
        regen=True,
        src=local,
        prompt=prompt,
        out=out,
        model_config=(session.models or {}).get("image_generator"),
        src_url=src_url,
    )
    aid = asset_key("background", character, description)   # nom unique par (perso, décor)
    url = upload_public(session.ctx.gcs, scene, f"media/test/scene_{character}_{aid}.jpg")
    # SAUVEGARDE PERMANENTE -> réutilisable gratuitement dans les prochains runs.
    library_add("background", description, url, prompt=prompt, character=character)
    # Met à jour le portrait du personnage -> ses prochains plans utiliseront ce décor.
    session.characters[character]["portrait_url"] = url
    return {
        "status": "ok",
        "character": character,
        "scene": description,
        "reused": False,
        "note": "background generated, SAVED to the library (reusable next runs) and applied to the "
        "character; their next shots will use it",
    }


@tool(
    {
        "name": "list_saved_backgrounds",
        "description": "List the SAVED backgrounds / establishing frames from the persistent asset "
        "library (kept across runs). Call it BEFORE generating a background/scene: if a suitable one "
        "already exists, REUSE it with `use_saved_background` instead of paying for a new generation. "
        "Returns entries with their `id`, `kind`, `character` and `description`.",
        "parameters": {
            "type": "object",
            "properties": {
                "character": {
                    "type": "string",
                    "description": "Optional: only list assets saved for this character.",
                },
            },
        },
    }
)
def list_saved_backgrounds(session: VideoSession, character: str = None) -> dict:
    items = [a for a in library_list(character=character)
             if a.get("kind") in ("background", "establish")]
    return {"status": "ok", "count": len(items),
            "assets": [{"id": a["id"], "kind": a["kind"], "character": a.get("character"),
                        "description": a.get("description")} for a in items],
            "note": "Reuse one with use_saved_background(background_id=<id>, character=<name>) — free."}


@tool(
    {
        "name": "use_saved_background",
        "description": "REUSE a saved background / establishing frame from the library (no generation — "
        "free) and PIN it as the given character's reference for the run. Get an `id` from "
        "`list_saved_backgrounds`. Their next shots will use this image.",
        "parameters": {
            "type": "object",
            "properties": {
                "background_id": {
                    "type": "string",
                    "description": "The `id` of the saved asset (from list_saved_backgrounds).",
                },
                "character": {
                    "type": "string",
                    "description": "Character to pin this background/reference onto for the run.",
                },
            },
            "required": ["background_id", "character"],
        },
    }
)
def use_saved_background(session: VideoSession, background_id: str, character: str) -> dict:
    entry = library_get(background_id)
    if not entry:
        return {"status": "error", "error": f"no saved asset with id '{background_id}' "
                "(use list_saved_backgrounds to get valid ids)."}
    if character not in (session.characters or {}):
        return {"status": "error", "error": f"unknown character '{character}'."}
    session.characters[character]["portrait_url"] = entry["url"]
    try:
        out = os.path.join(session.output_dir, f"reused_{character}.jpg")
        session.characters[character]["local_image"] = download(entry["url"], out)
    except Exception:
        pass
    return {"status": "ok", "character": character, "reference": entry["url"],
            "description": entry.get("description"),
            "note": "Saved asset reused and pinned as the character reference (no generation). Plan shots now."}


@tool(
    {
        "name": "establish_avatar_scene",
        "description": "Generate ONCE a canonical ESTABLISHING frame of the character — same face, dressed "
        "in SIMPLE reproducible clothes, in a coherent location — and PIN it as the reference for "
        "the WHOLE run. Every following `generate_minimax_video` then "
        "reuses this exact frame, so the model no longer re-invents hair, wardrobe, worn gear or "
        "setting from clip to clip (this is the strongest fix for a drifting avatar). Call it ONCE "
        "at the very START, right after picking the avatar and before planning any shot. The clothes "
        "are forced to be plain/solid-color/logo-free so the AI reproduces them reliably. If the SAME "
        "character + scene was already established in a PAST run, it is REUSED for free (no generation); "
        "pass `force_new=true` to force a fresh one.",
        "parameters": {
            "type": "object",
            "properties": {
                "character": {
                    "type": "string",
                    "description": "Character name (must have an image).",
                },
                "scene": {
                    "type": "string",
                    "description": "The LOCATION/setting only, inferred from the context "
                    "(e.g. 'a clean modern podcast studio, neutral wall', 'a tech workshop, soft light'). "
                    "Keep it simple and uncluttered. Do NOT describe the clothes here (kept simple automatically).",
                },
                "force_new": {
                    "type": "boolean",
                    "description": "Optional. Generate a fresh frame even if a saved one exists for this "
                    "character + scene (default false = reuse the saved one when available, free).",
                },
            },
            "required": ["character", "scene"],
        },
    }
)
def establish_avatar_scene(session: VideoSession, character: str, scene: str,
                           force_new: bool = False) -> dict:
    _, char = _resolve_character(session, character)
    out = os.path.join(session.output_dir, f"establish_{character}.jpg")
    # RÉUTILISATION (gratuit) : un établissement identique (même personnage + même lieu) déjà en
    # bibliothèque -> on le reprend au lieu de régénérer. `force_new=True` pour forcer une nouvelle.
    if not force_new:
        hit = library_find("establish", character, scene)
        if hit:
            session.characters[character]["portrait_url"] = hit["url"]
            try:
                session.characters[character]["local_image"] = download(hit["url"], out)
            except Exception:
                pass
            return {"status": "ok", "character": character, "scene": scene,
                    "reference": hit["url"], "reused": True,
                    "note": "Reused a SAVED establishing frame from the library (no generation — free). "
                            "PINNED as the run reference. Pass `force_new=true` to generate a fresh one."}
    local = char.get("local_image")
    if not local:
        return {
            "status": "error",
            "error": f"character '{character}' unknown or without an image.",
        }
    look = (
        char.get("appearance") or char.get("description") or "the same person"
    ).strip()
    prompt = ESTABLISH_TEMPLATE.format(look=look, scene=scene)
    if len(prompt) > 2000:                              # limite prompt de l'éditeur (Wan: 2100) :
        over = len(prompt) - 2000                       # on tronque l'apparence, pas la scène
        look = look[: max(0, len(look) - over - 1)].rstrip() + "…"
        prompt = ESTABLISH_TEMPLATE.format(look=look, scene=scene)
    src_url = char.get("portrait_url") or upload_public(
        session.ctx.gcs, local, f"media/test/char_{character}_src.png"
    )
    frame = prepare_scene_portrait(
        regen=True,
        src=local,
        prompt=prompt,
        out=out,
        model_config=(session.models or {}).get("image_generator"),
        src_url=src_url,
    )
    # L'éditeur suit souvent le ratio de la source : on force le 9:16 (sujet centré -> crop sûr).
    frame = crop_to_vertical(frame, out)
    aid = asset_key("establish", character, scene)     # nom de fichier unique par (perso, scène)
    url = upload_public(session.ctx.gcs, frame, f"media/test/establish_{character}_{aid}.jpg")
    # SAUVEGARDE PERMANENTE dans la bibliothèque -> réutilisable dans les prochains runs (gratuit).
    library_add("establish", scene, url, prompt=prompt, character=character)
    # PIN pour tout le run : le portrait ET la source locale deviennent cette frame d'établissement,
    # que tous les plans (H3 / b-roll) réutiliseront comme référence d'identité.
    session.characters[character]["portrait_url"] = url
    session.characters[character]["local_image"] = frame
    return {
        "status": "ok",
        "character": character,
        "scene": scene,
        "reference": url,
        "reused": False,
        "note": "Establishing frame generated, SAVED to the library (reusable in future runs) and "
        "PINNED as the run's reference. All next shots reuse it — same face, simple clothes, same "
        "setting. Plan shots now.",
    }


@tool(
    {
        "name": "assemble_video",
        "description": "RENDERS all planned shots IN PARALLEL (in order) then assembles them into "
        "a final video. Call it once ALL shots are planned.",
        "parameters": {"type": "object", "properties": {}},
    }
)
def assemble_video(session: VideoSession) -> dict:
    if not session.plan:
        return {
            "status": "error",
            "error": "no shot planned (use add_talking_clip/add_broll_clip)",
        }
    results = render_plan(session)
    if not session.clips:
        return {
            "status": "error",
            "error": "no shot rendered successfully",
            "plans": results,
        }
    out = os.path.join(session.output_dir, "final_story.mp4")
    concat_clips(session.clips, out)
    session.final_video = out
    return {
        "status": "ok",
        "final_video": out,
        "n_clips": len(session.clips),
        "plans": results,
    }


def _plan_transcript(session: VideoSession) -> str:
    """Transcript EXACT de la narration = concaténation des textes des plans DANS L'ORDRE (c'est
    l'input TTS, donc l'audio final le dit mot pour mot). Sert de vérité pour l'alignement au mot.
    """
    parts = []
    for spec in sorted(session.plan, key=lambda s: s["idx"]):
        t = spec.get("text") or spec.get("narration_text")
        if t and t.strip():
            parts.append(t.strip())
    return " ".join(parts).strip()


def _burn_captions(session: VideoSession, words: list) -> str:
    """Incruste des sous-titres depuis des mots alignés [{text,start,end}] sur `session.final_video`.
    Style par env `SUBTITLE_STYLE` : `karaoke` (défaut) = mot EN COURS colorié (.ass/libass),
    `plain` = légendes blanches simples (.srt). Couleurs karaoké : `SUBTITLE_COLOR` (base, défaut
    FFFFFF) et `SUBTITLE_HIGHLIGHT` (mot actif, défaut F5E003). Retourne le chemin de la vidéo finale.
    """
    d = session.output_dir
    out = os.path.join(d, "final_subtitled.mp4")
    style = os.getenv("SUBTITLE_STYLE", "karaoke").strip().lower()
    if style in ("karaoke", "word", "highlight", "ass"):
        w, h = _probe_size(session.final_video)
        ass = os.path.join(d, "subs.ass")
        words_to_ass(
            words,
            ass,
            video_w=w,
            video_h=h,
            base_color=os.getenv("SUBTITLE_COLOR", "FFFFFF"),
            highlight_color=os.getenv("SUBTITLE_HIGHLIGHT", "F5E003"),
        )
        return burn_ass(session.final_video, ass, out)
    srt = os.path.join(d, "subs.srt")
    words_to_srt(words, srt)
    return burn_subtitles(session.final_video, srt, out)


def _subtitles_elevenlabs(
    session: VideoSession, transcript: str, api_key: str, base_url: str = None
) -> str:
    """Extrait l'audio du montage -> aligne le transcript connu (ElevenLabs) -> incruste (karaoké/plain)."""
    audio = os.path.join(session.output_dir, "subs_audio.wav")
    # Audio mono 16 kHz : format léger et suffisant pour l'alignement.
    sh(
        [
            "ffmpeg",
            "-y",
            "-i",
            session.final_video,
            "-vn",
            "-ac",
            "1",
            "-ar",
            "16000",
            audio,
        ]
    )
    words = elevenlabs_forced_alignment(audio, transcript, api_key, base_url)
    if not words:
        raise RuntimeError("aucun mot aligné")
    return _burn_captions(session, words)


# Modèle faster-whisper chargé UNE SEULE FOIS (coûteux) et réutilisé par tous les runs du process.
_WHISPER_MODEL = None


def _get_whisper_model():
    """Charge (à la 1re demande, puis met en cache) le modèle faster-whisper LOCAL — aucune API.
    Réglable par env : WHISPER_MODEL (défaut `large-v3`, meilleure qualité FR ; `large-v3-turbo`
    plus rapide), WHISPER_DEVICE (défaut `cpu`), WHISPER_COMPUTE (défaut `int8` — rapide/léger CPU).
    """
    global _WHISPER_MODEL
    if _WHISPER_MODEL is None:
        from faster_whisper import WhisperModel

        _WHISPER_MODEL = WhisperModel(
            os.getenv("WHISPER_MODEL", "large-v3"),
            device=os.getenv("WHISPER_DEVICE", "cpu"),
            compute_type=os.getenv("WHISPER_COMPUTE", "int8"),
        )
    return _WHISPER_MODEL


def _subtitles_whisper(session: VideoSession) -> str:
    """Sous-titres 100% LOCAUX (faster-whisper, aucune API) : extrait l'audio du montage, transcrit
    avec timestamps AU MOT, puis SRT court + incrustation ffmpeg. Transcrit l'audio RÉEL — robuste
    même quand le transcript exact est inconnu (audio natif MiniMax-H3). `WHISPER_LANG` force la
    langue (ex. `fr`), sinon auto-détection. `vad_filter` coupe les silences pour éviter les hallus.
    """
    audio = os.path.join(session.output_dir, "subs_audio.wav")
    # Audio mono 16 kHz : format attendu par Whisper, léger.
    sh(
        [
            "ffmpeg",
            "-y",
            "-i",
            session.final_video,
            "-vn",
            "-ac",
            "1",
            "-ar",
            "16000",
            audio,
        ]
    )
    segments, _info = _get_whisper_model().transcribe(
        audio,
        language=os.getenv("WHISPER_LANG") or None,
        word_timestamps=True,
        vad_filter=True,
    )
    words = [
        {"text": w.word.strip(), "start": w.start, "end": w.end}
        for seg in segments
        for w in (seg.words or [])
        if w.word.strip()
    ]
    if not words:
        raise RuntimeError("faster-whisper: aucun mot transcrit")
    return _burn_captions(session, words)


@tool(
    {
        "name": "add_subtitles",
        "description": "Burns word-synced subtitles onto the final video. Call it AFTER assemble_video.",
        "parameters": {"type": "object", "properties": {}},
    }
)
def add_subtitles(session: VideoSession) -> dict:
    if not session.final_video:
        return {"status": "error", "error": "call assemble_video first"}
    # 1) LOCAL par défaut : faster-whisper (AUCUNE API). Transcrit l'audio réel -> timestamps au mot.
    # Marche pour le TTS comme pour l'audio natif MiniMax-H3. Réglable par env WHISPER_* .
    try:
        out = _subtitles_whisper(session)
        session.final_video = out
        session.subtitled = True
        return {"status": "ok", "final_video": out, "engine": "faster-whisper"}
    except Exception as e:
        print(
            f"[subtitles] faster-whisper KO ({e}) — repli ElevenLabs/Creatomate",
            flush=True,
        )

    # 2) Repli : ElevenLabs Forced Alignment (API) — le transcript est l'input TTS EXACT -> calage
    # au mot parfait. Clé = provider voice_generator du channel, sinon .env.
    transcript = _plan_transcript(session)
    voice_prov = (session.voice or {}).get("provider") or {}
    api_key = voice_prov.get("token") or os.getenv("ELEVENLABS_API_KEY")
    if transcript and api_key:
        try:
            out = _subtitles_elevenlabs(
                session, transcript, api_key, voice_prov.get("base_url")
            )
            session.final_video = out
            session.subtitled = True
            return {"status": "ok", "final_video": out, "engine": "elevenlabs"}
        except Exception as e:
            print(f"[subtitles] ElevenLabs KO ({e}) — repli Creatomate", flush=True)

    # 3) Repli : Creatomate (auto-transcription) — inchangé.
    url = upload_public(
        session.ctx.gcs, session.final_video, "media/test/final_for_subs.mp4"
    )
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
    session.subtitled = True
    return {"status": "ok", "final_video": out, "engine": "creatomate"}


@tool(
    {
        "name": "add_background_music",
        "description": "Adds a low-volume music bed under the narration of the final video. "
        "Call it AFTER assemble_video.",
        "parameters": {
            "type": "object",
            "properties": {
                "source": {
                    "type": "string",
                    "description": "Local path or URL of a music audio file.",
                },
                "volume": {
                    "type": "number",
                    "description": "Music volume 0-1 (default 0.15).",
                },
            },
            "required": ["source"],
        },
    }
)
def add_background_music(
    session: VideoSession, source: str, volume: float = 0.15
) -> dict:
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
    en fin de vidéo. Les copies GCS sont conservées. Retourne le nombre de fichiers supprimés.
    """
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

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
    sh, download, upload_public,
    synthesize_audio, generate_lipsync, generate_broll,
    reframe_vertical, concat_clips, prepare_scene_portrait,
)
from content_creator.pipelines.modules import VideoGenerator

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
        return {"status": "error", "error": f"tool inconnu: {name}"}
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
    avatar_local: str                            # copie locale de l'avatar (set_scene_background)
    output_dir: str = OUTPUT_DIR
    article: object = None                        # FullArticle source (pour write_script)
    script: str = None                            # script écrit par le master (write_script)
    plan: list = field(default_factory=list)     # specs planifiés, dans l'ordre
    clips: list = field(default_factory=list)    # plans rendus (rempli par render_plan)
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
    try:
        if spec["kind"] == "talking":
            _, dur = synthesize_audio(session.ctx.summarizer, spec["text"], narration)
            audio_url = upload_public(session.ctx.gcs, narration, f"media/test/narration_{idx+1}.mp3")
            generate_lipsync(spec["portrait_url"], audio_url, spec["video_prompt"], spec["seed"], raw)
            reframe_vertical(raw, final)                      # audio narration déjà dans la vidéo
        else:  # broll
            _, dur = synthesize_audio(session.ctx.summarizer, spec["narration_text"], narration)
            duration = max(2, min(15, int(round(dur + 0.8))))
            generate_broll(spec["shot"], duration, spec["seed"], spec["media"], raw)
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
@tool({
    "name": "add_talking_clip",
    "description": "PLANIFIE un plan FACE CAMÉRA: l'avatar dit `text`, lèvres synchronisées (lip-sync). "
                   "Instantané (le rendu se fait à assemble_video). À utiliser pour l'accroche, "
                   "les phrases clés et la conclusion.",
    "parameters": {"type": "object", "properties": {
        "text": {"type": "string", "description": "Texte exact que l'avatar prononce (un segment/phrase)."},
        "expression": {"type": "string", "description": "Optionnel: ton/expression (ex. 'sourire chaleureux')."},
    }, "required": ["text"]},
})
def add_talking_clip(session: VideoSession, text: str, expression: str = None) -> dict:
    idx = session.clip_no
    session.clip_no += 1
    video_prompt = " ".join(p for p in [expression, PRUNA_MOVEMENT] if p)
    session.plan.append({
        "kind": "talking", "idx": idx, "text": text, "video_prompt": video_prompt,
        "portrait_url": session.ctx.portrait_url, "seed": SEED_BASE + idx,
    })
    return {"status": "ok", "queued": "talking", "slot": idx + 1, "text": text[:60]}


@tool({
    "name": "add_broll_clip",
    "description": "PLANIFIE un plan B-ROLL cinématographique (avatar de profil/marche/ambiance) avec "
                   "la narration en voix off. Instantané (rendu à assemble_video). Pour les phrases "
                   "descriptives/d'ambiance.",
    "parameters": {"type": "object", "properties": {
        "narration_text": {"type": "string", "description": "Texte de la voix off pour ce plan."},
        "shot_description": {"type": "string", "description": "Prompt vidéo pour le moteur (LTX), "
                             "rédigé selon la COMPÉTENCE de prompting : un seul plan continu, chronologique, "
                             "au présent, cadrage + lumière + action + caméra, en anglais, qui reflète le mood."},
    }, "required": ["narration_text", "shot_description"]},
})
def add_broll_clip(session: VideoSession, narration_text: str, shot_description: str) -> dict:
    idx = session.clip_no
    session.clip_no += 1
    session.plan.append({
        "kind": "broll", "idx": idx, "narration_text": narration_text,
        "shot": shot_description, "media": session.ctx.media, "seed": SEED_BASE + idx,
    })
    return {"status": "ok", "queued": "broll", "slot": idx + 1}


# ========================
# TOOLS — écriture du script
# ========================
@tool({
    "name": "write_script",
    "description": "Écris le SCRIPT de narration à partir de l'article fourni. TU rédiges `style` "
                   "(ton, angle, rythme, intention) EN FONCTION DU MOOD — c'est toi qui écris le prompt "
                   "d'écriture. Appelle-le EN PREMIER ; le script t'est retourné pour le découper ensuite.",
    "parameters": {"type": "object", "properties": {
        "style": {"type": "string", "description": "Tes instructions de ton/style/angle pour écrire le "
                  "script, dérivées du mood (ex. 'ton dramatique, phrases courtes et tendues, montée en tension')."},
    }, "required": ["style"]},
})
def write_script(session: VideoSession, style: str = "") -> dict:
    if session.article is None:
        return {"status": "error",
                "error": "aucun article : le message contient déjà le script, découpe-le directement"}
    script = session.ctx.summarizer.summarize_article(session.article, mood=style or None)
    if not script:
        return {"status": "error", "error": "échec écriture du script"}
    session.script = script
    return {"status": "ok", "script": script}


# ========================
# TOOLS — décor / rendu / finition
# ========================
@tool({
    "name": "set_scene_background",
    "description": "Place l'avatar dans un DÉCOR cohérent (FLUX Kontext). Tu INFÈRES le décor "
                   "à partir du sujet du script et du mood. L'identité de l'avatar est préservée "
                   "automatiquement. À appeler EN PREMIER, avant de planifier les plans.",
    "parameters": {"type": "object", "properties": {
        "description": {"type": "string", "description": "Le DÉCOR/l'ambiance uniquement, inféré du "
                        "contexte (ex. 'stade de football au coucher du soleil, ambiance chaleureuse', "
                        "'studio TV moderne épuré'). Ne décris PAS la personne."},
    }, "required": ["description"]},
})
def set_scene_background(session: VideoSession, description: str) -> dict:
    out = os.path.join(session.output_dir, "scene_portrait.jpg")
    prompt = BACKGROUND_TEMPLATE.format(scene=description)   # identité préservée + décor inféré
    scene = prepare_scene_portrait(regen=True, src=session.avatar_local, prompt=prompt, out=out)
    url = upload_public(session.ctx.gcs, scene, "media/test/scene_portrait.jpg")
    session.ctx.portrait_url = url
    session.ctx.media = [{"type": "reference_image", "url": url}]
    return {"status": "ok", "scene": description,
            "note": "décor appliqué; les prochains plans planifiés l'utiliseront"}


@tool({
    "name": "assemble_video",
    "description": "REND tous les plans planifiés EN PARALLÈLE (dans l'ordre) puis les assemble en "
                   "une vidéo finale. À appeler une fois TOUS les plans planifiés.",
    "parameters": {"type": "object", "properties": {}},
})
def assemble_video(session: VideoSession) -> dict:
    if not session.plan:
        return {"status": "error", "error": "aucun plan planifié (utilise add_talking_clip/add_broll_clip)"}
    results = render_plan(session)
    if not session.clips:
        return {"status": "error", "error": "aucun plan rendu avec succès", "plans": results}
    out = os.path.join(session.output_dir, "final_story.mp4")
    concat_clips(session.clips, out)
    session.final_video = out
    return {"status": "ok", "final_video": out, "n_clips": len(session.clips), "plans": results}


@tool({
    "name": "add_subtitles",
    "description": "Incruste des sous-titres animés sur la vidéo finale (Creatomate). À appeler APRÈS assemble_video.",
    "parameters": {"type": "object", "properties": {}},
})
def add_subtitles(session: VideoSession) -> dict:
    if not session.final_video:
        return {"status": "error", "error": "appelle assemble_video d'abord"}
    url = upload_public(session.ctx.gcs, session.final_video, "media/test/final_for_subs.mp4")
    vg = VideoGenerator()
    resp = vg.add_subtitles(url)
    if not resp:
        return {"status": "error", "error": "add_subtitles a échoué"}
    final = vg.wait_for_render(resp.id, max_wait=120, poll_interval=3)
    if not final:
        return {"status": "error", "error": "render sous-titres timeout"}
    out = os.path.join(session.output_dir, "final_subtitled.mp4")
    download(str(final.url), out)
    session.final_video = out
    return {"status": "ok", "final_video": out}


@tool({
    "name": "add_background_music",
    "description": "Ajoute un lit musical à bas volume sous la narration de la vidéo finale. "
                   "À appeler APRÈS assemble_video.",
    "parameters": {"type": "object", "properties": {
        "source": {"type": "string", "description": "Chemin local ou URL d'un fichier audio musical."},
        "volume": {"type": "number", "description": "Volume de la musique 0-1 (défaut 0.15)."},
    }, "required": ["source"]},
})
def add_background_music(session: VideoSession, source: str, volume: float = 0.15) -> dict:
    if not session.final_video:
        return {"status": "error", "error": "appelle assemble_video d'abord"}
    out = os.path.join(session.output_dir, "final_music.mp4")
    mix_music(session.final_video, source, out, volume)
    session.final_video = out
    return {"status": "ok", "final_video": out}

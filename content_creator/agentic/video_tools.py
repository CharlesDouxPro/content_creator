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
    "duration_s": {"type": "number", "description": "Optionnel (LTX): durée cible du plan en secondes "
                   "(arrondie au format 8k+1). Défaut: longueur de la narration. Garde 2–10 s."},
    "width": {"type": "integer", "description": "Optionnel (LTX): largeur px (multiple de 64, arrondi serveur). "
              "Défaut: format 9:16 du .env. Ne change que pour un besoin précis (cohérence du concat)."},
    "height": {"type": "integer", "description": "Optionnel (LTX): hauteur px (multiple de 64). Défaut: 9:16 du .env."},
    "frame_rate": {"type": "number", "description": "Optionnel (LTX): images/s. Défaut: .env (24)."},
    "num_inference_steps": {"type": "integer", "description": "Optionnel (LTX): nb d'étapes de denoising "
                            "(plus haut = un peu mieux, plus lent). Défaut serveur: 30."},
    "image_strength": {"type": "number", "description": "Optionnel (LTX i2v): adhérence à l'image de réf "
                       "0–1 (1=colle fort, 0.7–0.85=plus de liberté de mouvement)."},
    "hdr": {"type": "boolean", "description": "Optionnel (LTX): passe HDR de raffinement (≈2× plus lent). "
            "Réserve aux plans CLÉS."},
}

# Clés de _LTX_PARAM_PROPS = les noms d'args LTX à extraire des kwargs d'un tool.
_LTX_PARAM_KEYS = tuple(_LTX_PARAM_PROPS.keys())


def _collect_ltx_params(kwargs: dict) -> dict:
    """Extrait les params LTX fournis (non None) d'un appel de tool -> dict propre."""
    return {k: kwargs[k] for k in _LTX_PARAM_KEYS if kwargs.get(k) is not None}


# Propriété `character` partagée par les tools de planification.
_CHARACTER_PROP = {
    "character": {"type": "string", "description": "Optionnel: nom d'un personnage défini pour ce "
                  "channel. Sa VOIX, son APPARENCE (portrait) et sa DESCRIPTION sont alors appliquées. "
                  "Sans valeur: voix par défaut du channel."},
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
    "description": "PLANIFIE un plan FACE CAMÉRA: l'avatar dit `text`, lèvres synchronisées (lip-sync). "
                   "Instantané (le rendu se fait à assemble_video). À utiliser pour l'accroche, "
                   "les phrases clés et la conclusion.",
    "parameters": {"type": "object", "properties": {
        "text": {"type": "string", "description": "Texte exact que l'avatar prononce (un segment/phrase)."},
        "expression": {"type": "string", "description": "Optionnel: ton/expression (ex. 'sourire chaleureux')."},
        **_CHARACTER_PROP,
        **_LTX_PARAM_PROPS,
    }, "required": ["text"]},
})
def add_talking_clip(session: VideoSession, text: str, expression: str = None,
                     character: str = None, **kwargs) -> dict:
    voice, char = _resolve_character(session, character)
    portrait, description = char.get("portrait_url"), char.get("description")
    if not portrait:
        return {"status": "error", "error": "plan face caméra (lip-sync) impossible : passe un "
                "`character` qui possède une image. Sinon utilise add_broll_clip ou add_media_clip."}
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
    "description": "PLANIFIE un plan B-ROLL cinématographique (avatar de profil/marche/ambiance) avec "
                   "la narration en voix off. Instantané (rendu à assemble_video). Pour les phrases "
                   "descriptives/d'ambiance.",
    "parameters": {"type": "object", "properties": {
        "narration_text": {"type": "string", "description": "Texte de la voix off pour ce plan."},
        "shot_description": {"type": "string", "description": "Prompt vidéo pour le moteur (LTX), "
                             "rédigé selon la COMPÉTENCE de prompting : un seul plan continu, chronologique, "
                             "au présent, cadrage + lumière + action + caméra, en anglais, qui reflète le mood."},
        "reference_image": {"type": "string", "description": "Optionnel: URL d'une image de référence "
                            "à animer en INPUT i2v de CE plan (à la place du décor courant). Typiquement "
                            "l'`url` retournée par `search_web_image` pour une entité réelle sans image fournie. "
                            "Le moteur partira de cette image."},
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
    "description": "PLANIFIE un plan à partir d'un CLIP VIDÉO ou d'une IMAGE FOURNI (montage). `source` = "
                   "un chemin local OU une URL issus des ressources OU une image récupérée par "
                   "`search_web_image`. Le média est normalisé au MÊME format que les autres plans de la "
                   "vidéo (cohérence garantie) : une vidéo est recadrée, une IMAGE devient un plan fixe "
                   "(sur la voix off si fournie, sinon `image_duration_s`). Instantané (rendu à "
                   "assemble_video). Outil clé pour le montage et pour ILLUSTRER une entité avec une image web.",
    "parameters": {"type": "object", "properties": {
        "source": {"type": "string", "description": "Chemin local OU URL du clip vidéo / de l'image à "
                   "intégrer (ressources disponibles ou résultat de search_web_image)."},
        "narration_text": {"type": "string", "description": "Optionnel: voix off TTS qui REMPLACE "
                           "l'audio du clip (ou commente l'image). Laisser vide pour garder l'audio d'origine."},
        "image_duration_s": {"type": "number", "description": "Optionnel (IMAGE muette uniquement): durée "
                             "du plan fixe en secondes. Défaut 4. Ignoré pour une vidéo ou si narration_text."},
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
    "description": "Scrape les pages listées dans les RESSOURCES (urls), sélectionne le PREMIER "
                   "article encore NON traité pour ce channel (dédup), le marque comme traité et te "
                   "retourne son texte. À appeler EN PREMIER pour une vidéo basée sur l'actualité ; "
                   "ensuite tu peux adapter ce texte avec `write_script`.",
    "parameters": {"type": "object", "properties": {}},
})
def scrape_article(session: VideoSession) -> dict:
    urls = (session.ressources or {}).get("urls") or []
    if not urls:
        return {"status": "error", "error": "aucune url dans les ressources (context.ressources.urls)"}
    scraper = NewsScraper()
    articles = []
    for url in urls:
        links = scraper.scrape_links_older_than_24h(url)
        for link in {ln.href: ln for ln in links}.values():
            blocks = scraper.scrape_article(link.href)
            if blocks:
                articles.append(FullArticle(link=link, content=blocks))
    if not articles:
        return {"status": "error", "error": "aucun article scrapé"}
    for article in articles:
        if not is_processed(session.name, article.link.href):
            session.article = article
            mark_processed(session.name, article.link.href)
            return {"status": "ok", "title": article.link.title,
                    "text": _article_to_text(article)}
    return {"status": "error", "error": "tous les articles déjà traités"}


# ========================
# TOOLS — recherche d'image web
# ========================
@tool({
    "name": "search_web_image",
    "description": "Cherche et télécharge une IMAGE sur le web (Google Images) pour une entité/un sujet "
                   "RÉEL et NON-PUBLIC/non-fictif que le moteur ne saura pas dessiner de façon fiable et pour "
                   "lequel AUCUNE image n'est fournie dans les ressources : ex. une personne peu connue, un "
                   "produit/logo précis, un lieu spécifique, un événement local. Inutile pour une célébrité, "
                   "une marque ultra-connue ou un sujet fictif/générique (le moteur les gère seul). "
                   "EN CAS DE SUCCÈS, le tool retourne `url` (+ `local_path`) : réutilise `url` comme "
                   "`reference_image` d'un `add_broll_clip` (input i2v, le moteur anime l'image) OU comme "
                   "`source` d'un `add_media_clip` (plan d'illustration fixe au montage). "
                   "EN CAS D'ÉCHEC (status=error), AUCUNE image n'a pu être récupérée : CHANGE de stratégie — "
                   "génère le plan sans image de référence en décrivant toute la scène dans `shot_description`, "
                   "ou reformule la requête une fois, ou abandonne ce visuel.",
    "parameters": {"type": "object", "properties": {
        "query": {"type": "string", "description": "Requête de recherche précise : nom exact de l'entité/du "
                  "sujet, 2-5 mots (ex. 'Jean Dupont maire Annecy', 'casque Sony WH-1000XM5', 'gare de Metz')."},
    }, "required": ["query"]},
})
def search_web_image(session: VideoSession, query: str) -> dict:
    idx = len(session.fetched_images)
    local = fetch_web_image(query, session.output_dir, idx=idx)
    if not local:
        return {"status": "error",
                "error": f"aucune image exploitable trouvée pour « {query} ». La récupération d'image a "
                         "ÉCHOUÉ : change de stratégie — génère le plan sans image de référence (décris toute "
                         "la scène dans shot_description), reformule la requête, ou laisse tomber ce visuel."}
    session.fetched_images.append(local)
    try:
        url = upload_public(session.ctx.gcs, local, f"media/test/web_image_{idx}.jpg")
    except Exception as e:
        # L'image locale existe (utilisable en montage), mais pas d'URL publique pour l'i2v.
        return {"status": "ok", "query": query, "local_path": local, "url": None,
                "note": f"image téléchargée localement mais upload public échoué ({e}). Utilisable via "
                        "add_media_clip (`source`={local_path}); pas d'i2v sans URL. Supprimée en fin de vidéo."}
    session.web_images[query] = {"local_path": local, "url": url}
    return {"status": "ok", "query": query, "local_path": local, "url": url,
            "note": "image téléchargée. Passe `url` en `reference_image` d'add_broll_clip (input i2v) "
                    "OU en `source` d'add_media_clip (plan d'illustration). Supprimée en fin de vidéo."}


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
    "description": "Place un PERSONNAGE dans un DÉCOR cohérent (FLUX Kontext), en préservant son "
                   "identité. Met à jour le portrait du personnage : ses prochains plans (face caméra / "
                   "b-roll) utiliseront ce décor. À appeler AVANT de planifier les plans du personnage.",
    "parameters": {"type": "object", "properties": {
        "character": {"type": "string", "description": "Nom du personnage (doit posséder une image)."},
        "description": {"type": "string", "description": "Le DÉCOR/l'ambiance uniquement, inféré du "
                        "contexte (ex. 'stade de football au coucher du soleil', 'studio TV épuré'). "
                        "Ne décris PAS la personne."},
    }, "required": ["character", "description"]},
})
def set_scene_background(session: VideoSession, character: str, description: str) -> dict:
    _, char = _resolve_character(session, character)
    local = char.get("local_image")
    if not local:
        return {"status": "error", "error": f"personnage '{character}' inconnu ou sans image : "
                "le décor (FLUX Kontext) s'applique au portrait d'un personnage."}
    out = os.path.join(session.output_dir, f"scene_{character}.jpg")
    prompt = BACKGROUND_TEMPLATE.format(scene=description)   # identité préservée + décor inféré
    scene = prepare_scene_portrait(regen=True, src=local, prompt=prompt, out=out)
    url = upload_public(session.ctx.gcs, scene, f"media/test/scene_{character}.jpg")
    # Met à jour le portrait du personnage -> ses prochains plans utiliseront ce décor.
    session.characters[character]["portrait_url"] = url
    return {"status": "ok", "character": character, "scene": description,
            "note": "décor appliqué au personnage; ses prochains plans l'utiliseront"}


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

#!/usr/bin/env python3
"""
capabilities.py — Capacités vidéo réutilisables (enveloppées en tools par video_tools.py).

Fonctions atomiques : TTS, édition de fond (FLUX Kontext), lip-sync (Pruna),
b-roll (Wan), recadrage 9:16, concat, upload GCS. Pas d'orchestration ici
(l'agent orchestre via les tools).
"""

import os
import json
import base64
import subprocess
import urllib.parse
from dataclasses import dataclass

import requests
from openai import OpenAI

from content_creator.config.config import API_KEYS, VIDEO_BACKEND_CONFIG
from content_creator.pipelines.modules import GCSManager, ArticleSummarizer
from content_creator.agentic import ltx_client

# ========================
# CONFIG
# ========================
AVATAR_LOCAL = "image.png"
os.getenv("PRUNA_URL")
PRUNA_URL = "https://api.deepinfra.com/v1/inference/PrunaAI/p-video-avatar"
WAN_URL = "https://api.deepinfra.com/v1/inference/Wan-AI/Wan2.7-R2V"
KONTEXT_MODEL = "black-forest-labs/FLUX.1-Kontext-dev"
# Text-to-image (1re frame pour un backend Reference-to-Video type Wan quand aucune réf fournie).
FLUX_T2I_MODEL = os.getenv("FLUX_T2I_MODEL", "black-forest-labs/FLUX-1-schnell")

OUT_W, OUT_H, FPS = 720, 1280, 30
RESOLUTION = "720P"
RATIO = "9:16"
NEGATIVE_PROMPT = "low resolution, error, worst quality, distorted face, extra fingers"
SEED_BASE = 12345
OUTPUT_DIR = "output/story_hybrid"

SCENE_PORTRAIT_LOCAL = "scene_portrait.jpg"
# Le décor (`{scene}`) est INFÉRÉ par l'agent selon le contexte ; le template garantit
# que l'identité de l'avatar est préservée quel que soit le décor demandé.
BACKGROUND_TEMPLATE = (
    "Place this exact same person, keeping the identical face, hair, skin tone, glasses and "
    "outfit, into: {scene}. ONE single person only, centered, vertical portrait framing, "
    "upper body. Cinematic, photorealistic, shallow depth of field, natural lighting. "
    "Keep the identity and face EXACTLY the same; do NOT duplicate or clone the person; "
    "only change the background/scene."
)
DEFAULT_SCENE = (
    "a warm cozy modern living room at early morning, soft golden light through a large window, "
    "a steaming cup of coffee, blurred plants and a bookshelf"
)
BACKGROUND_PROMPT = BACKGROUND_TEMPLATE.format(scene=DEFAULT_SCENE)  # fallback
PRUNA_MOVEMENT = (
    "Natural lively head movements and subtle gestures in sync with the speech, "
    "expressive but calm, keeping the same person and identity, staying in the same framing."
)


@dataclass
class Ctx:
    """Ressources partagées entre les plans (thread-safe : pas d'état mutable partagé).
    Pas d'« avatar » global : l'identité visuelle/vocale vit dans les personnages (session.characters).
    """

    gcs: GCSManager
    summarizer: ArticleSummarizer


# ========================
# TRANSPORT
# ========================
def sh(cmd: list) -> subprocess.CompletedProcess:
    """Exécute une commande, lève une erreur lisible si échec."""
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError(f"cmd failed: {' '.join(cmd[:3])}...\n{p.stderr[-500:]}")
    return p


def deepinfra_post(url: str, payload: dict, token: str = None) -> dict:
    """POST authentifié vers une inférence DeepInfra, retourne le JSON.
    `token` (depuis le provider du channel) prime ; sinon clé globale du .env."""
    headers = {
        "Authorization": f"bearer {token or API_KEYS['deepinfra_api_key']}",
        "Content-Type": "application/json",
    }
    r = requests.post(url, json=payload, headers=headers, timeout=900)
    if r.status_code >= 400:
        # Remonte le CORPS de l'erreur DeepInfra (raison du 422/400) au lieu d'un HTTPError nu.
        raise RuntimeError(f"DeepInfra {r.status_code}: {r.text[:400]}")
    return r.json()


def _is_ltx_provider(model_config: dict = None) -> bool:
    """Le rôle pointe-t-il vers le serveur LTX local ? (provider_id == "ltx_local").
    C'est le sélecteur PAR CHANNEL (choisi dans le panneau) qui active le backend LTX,
    en complément des flags globaux USE_LTX_* de VIDEO_BACKEND_CONFIG."""
    return bool(model_config) and model_config.get("provider_id") == "ltx_local"


def _deepinfra_inference(model_config: dict, fallback_url: str) -> tuple[str, str]:
    """Depuis un ModelConfig {model_name, provider}, dérive l'URL d'inférence brute
    DeepInfra (/v1/inference/{model}) et le token. Le provider expose le base_url
    OpenAI-compatible (.../v1/openai) ; l'inférence média passe par .../v1/inference.
    Sans config -> (fallback_url, clé globale) : comportement historique préservé."""
    if not model_config:
        return fallback_url, API_KEYS["deepinfra_api_key"]
    provider = model_config["provider"]
    base = provider["base_url"].rstrip("/")
    root = base[: -len("/openai")] if base.endswith("/openai") else base
    return f"{root}/inference/{model_config['model_name']}", provider["token"]


def download(url: str, dest: str) -> str:
    """Télécharge une URL vers un fichier local. Gère aussi les DATA URIs
    (`data:<mime>;base64,<...>`) que certains modèles renvoient dans `video_url`
    (ex. Wan T2V sur DeepInfra) au lieu d'une URL HTTPS."""
    if url.startswith("data:"):
        header, _, payload = url.partition(",")
        data = (
            base64.b64decode(payload)
            if "base64" in header
            else urllib.parse.unquote_to_bytes(payload)
        )
        with open(dest, "wb") as f:
            f.write(data)
        return dest
    r = requests.get(url, stream=True, timeout=300)
    r.raise_for_status()
    with open(dest, "wb") as f:
        for chunk in r.iter_content(chunk_size=8192):
            f.write(chunk)
    return dest


def download_media(url: str, dest: str) -> str:
    """Comme download() mais avec un User-Agent navigateur (évite les 403 de certains CDN,
    ex. musique/stock). Lève sur échec HTTP -> l'appelant renvoie une erreur propre."""
    r = requests.get(url, stream=True, timeout=300, headers=_WEB_UA)
    r.raise_for_status()
    with open(dest, "wb") as f:
        for chunk in r.iter_content(chunk_size=8192):
            f.write(chunk)
    return dest


def upload_public(gcs: GCSManager, local: str, dest: str) -> str:
    """Upload un fichier sur GCS et retourne son URL publique."""
    res = gcs.upload_file(local, dest)
    if not res:
        raise RuntimeError(f"Echec upload GCS: {local}")
    return res["url"]


# ========================
# RECHERCHE D'IMAGE WEB
# ========================
IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp")


def is_image_path(path: str) -> bool:
    """True si le chemin/l'URL pointe vers une image (par extension, query string ignorée)."""
    return path.lower().split("?")[0].endswith(IMAGE_EXTS)


_UNSUPPORTED_IMG_EXTS = (".svg", ".ico", ".bmp", ".tiff", ".tif")
_WEB_UA = {  # évite les 403 (Wikipedia & co.)
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
}


def google_image_urls(query: str, api_key: str, cx: str, n: int = 8) -> list[str]:
    """Retourne jusqu'à `n` URLs d'images candidates (Google Custom Search API), formats
    non supportés (svg/ico/bmp/tiff) filtrés. Liste vide si erreur ou aucun résultat.
    Contrairement à VideoGenerator.google_image_search (1 seule URL), renvoie TOUTE la liste
    pour que l'appelant essaie les candidats successifs (certains renvoient 403/404)."""
    params = {
        "q": query,
        "key": api_key,
        "cx": cx,
        "searchType": "image",
        "num": max(1, min(n, 10)),
    }
    try:
        r = requests.get(
            "https://www.googleapis.com/customsearch/v1", params=params, timeout=10
        )
        r.raise_for_status()
        items = r.json().get("items", []) or []
    except Exception as e:
        print(f"[google_image_urls '{query}'] {e}")
        return []
    urls = []
    for it in items:
        u = it.get("link", "")
        if u.startswith("http") and not u.lower().split("?")[0].endswith(
            _UNSUPPORTED_IMG_EXTS
        ):
            urls.append(u)
    return urls


def fetch_web_image(
    query: str, dest_dir: str, idx: int = 0, max_candidates: int = 8
) -> str | None:
    """Cherche une image sur le web (Google Custom Search) pour `query` et ESSAIE les candidats
    successivement jusqu'à en télécharger+valider un (certains résultats renvoient 403/404 ou
    sont trop petits/corrompus). Retourne le chemin LOCAL d'une image raster exploitable
    (convertie en JPEG), ou None si aucun candidat n'aboutit (clé absente, 0 résultat, tous KO).
    """
    api_key = API_KEYS.get("google_search_api_key")
    cx = API_KEYS.get("google_search_cx")
    if not api_key or not cx:
        print(
            "[fetch_web_image] clé Google Custom Search absente (google_search_api_key/cx)"
        )
        return None
    urls = google_image_urls(query, api_key, cx, n=max_candidates)
    if not urls:
        return None

    from PIL import Image

    os.makedirs(dest_dir, exist_ok=True)
    raw = os.path.join(dest_dir, f"web_image_{idx}_raw")
    out = os.path.join(dest_dir, f"web_image_{idx}.jpg")
    for i, image_url in enumerate(urls):
        try:
            r = requests.get(image_url, timeout=30, headers=_WEB_UA)
            r.raise_for_status()
            with open(raw, "wb") as f:
                f.write(r.content)
            with Image.open(raw) as img:
                if img.width < 64 or img.height < 64:
                    raise ValueError(f"image trop petite: {img.width}x{img.height}")
                rgb = img.convert("RGB") if img.mode in ("RGBA", "P", "LA") else img
                rgb.save(out, "JPEG", quality=90)
            print(
                f"[fetch_web_image '{query}'] candidat {i + 1}/{len(urls)} OK",
                flush=True,
            )
            return out
        except Exception as e:
            print(
                f"[fetch_web_image '{query}'] candidat {i + 1}/{len(urls)} KO ({image_url}): {e}"
            )
            continue
        finally:
            if os.path.exists(raw):
                try:
                    os.remove(raw)
                except OSError:
                    pass
    return None


# ========================
# ENRICHISSEMENT DE PROMPT i2v (VLM / Ollama)
# ========================
# Façon workflow "fast-distilled" : un modèle VISION regarde l'image de référence et
# réécrit l'ACTION voulue en un prompt riche ANCRÉ dans l'image (apparence, décor, cadrage,
# lumière). Le distilled LTX i2v rend bien mieux avec ce type de prompt qu'avec un prompt nu.
_VLM_SYSTEM = (
    "You write prompts for image-to-video generation. Look at the provided image and rewrite the "
    "requested action into ONE vivid, present-tense paragraph that BAKES IN the visible details: "
    "the subject's appearance, clothing, hair, the setting, framing and lighting. Keep the requested "
    "motion and camera. Do NOT invent a different scene, place or person than the image. No preamble, "
    "no markdown — return ONLY the final prompt."
)


def _vlm_base_url() -> str:
    """Base URL OpenAI-compatible du VLM (ex. vLLM) : VLM_BASE_URL si fourni, sinon déduit de
    LTX_SERVER_URL (même hôte, port 11435, chemin /v1)."""
    from content_creator.config.config import VIDEO_BACKEND_CONFIG as c

    u = (c.get("vlm_base_url") or "").strip()
    if u:
        return u.rstrip("/")
    from urllib.parse import urlparse

    host = urlparse(c["ltx_server_url"]).hostname or "localhost"
    return f"http://{host}:11435/v1"


def enrich_i2v_prompt(image_ref: str, base_prompt: str, timeout: int = 90) -> str:
    """Enrichit `base_prompt` (l'action voulue) en faisant DÉCRIRE l'image de réf par un VLM
    servi en OpenAI-compatible (ex. Qwen3-VL via vLLM). Retourne un prompt i2v ancré dans
    l'image, ou `base_prompt` INCHANGÉ si le VLM est désactivé/indisponible (dégradation
    gracieuse, jamais d'exception propagée). `image_ref` = chemin local OU URL."""
    from content_creator.config.config import VIDEO_BACKEND_CONFIG as c

    if not c.get("vlm_enrich"):
        return base_prompt
    try:
        if str(image_ref).startswith("http"):
            raw = requests.get(image_ref, timeout=30, headers=_WEB_UA).content
        else:
            with open(image_ref, "rb") as f:
                raw = f.read()
        # Vérifie que ce sont bien des octets d'IMAGE (magic bytes) : évite d'envoyer une page
        # HTML (URL 404/placeholder) au VLM -> 500 "cannot identify image file".
        is_png = raw[:8].startswith(b"\x89PNG")
        is_jpg = raw[:3] == b"\xff\xd8\xff"
        is_webp = raw[:4] == b"RIFF" and raw[8:12] == b"WEBP"
        is_gif = raw[:6] in (b"GIF87a", b"GIF89a")
        if not (is_png or is_jpg or is_webp or is_gif):
            print(
                "[vlm_enrich] la référence n'est pas une image — enrichissement sauté.",
                flush=True,
            )
            return base_prompt
        media = "image/png" if is_png else "image/jpeg"
        data_uri = f"data:{media};base64,{base64.b64encode(raw).decode('ascii')}"
        client = OpenAI(
            api_key=c.get("vlm_api_key") or "EMPTY",
            base_url=_vlm_base_url(),
            timeout=timeout,
        )
        resp = client.chat.completions.create(
            model=c.get("vlm_model"),
            messages=[
                {"role": "system", "content": _VLM_SYSTEM},
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": f'Requested action and camera to keep: "{base_prompt}". '
                            "Now enrich it using ONLY what is visible in the image.",
                        },
                        {"type": "image_url", "image_url": {"url": data_uri}},
                    ],
                },
            ],
            temperature=0.4,
            max_tokens=1024,  # marge pour un modèle "thinking" (raisonnement + réponse)
        )
        out = (resp.choices[0].message.content or "").strip()
        # Modèle "thinking" (Qwen3-VL Thinking) : retire le bloc de raisonnement <think>…</think>,
        # on ne garde que le prompt final.
        if "</think>" in out:
            out = out.rsplit("</think>", 1)[-1].strip()
        if out:
            print(
                f"[vlm_enrich] ✓ prompt enrichi ({len(base_prompt)}→{len(out)} car.)",
                flush=True,
            )
            return out
        print("[vlm_enrich] réponse vide — prompt inchangé.", flush=True)
    except Exception as e:
        print(
            f"[vlm_enrich] indisponible ({type(e).__name__}: {e}) — prompt inchangé.",
            flush=True,
        )
    return base_prompt


# ========================
# MÉDIA (ffmpeg)
# ========================
def ffprobe_duration(path: str) -> float:
    """Durée d'un média en secondes."""
    p = sh(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=nokey=1:noprint_wrappers=1",
            path,
        ]
    )
    try:
        return float(p.stdout.strip())
    except ValueError:
        return 0.0


def to_rgb(src: str, dst: str) -> str:
    """Convertit en RGB (FLUX rejette parfois l'alpha RGBA)."""
    sh(["ffmpeg", "-y", "-i", src, "-pix_fmt", "rgb24", dst])
    return dst


_VF = (
    f"scale={OUT_W}:{OUT_H}:force_original_aspect_ratio=increase,"
    f"crop={OUT_W}:{OUT_H},fps={FPS},format=yuv420p"
)


def reframe_vertical(video_in: str, out: str, audio_in: str = None) -> str:
    """Recadre/normalise en 720x1280 30fps + audio AAC 44.1k stéréo.
    Si audio_in est fourni, il REMPLACE l'audio de la vidéo (cas Wan)."""
    cmd = ["ffmpeg", "-y", "-i", video_in]
    if audio_in:
        cmd += ["-i", audio_in, "-map", "0:v:0", "-map", "1:a:0", "-shortest"]
    cmd += [
        "-vf",
        _VF,
        "-r",
        str(FPS),
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-ar",
        "44100",
        "-ac",
        "2",
        out,
    ]
    sh(cmd)
    return out


def image_to_clip(
    image_in: str, out: str, duration: float = 4.0, audio_in: str = None
) -> str:
    """Transforme une image fixe en clip vidéo 9:16 (720x1280, 30fps). Si `audio_in` est fourni,
    il sert de bande-son et la durée se cale dessus (-shortest) ; sinon le clip dure `duration` s.
    Utilisé pour insérer une image (ex. récupérée par fetch_web_image) dans le montage.
    """
    cmd = ["ffmpeg", "-y", "-loop", "1", "-i", image_in]
    if audio_in:
        cmd += ["-i", audio_in]
    cmd += ["-vf", _VF, "-r", str(FPS), "-c:v", "libx264", "-pix_fmt", "yuv420p"]
    if audio_in:
        cmd += [
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-c:a",
            "aac",
            "-ar",
            "44100",
            "-ac",
            "2",
            "-shortest",
        ]
    else:
        cmd += ["-t", f"{max(0.5, duration):.2f}"]
    cmd += [out]
    sh(cmd)
    return out


def concat_clips(clips: list, out: str) -> str:
    """Assemble plusieurs clips (déjà normalisés) en une vidéo finale."""
    list_path = os.path.join(os.path.dirname(out) or ".", "_concat_list.txt")
    with open(list_path, "w") as f:
        for c in clips:
            f.write(f"file '{os.path.abspath(c)}'\n")
    sh(
        [
            "ffmpeg",
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            list_path,
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-ar",
            "44100",
            out,
        ]
    )
    return out


# ========================
# CAPACITÉS IA
# ========================
def synthesize_audio(
    summarizer: ArticleSummarizer,
    text: str,
    out: str,
    voice: str = None,
    api_key: str = None,
    base_url: str = None,
    style: str = None,
    voice_model: str = None,
    language: str = None,
) -> tuple:
    """Génère l'audio de narration. Le MOTEUR est choisi d'après le provider du
    voice_generator (base_url) : ElevenLabs si base_url pointe vers elevenlabs.io, sinon
    Google TTS. Tout est propagé depuis le channel (voice_generator / character) :
    `voice`, `style` (Gemini), `voice_model` (= model_id ElevenLabs OU modèle Gemini),
    `language`, `api_key`, `base_url`. Retourne (chemin, durée_sec)."""
    if base_url and "elevenlabs" in base_url:
        summarizer.text_to_speech_elevenlabs(
            text,
            out,
            voice=voice,
            api_key=api_key,
            model=voice_model,
            base_url=base_url,
        )
    else:
        summarizer.text_to_speech_google(
            text,
            out,
            voice=voice,
            api_key=api_key,
            base_url=base_url,
            style=style,
            voice_model=voice_model,
            language=language,
        )
    return out, ffprobe_duration(out)


def prepare_scene_portrait(
    regen: bool = False,
    src: str = AVATAR_LOCAL,
    prompt: str = BACKGROUND_PROMPT,
    out: str = SCENE_PORTRAIT_LOCAL,
    model_config: dict = None,
) -> str:
    """Édite une image d'avatar (FLUX Kontext) pour lui donner un fond cohérent.
    Le provider (endpoint/token) vient du rôle `image_generator` du channel (sinon clé globale).
    Le modèle d'ÉDITION reste KONTEXT_MODEL (édition, distinct du t2i). Réutilise `out` sauf regen.
    """
    if os.path.exists(out) and not regen:
        print(f"♻️  Réutilise {out}")
        return out

    rgb = os.path.join(os.path.dirname(out) or ".", "_portrait_rgb.png")
    os.makedirs(os.path.dirname(rgb) or ".", exist_ok=True)
    to_rgb(src, rgb)
    client = _image_client(model_config)
    print("🎨 FLUX Kontext: génération du fond cohérent...")
    resp = client.images.edit(
        model=KONTEXT_MODEL,
        image=open(rgb, "rb"),
        prompt=prompt,
        n=1,
        size="1024x1024",
    )
    with open(out, "wb") as f:
        f.write(base64.b64decode(resp.data[0].b64_json))
    print(f"   ✅ {out}")
    return out


def _image_client(model_config: dict = None) -> OpenAI:
    """Client OpenAI-compatible pour la génération d'IMAGE (FLUX). Utilise le provider du rôle
    `image_generator` du channel (endpoint/token) s'il est fourni, sinon la clé DeepInfra globale.
    """
    provider = (model_config or {}).get("provider") or {}
    if provider.get("base_url") and provider.get("token"):
        return OpenAI(api_key=provider["token"], base_url=provider["base_url"])
    return OpenAI(
        api_key=API_KEYS["deepinfra_api_key"],
        base_url="https://api.deepinfra.com/v1/openai",
    )


def text_to_image(
    prompt: str, out: str, size: str = "768x1344", model_config: dict = None
) -> str:
    """Génère une image depuis un texte (FLUX t2i) -> fichier local `out`. Modèle + provider issus
    du rôle `image_generator` du channel (sinon FLUX_T2I_MODEL + clé globale). Sert de 1re frame
    pour un backend Reference-to-Video (Wan) quand aucune réf n'est fournie -> t2v aussi sur DeepInfra.
    """
    client = _image_client(model_config)
    model = (model_config or {}).get("model_name") or FLUX_T2I_MODEL
    resp = client.images.generate(model=model, prompt=prompt, n=1, size=size)
    d = resp.data[0]
    if getattr(d, "b64_json", None):
        with open(out, "wb") as f:
            f.write(base64.b64decode(d.b64_json))
    elif getattr(d, "url", None):
        download(d.url, out)
    else:
        raise RuntimeError("FLUX t2i : réponse sans image (ni b64_json ni url)")
    return out


def generate_lipsync(
    portrait_url: str,
    audio_url: str,
    video_prompt: str,
    seed: int,
    dest: str,
    audio_path: str = None,
    ltx_params: dict = None,
    model_config: dict = None,
) -> str:
    """[A-roll] Tête parlante. Deux backends, LTX activé par le flag global
    USE_LTX_LIPSYNC OU par le provider "ltx_local" du rôle lip_sync du channel :

    - DeepInfra/Pruna (défaut) : anime le portrait piloté PAR L'AUDIO (image+audio),
      l'audio sert aussi de bande-son. Retourne le clip brut (audio inclus).
    - LTX local (USE_LTX_LIPSYNC ou provider ltx_local) : le serveur LTX n'a pas d'équivalent image+audio.
      On fait donc de l'IMAGE-TO-VIDEO depuis le portrait (mouvement piloté par le
      prompt), durée calée sur l'audio de narration, puis on muxe la narration TTS
      comme bande-son (le clip rendu N'inclut PAS l'audio -> _render_spec doit le
      remettre, cf. video_tools). `audio_path` = fichier local de narration.
    """
    if VIDEO_BACKEND_CONFIG["use_ltx_lipsync"] or _is_ltx_provider(model_config):
        return _ltx_talking_head(
            portrait_url, video_prompt, seed, dest, audio_path, ltx_params
        )

    url, token = _deepinfra_inference(model_config, PRUNA_URL)
    data = deepinfra_post(
        url,
        {
            "image": portrait_url,
            "audio": audio_url,  # pilote le lip-sync + sert de bande-son
            "video_prompt": video_prompt,
            "resolution": "720p",
            "seed": seed,
        },
        token=token,
    )
    url = data.get("video_url")
    if not url:
        raise RuntimeError(f"Pruna: pas de video_url ({json.dumps(data)[:200]})")
    return download(url, dest)


def _ltx_talking_head(
    portrait_url: str,
    video_prompt: str,
    seed: int,
    dest: str,
    audio_path: str = None,
    ltx_params: dict = None,
) -> str:
    """Tête parlante via serveur LTX local (image-to-video depuis le portrait).

    LTX i2v ne consomme PAS de fichier audio : il génère sa propre piste. On cale donc
    la DURÉE de la vidéo sur celle de la narration TTS (si fournie), et on laisse
    _render_spec remplacer l'audio LTX par la narration via reframe_vertical(audio_in=).
    Le clip retourné NE contient donc pas l'audio voulu -> il faut le muxer ensuite.

    `ltx_params` (depuis l'agent, optionnels) priment sur les défauts : width/height,
    frame_rate, num_inference_steps, image_strength, hdr, duration_s. La durée par
    défaut = longueur de la narration ; l'agent peut la forcer via duration_s.
    """
    p = dict(ltx_params or {})
    ltx_client.health()  # fail-fast si serveur down
    local_img = os.path.join(os.path.dirname(dest) or ".", "_ltx_portrait_src.png")
    download(portrait_url, local_img)
    # Durée : duration_s de l'agent prime, sinon longueur de la narration.
    duration = p.pop("duration_s", None)
    if duration is None and audio_path:
        duration = ffprobe_duration(audio_path)
    # En i2v, le prompt décrit le MOUVEMENT/caméra (la scène vient de l'image).
    prompt = (
        f"{video_prompt}. The person speaks to camera; natural lively head movements "
        "and subtle gestures, expressive but calm, fixed framing, soft natural lighting."
    )
    return ltx_client.generate(
        prompt=prompt,
        dest=dest,
        image_path=local_img,
        seed=seed,
        duration_s=duration,
        image_strength=p.pop("image_strength", 1.0),
        **p,
    )


# Langues validées par LipDub (code locale -> nom anglais attendu dans le prompt).
_LANG_NAMES = {
    "fr": "French",
    "en": "English",
    "es": "Spanish",
    "de": "German",
    "ru": "Russian",
    "it": "Italian",
    "pt": "Portuguese",
}


def lang_name(code: str = None) -> str:
    """'fr-FR' -> 'French'. Défaut French (projet FR) si code absent/inconnu non listé."""
    if not code:
        return "French"
    return _LANG_NAMES.get(code.split("-")[0].lower(), "French")


def ltx_lipsync_clip(
    reference_video: str,
    dialogue_text: str,
    seed: int,
    dest: str,
    language: str = None,
    speaker: str = "A person",
    width: int = None,
    height: int = None,
    reference_strength: float = 1.0,
) -> str:
    """VRAI lip-sync via LTX LipDub (POST /lipsync) : régénère la région des lèvres de
    `reference_video` (qui fournit frames + voix) pour COLLER au dialogue, en conservant
    l'apparence et l'identité vocale. Prompt au format LipDub imposé par le serveur.
    Retourne le MP4 (lèvres synchronisées + audio). NB : la sortie est arrondie à un
    multiple de 64 côté serveur -> renormaliser en aval (reframe_vertical) pour le concat.
    """
    ltx_client.health()  # fail-fast si serveur down
    prompt = f'{speaker} is speaking {lang_name(language)}, saying: "{dialogue_text}"'
    return ltx_client.lipsync(
        reference_video_path=reference_video,
        prompt=prompt,
        dest=dest,
        seed=seed,
        width=width,
        height=height,
        reference_strength=reference_strength,
    )


def _media_image_url(media: list) -> str | None:
    """Récupère l'URL de la 1ère image de référence dans `media` (sinon None)."""
    for m in media or []:
        if m.get("type") == "reference_image" and m.get("url"):
            return m["url"]
    return None


def generate_broll(
    shot: str,
    duration: int,
    seed: int,
    media: list,
    dest: str,
    ltx_params: dict = None,
    model_config: dict = None,
) -> str:
    """[B-roll] Plan cinématographique. Deux backends, LTX activé par le flag global
    USE_LTX_BROLL OU par le provider "ltx_local" du rôle video_generator du channel :

    - DeepInfra/Wan (défaut) : i2v depuis l'image de réf (media).
    - LTX local (USE_LTX_BROLL ou provider ltx_local) : i2v via le serveur LTX local (POST /generate).
    Dans les deux cas l'audio du clip est remplacé par la narration en aval
    (reframe_vertical(audio_in=...)), donc l'audio généré ici n'a pas d'importance.

    `ltx_params` (depuis l'agent, optionnels) priment : width/height, frame_rate,
    num_inference_steps, image_strength, hdr, duration_s (sinon = `duration` calé
    sur la narration).

    Sans image de référence dans `media` (mode génératif/animé, pas d'avatar), le prompt ne
    contraint PAS l'identité « Image 1 » : c'est le `shot` + le mood qui pilotent le style.
    """
    ref_url = _media_image_url(media)
    if ref_url:
        prompt = (
            f"{shot}. Photorealistic, cinematic, the exact same person and face as Image 1, "
            "natural subtle motion, content-creator aesthetic, warm and calm."
        )
    else:
        prompt = (
            f"{shot}. Cinematic, coherent and consistent style, smooth natural motion."
        )

    if VIDEO_BACKEND_CONFIG["use_ltx_broll"] or _is_ltx_provider(model_config):
        p = dict(ltx_params or {})
        ltx_client.health()  # fail-fast si serveur down
        local_img = None
        if ref_url:
            local_img = os.path.join(os.path.dirname(dest) or ".", "_ltx_broll_src.png")
            download(ref_url, local_img)
        return ltx_client.generate(
            prompt=prompt,
            dest=dest,
            image_path=local_img,
            seed=seed,
            duration_s=p.pop("duration_s", float(duration)),
            # 0.9 par défaut en i2v : ANCRE l'identité du personnage (évite que le b-roll
            # réinvente un autre visage). Sans image de réf (t2v), ignoré. Surchargable par l'agent.
            image_strength=p.pop("image_strength", 0.9),
            **p,
        )

    url, token = _deepinfra_inference(model_config, WAN_URL)
    payload = {
        "prompt": prompt,
        "negative_prompt": NEGATIVE_PROMPT,
        "resolution": RESOLUTION,
        "ratio": RATIO,
        "duration": duration,
        "watermark": False,
        "seed": seed,
    }
    # `media` (image de réf) UNIQUEMENT si présent : les modèles T2V (texte->vidéo, ex.
    # Wan2.2-T2V) rejettent une liste vide ; les modèles R2V/I2V l'exigent non vide.
    if media:
        payload["media"] = media
    data = deepinfra_post(url, payload, token=token)
    url = data.get("video_url")
    if not url:
        raise RuntimeError(f"Wan: pas de video_url ({json.dumps(data)[:200]})")
    return download(url, dest)

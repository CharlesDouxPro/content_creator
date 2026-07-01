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
from dataclasses import dataclass

import requests
from openai import OpenAI

from content_creator.config.config import API_KEYS, VIDEO_BACKEND_CONFIG
from content_creator.pipelines.modules import GCSManager, ArticleSummarizer, VideoGenerator
from content_creator.agentic import ltx_client


# ========================
# CONFIG
# ========================
AVATAR_LOCAL = "image.png"
os.getenv("PRUNA_URL")
PRUNA_URL = "https://api.deepinfra.com/v1/inference/PrunaAI/p-video-avatar"
WAN_URL = "https://api.deepinfra.com/v1/inference/Wan-AI/Wan2.7-R2V"
KONTEXT_MODEL = "black-forest-labs/FLUX.1-Kontext-dev"

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
    "Place this exact same man, with the identical face, expression, glasses, hair and suit, "
    "into: {scene}. Cinematic, photorealistic, shallow depth of field, natural lighting. "
    "Keep his identity and face EXACTLY the same, only change the background/scene."
)
DEFAULT_SCENE = (
    "a warm cozy modern living room at early morning, soft golden light through a large window, "
    "a steaming cup of coffee, blurred plants and a bookshelf"
)
BACKGROUND_PROMPT = BACKGROUND_TEMPLATE.format(scene=DEFAULT_SCENE)  # fallback
PRUNA_MOVEMENT = (
    "Natural lively head movements and subtle gestures in sync with his speech, "
    "expressive but calm, staying in the same framing."
)


@dataclass
class Ctx:
    """Ressources partagées entre les plans (thread-safe : pas d'état mutable partagé).
    Pas d'« avatar » global : l'identité visuelle/vocale vit dans les personnages (session.characters)."""
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
    headers = {"Authorization": f"bearer {token or API_KEYS['deepinfra_api_key']}",
               "Content-Type": "application/json"}
    r = requests.post(url, json=payload, headers=headers, timeout=900)
    r.raise_for_status()
    return r.json()


def _deepinfra_inference(model_config: dict, fallback_url: str) -> tuple[str, str]:
    """Depuis un ModelConfig {model_name, provider}, dérive l'URL d'inférence brute
    DeepInfra (/v1/inference/{model}) et le token. Le provider expose le base_url
    OpenAI-compatible (.../v1/openai) ; l'inférence média passe par .../v1/inference.
    Sans config -> (fallback_url, clé globale) : comportement historique préservé."""
    if not model_config:
        return fallback_url, API_KEYS["deepinfra_api_key"]
    provider = model_config["provider"]
    base = provider["base_url"].rstrip("/")
    root = base[:-len("/openai")] if base.endswith("/openai") else base
    return f"{root}/inference/{model_config['model_name']}", provider["token"]


def download(url: str, dest: str) -> str:
    """Télécharge une URL vers un fichier local."""
    r = requests.get(url, stream=True, timeout=300)
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


def fetch_web_image(query: str, dest_dir: str, idx: int = 0) -> str | None:
    """Cherche une image sur le web (Google Custom Search) pour `query`, la télécharge et la
    valide. Retourne le chemin LOCAL d'une image raster exploitable (convertie en JPEG si besoin),
    ou None si aucune image utilisable n'a pu être récupérée (clé absente, pas de résultat,
    téléchargement KO, format invalide). Réutilise VideoGenerator.google_image_search."""
    api_key = API_KEYS.get("google_search_api_key")
    cx = API_KEYS.get("google_search_cx")
    if not api_key or not cx:
        print("[fetch_web_image] clé Google Custom Search absente (google_search_api_key/cx)")
        return None
    image_url = VideoGenerator.google_image_search(query=query, api_key=api_key, cx=cx, num_results=1)
    if not image_url:
        return None

    from PIL import Image

    os.makedirs(dest_dir, exist_ok=True)
    raw = os.path.join(dest_dir, f"web_image_{idx}_raw")
    out = os.path.join(dest_dir, f"web_image_{idx}.jpg")
    headers = {  # évite les 403 (Wikipedia & co.)
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
    }
    try:
        r = requests.get(image_url, timeout=30, headers=headers)
        r.raise_for_status()
        with open(raw, "wb") as f:
            f.write(r.content)
        with Image.open(raw) as img:
            if img.width < 64 or img.height < 64:
                raise ValueError(f"image trop petite: {img.width}x{img.height}")
            if img.mode in ("RGBA", "P", "LA"):
                img = img.convert("RGB")
            img.save(out, "JPEG", quality=90)
        return out
    except Exception as e:
        print(f"[fetch_web_image '{query}'] échec ({image_url}): {e}")
        return None
    finally:
        if os.path.exists(raw):
            try:
                os.remove(raw)
            except OSError:
                pass


# ========================
# MÉDIA (ffmpeg)
# ========================
def ffprobe_duration(path: str) -> float:
    """Durée d'un média en secondes."""
    p = sh(["ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "default=nokey=1:noprint_wrappers=1", path])
    try:
        return float(p.stdout.strip())
    except ValueError:
        return 0.0


def to_rgb(src: str, dst: str) -> str:
    """Convertit en RGB (FLUX rejette parfois l'alpha RGBA)."""
    sh(["ffmpeg", "-y", "-i", src, "-pix_fmt", "rgb24", dst])
    return dst


_VF = (f"scale={OUT_W}:{OUT_H}:force_original_aspect_ratio=increase,"
       f"crop={OUT_W}:{OUT_H},fps={FPS},format=yuv420p")


def reframe_vertical(video_in: str, out: str, audio_in: str = None) -> str:
    """Recadre/normalise en 720x1280 30fps + audio AAC 44.1k stéréo.
    Si audio_in est fourni, il REMPLACE l'audio de la vidéo (cas Wan)."""
    cmd = ["ffmpeg", "-y", "-i", video_in]
    if audio_in:
        cmd += ["-i", audio_in, "-map", "0:v:0", "-map", "1:a:0", "-shortest"]
    cmd += ["-vf", _VF, "-r", str(FPS),
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-ar", "44100", "-ac", "2", out]
    sh(cmd)
    return out


def image_to_clip(image_in: str, out: str, duration: float = 4.0, audio_in: str = None) -> str:
    """Transforme une image fixe en clip vidéo 9:16 (720x1280, 30fps). Si `audio_in` est fourni,
    il sert de bande-son et la durée se cale dessus (-shortest) ; sinon le clip dure `duration` s.
    Utilisé pour insérer une image (ex. récupérée par fetch_web_image) dans le montage."""
    cmd = ["ffmpeg", "-y", "-loop", "1", "-i", image_in]
    if audio_in:
        cmd += ["-i", audio_in]
    cmd += ["-vf", _VF, "-r", str(FPS), "-c:v", "libx264", "-pix_fmt", "yuv420p"]
    if audio_in:
        cmd += ["-map", "0:v:0", "-map", "1:a:0", "-c:a", "aac", "-ar", "44100", "-ac", "2", "-shortest"]
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
    sh(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", list_path,
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-ar", "44100", out])
    return out


# ========================
# CAPACITÉS IA
# ========================
def synthesize_audio(summarizer: ArticleSummarizer, text: str, out: str,
                     voice: str = None, api_key: str = None, base_url: str = None,
                     style: str = None, voice_model: str = None, language: str = None) -> tuple:
    """Génère l'audio de narration (Google TTS). Tout est propagé depuis le channel
    (voice_generator / character) : `voice`, `style` (ton, Gemini TTS), `voice_model`, `language`,
    `api_key`, `base_url`. Retourne (chemin, durée_sec)."""
    summarizer.text_to_speech_google(text, out, voice=voice, api_key=api_key, base_url=base_url,
                                     style=style, voice_model=voice_model, language=language)
    return out, ffprobe_duration(out)


def prepare_scene_portrait(regen: bool = False, src: str = AVATAR_LOCAL,
                           prompt: str = BACKGROUND_PROMPT,
                           out: str = SCENE_PORTRAIT_LOCAL) -> str:
    """Édite une image d'avatar (FLUX Kontext) pour lui donner un fond cohérent.
    Réutilise `out` existant sauf si regen=True."""
    if os.path.exists(out) and not regen:
        print(f"♻️  Réutilise {out}")
        return out

    rgb = os.path.join(os.path.dirname(out) or ".", "_portrait_rgb.png")
    os.makedirs(os.path.dirname(rgb) or ".", exist_ok=True)
    to_rgb(src, rgb)
    client = OpenAI(api_key=API_KEYS["deepinfra_api_key"],
                    base_url="https://api.deepinfra.com/v1/openai")
    print("🎨 FLUX Kontext: génération du fond cohérent...")
    resp = client.images.edit(
        model=KONTEXT_MODEL, image=open(rgb, "rb"),
        prompt=prompt, n=1, size="1024x1024",
    )
    with open(out, "wb") as f:
        f.write(base64.b64decode(resp.data[0].b64_json))
    print(f"   ✅ {out}")
    return out


def generate_lipsync(portrait_url: str, audio_url: str, video_prompt: str,
                     seed: int, dest: str, audio_path: str = None,
                     ltx_params: dict = None, model_config: dict = None) -> str:
    """[A-roll] Tête parlante. Deux backends selon VIDEO_BACKEND_CONFIG :

    - DeepInfra/Pruna (défaut) : anime le portrait piloté PAR L'AUDIO (image+audio),
      l'audio sert aussi de bande-son. Retourne le clip brut (audio inclus).
    - LTX local (USE_LTX_LIPSYNC) : le serveur LTX n'a pas d'équivalent image+audio.
      On fait donc de l'IMAGE-TO-VIDEO depuis le portrait (mouvement piloté par le
      prompt), durée calée sur l'audio de narration, puis on muxe la narration TTS
      comme bande-son (le clip rendu N'inclut PAS l'audio -> _render_spec doit le
      remettre, cf. video_tools). `audio_path` = fichier local de narration.
    """
    if VIDEO_BACKEND_CONFIG["use_ltx_lipsync"]:
        return _ltx_talking_head(portrait_url, video_prompt, seed, dest, audio_path, ltx_params)

    url, token = _deepinfra_inference(model_config, PRUNA_URL)
    data = deepinfra_post(url, {
        "image": portrait_url,
        "audio": audio_url,             # pilote le lip-sync + sert de bande-son
        "video_prompt": video_prompt,
        "resolution": "720p",
        "seed": seed,
    }, token=token)
    url = data.get("video_url")
    if not url:
        raise RuntimeError(f"Pruna: pas de video_url ({json.dumps(data)[:200]})")
    return download(url, dest)


def _ltx_talking_head(portrait_url: str, video_prompt: str, seed: int,
                      dest: str, audio_path: str = None, ltx_params: dict = None) -> str:
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
    ltx_client.health()                                   # fail-fast si serveur down
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
        prompt=prompt, dest=dest, image_path=local_img, seed=seed,
        duration_s=duration, image_strength=p.pop("image_strength", 1.0), **p,
    )


def _media_image_url(media: list) -> str | None:
    """Récupère l'URL de la 1ère image de référence dans `media` (sinon None)."""
    for m in media or []:
        if m.get("type") == "reference_image" and m.get("url"):
            return m["url"]
    return None


def generate_broll(shot: str, duration: int, seed: int, media: list, dest: str,
                   ltx_params: dict = None, model_config: dict = None) -> str:
    """[B-roll] Plan cinématographique. Deux backends selon VIDEO_BACKEND_CONFIG :

    - DeepInfra/Wan (défaut) : i2v depuis l'image de réf (media).
    - LTX local (USE_LTX_BROLL) : i2v via le serveur LTX local (POST /generate).
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
        prompt = f"{shot}. Cinematic, coherent and consistent style, smooth natural motion."

    if VIDEO_BACKEND_CONFIG["use_ltx_broll"]:
        p = dict(ltx_params or {})
        ltx_client.health()                               # fail-fast si serveur down
        local_img = None
        if ref_url:
            local_img = os.path.join(os.path.dirname(dest) or ".", "_ltx_broll_src.png")
            download(ref_url, local_img)
        return ltx_client.generate(
            prompt=prompt, dest=dest, image_path=local_img, seed=seed,
            duration_s=p.pop("duration_s", float(duration)),
            image_strength=p.pop("image_strength", 0.85), **p,
        )

    url, token = _deepinfra_inference(model_config, WAN_URL)
    data = deepinfra_post(url, {
        "prompt": prompt,
        "media": media,
        "negative_prompt": NEGATIVE_PROMPT,
        "resolution": RESOLUTION,
        "ratio": RATIO,
        "duration": duration,
        "watermark": False,
        "seed": seed,
    }, token=token)
    url = data.get("video_url")
    if not url:
        raise RuntimeError(f"Wan: pas de video_url ({json.dumps(data)[:200]})")
    return download(url, dest)

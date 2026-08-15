#!/usr/bin/env python3
"""
ltx25_server.py — Serveur HTTP LTX-2.5 (ltx-pipelines), protocole /v1/videos ASYNCHRONE
compatible avec content_creator/agentic/sglang_video_client.py (même contrat que MiniMax-H3).

  POST /v1/videos            -> {"id": ...}          (soumet un job, retour immédiat)
  GET  /v1/videos/{id}       -> {"status": queued|running|completed|failed}
  GET  /v1/videos/{id}/content -> MP4                 (une fois "completed")
  GET  /health               -> {"status": "ok"}

Le pipeline LTX-2.5 (22B distilled) est chargé UNE SEULE FOIS au démarrage. La génération
est SÉRIALISÉE (un seul job à la fois sur le GPU) : la pipeline content_creator envoie les
plans en parallèle, mais ils sont mis en file ici pour éviter l'OOM.

À lancer DANS le venv du repo Lightricks/LTX-2 (où `ltx_pipelines` est installé) :
    LTX25_MODELS_DIR=~/LTX-2/models LTX25_PORT=30000 python ltx25_server.py

Chemins des composants : soit via LTX25_MODELS_DIR (défauts ci-dessous), soit surchargés
un par un (LTX25_TRANSFORMER, LTX25_TEXT_ENCODER, ...). Voir install_ltx25.sh.
"""

import os
import inspect
import threading
import traceback
import uuid

import requests
import uvicorn
from fastapi import FastAPI, HTTPException, Header
from fastapi.responses import FileResponse
from pydantic import BaseModel

# ---------------------------------------------------------------------------
# Chemins des poids (depuis LTX25_MODELS_DIR, surchargeables individuellement).
# ---------------------------------------------------------------------------
MODELS_DIR = os.environ.get("LTX25_MODELS_DIR", os.path.expanduser("~/LTX-2/models"))


def _p(env_key: str, rel: str) -> str:
    return os.environ.get(env_key, os.path.join(MODELS_DIR, rel))


TRANSFORMER = _p("LTX25_TRANSFORMER", "ltx-2.5/diffusion_models/ltx-2.5-22b-distilled-transformer-bf16.safetensors")
TEXT_ENCODER = _p("LTX25_TEXT_ENCODER", "ltx-2.5/text_encoders/gemma4-12b-with-proj-ltx-2.5-bf16.safetensors")
VIDEO_VAE = _p("LTX25_VIDEO_VAE", "ltx-2.5/vae/ltx-2.5-video-vae-bf16.safetensors")
AUDIO_VAE = _p("LTX25_AUDIO_VAE", "ltx-2.5/vae/ltx-2.5-audio-vae-bf16.safetensors")
DURATION_HEAD = _p("LTX25_DURATION_HEAD", "ltx-2.5/model_patches/ltx-2.5-duration-head-bf16.safetensors")
SPATIAL_UPSAMPLER = _p("LTX25_SPATIAL_UPSAMPLER", "ltx-2.3/ltx-2.3-spatial-upscaler-x2-1.1.safetensors")

PORT = int(os.getenv("LTX25_PORT", "30000"))
API_KEY = os.getenv("LTX25_API_KEY", "")          # optionnel : si défini, Bearer requis
FPS = int(os.getenv("LTX25_FPS", "24"))
JOBS_DIR = os.getenv("LTX25_JOBS_DIR", "/tmp/ltx25_jobs")
os.makedirs(JOBS_DIR, exist_ok=True)

# ---------------------------------------------------------------------------
# Chargement UNIQUE du pipeline distilled.
# ---------------------------------------------------------------------------
print("==> Chargement du pipeline LTX-2.5 (distilled)…", flush=True)
from ltx_pipelines.distilled import DistilledPipeline           # noqa: E402
from ltx_pipelines.utils.model_paths import ModelPaths          # noqa: E402

# On imprime les signatures réelles (elles varient selon la version installée) pour
# diagnostiquer sans deviner.
print(f"==> DistilledPipeline.__init__{inspect.signature(DistilledPipeline.__init__)}", flush=True)
print(f"==> ModelPaths.from_split{inspect.signature(ModelPaths.from_split)}", flush=True)

_model_paths = ModelPaths.from_split(
    transformer_path=TRANSFORMER,
    text_encoder_path=TEXT_ENCODER,
    video_vae_path=VIDEO_VAE,
    audio_vae_path=AUDIO_VAE,
    duration_head_path=DURATION_HEAD,
)


def _fill_required(func, provided: dict, known_defaults: dict) -> dict:
    """Complète `provided` avec des valeurs pour les args REQUIS (sans défaut) de `func`
    non encore fournis : valeur connue si dispo (ex. loras=[]), sinon None + warning."""
    out = dict(provided)
    for name, p in inspect.signature(func).parameters.items():
        if name == "self" or name in out:
            continue
        if p.kind in (p.VAR_POSITIONAL, p.VAR_KEYWORD):
            continue
        if p.default is inspect.Parameter.empty:
            if name in known_defaults:
                out[name] = known_defaults[name]
            else:
                print(f"[WARN] arg requis inconnu de {func.__qualname__}: '{name}' -> None "
                      "(signale-moi la signature imprimée ci-dessus)", flush=True)
                out[name] = None
    return out


# `loras` est requis dans la version installée (la doc l'omet) : [] = pas de LoRA (transformer distilled).
_init_kwargs = _fill_required(
    DistilledPipeline.__init__,
    {"model_paths": _model_paths, "spatial_upsampler_path": SPATIAL_UPSAMPLER},
    known_defaults={"loras": []},
)
PIPE = DistilledPipeline(**_init_kwargs)
_CALL_PARAMS = set(inspect.signature(PIPE.__call__).parameters)
print(f"==> Pipeline prêt. __call__ accepte: {sorted(_CALL_PARAMS)}", flush=True)

_gpu_lock = threading.Lock()        # une seule génération à la fois (GPU)
JOBS: dict[str, dict] = {}          # id -> {status, path?, error?}


# ---------------------------------------------------------------------------
# Mapping payload (/v1/videos, forme _sglang_broll) -> args ltx-pipelines.
# ---------------------------------------------------------------------------
class VideoRequest(BaseModel):
    model: str | None = None
    prompt: str = ""
    seconds: int | None = None
    task: str | None = None
    conditions: list = []
    target: dict = {}
    seed: int = 0
    # champs H3 non pertinents pour LTX-2.5 (ignorés) : num_inference_steps (distilled = 8 fixe),
    # flow_shift, audio_flow_shift, num_outputs_per_prompt.

    model_config = {"extra": "ignore"}


def _round_frames(seconds: float | None) -> int | None:
    """secondes -> nombre de frames vérifiant frames % 8 == 1. None -> laisse la duration-head décider."""
    if not seconds:
        return None
    n = max(9, int(round(seconds * FPS)))
    return n - ((n - 1) % 8)          # plus grand n' <= n avec n' % 8 == 1


def _round32(x: float) -> int:
    return max(32, int(round(x / 32)) * 32)


def _wh_from_target(target: dict) -> tuple[int | None, int | None]:
    """short_edge + aspect_ratio -> (width, height) multiples de 32 (portrait). (None,None) si absent."""
    se = (target or {}).get("short_edge")
    if not se:
        return None, None
    ratio = str((target or {}).get("aspect_ratio", "9:16"))
    try:
        a, b = (float(x) for x in ratio.split(":"))
    except Exception:
        a, b = 9.0, 16.0
    long_edge = se * (max(a, b) / min(a, b))
    return _round32(se), _round32(long_edge)     # portrait : short=width, long=height


def _localize(uri: str) -> str | None:
    """Rend une condition-image accessible localement : file://… -> chemin ; http(s) -> download tmp."""
    if not uri:
        return None
    if uri.startswith("file://"):
        return uri[len("file://"):]
    if uri.startswith(("http://", "https://")):
        dest = os.path.join(JOBS_DIR, f"ref_{uuid.uuid4().hex}.img")
        r = requests.get(uri, timeout=120, stream=True)
        r.raise_for_status()
        with open(dest, "wb") as f:
            for chunk in r.iter_content(8192):
                f.write(chunk)
        return dest
    return uri                                    # chemin local nu


def _images_from(conditions: list) -> list | None:
    """conditions [{type:image, uri, frame_index, strength}] -> [(path, frame_idx, strength)]."""
    out = []
    for c in conditions or []:
        if c.get("type") != "image":
            continue
        path = _localize(c.get("uri", ""))
        if path:
            out.append((path, int(c.get("frame_index", 0)), float(c.get("strength", 1.0))))
    return out or None


def _generate(req: VideoRequest, dest: str) -> None:
    """Appelle le pipeline LTX-2.5 et écrit `dest` (MP4 vidéo+audio). Ne passe que les kwargs
    réellement acceptés par __call__ (robuste aux variations de signature)."""
    kwargs: dict = {"prompt": req.prompt, "seed": req.seed}

    def maybe(key, value):
        if value is not None and key in _CALL_PARAMS:
            kwargs[key] = value

    secs = req.seconds or (req.target or {}).get("duration_seconds")
    maybe("num_frames", _round_frames(float(secs)) if secs else None)
    maybe("images", _images_from(req.conditions))
    w, h = _wh_from_target(req.target)
    maybe("width", w)
    maybe("height", h)
    maybe("frame_rate", float(FPS))

    if "output_path" in _CALL_PARAMS:
        kwargs["output_path"] = dest
        PIPE(**kwargs)
    else:
        raise RuntimeError(
            "DistilledPipeline.__call__ n'expose pas `output_path` ; signature = "
            f"{sorted(_CALL_PARAMS)}. Envoie-moi cette signature (et "
            "`python -m ltx_pipelines.distilled --help`) pour finaliser l'encodage MP4."
        )
    if not os.path.exists(dest):
        raise RuntimeError("génération terminée mais aucun fichier de sortie produit")


def _run(job_id: str, req: VideoRequest) -> None:
    dest = os.path.join(JOBS_DIR, f"{job_id}.mp4")
    JOBS[job_id] = {"status": "running"}
    try:
        with _gpu_lock:                           # sérialise sur le GPU
            _generate(req, dest)
        JOBS[job_id] = {"status": "completed", "path": dest}
    except Exception as e:                          # noqa: BLE001
        traceback.print_exc()
        JOBS[job_id] = {"status": "failed", "error": str(e)}


# ---------------------------------------------------------------------------
# API.
# ---------------------------------------------------------------------------
app = FastAPI(title="LTX-2.5 video server", version="1.0.0")


def _check_auth(authorization: str | None) -> None:
    if API_KEY and authorization != f"Bearer {API_KEY}":
        raise HTTPException(401, "unauthorized")


@app.post("/v1/videos")
def create_video(req: VideoRequest, authorization: str | None = Header(default=None)):
    _check_auth(authorization)
    job_id = uuid.uuid4().hex
    JOBS[job_id] = {"status": "queued"}
    threading.Thread(target=_run, args=(job_id, req), daemon=True).start()
    return {"id": job_id}


@app.get("/v1/videos/{job_id}")
def video_status(job_id: str, authorization: str | None = Header(default=None)):
    _check_auth(authorization)
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(404, "unknown job id")
    out = {"id": job_id, "status": job["status"]}
    if job.get("error"):
        out["error"] = job["error"]
    return out


@app.get("/v1/videos/{job_id}/content")
def video_content(job_id: str, authorization: str | None = Header(default=None)):
    _check_auth(authorization)
    job = JOBS.get(job_id)
    if not job or job.get("status") != "completed":
        raise HTTPException(409, "job not completed")
    return FileResponse(job["path"], media_type="video/mp4")


@app.get("/health")
def health():
    return {"status": "ok"}


if __name__ == "__main__":
    print(f"==> LTX-2.5 server: http://0.0.0.0:{PORT}/v1/videos", flush=True)
    uvicorn.run(app, host="0.0.0.0", port=PORT, workers=1)

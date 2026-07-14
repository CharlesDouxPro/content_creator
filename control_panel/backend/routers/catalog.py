"""
catalog.py — Données de référence (lecture seule) pour peupler les menus du panneau :
skills disponibles, catalogue de voix, providers, modèles suggérés par rôle.
"""

from fastapi import APIRouter
from pydantic import BaseModel

from content_creator.agentic.video_skills import list_skills, get_skill
from content_creator.config.channels import CHIRP3_HD_VOICES
from content_creator.config.schema import PROVIDERS, ROLES, DEFAULT_POOL

router = APIRouter(prefix="/api/catalog", tags=["catalog"])


class SkillInfo(BaseModel):
    name: str
    description: str


class ProviderInfo(BaseModel):
    id: str
    base_url: str
    token_set: bool            # le token est-il présent dans l'env ? (jamais exposé en clair)


class VoicesInfo(BaseModel):
    chirp3: dict[str, list[str]]         # {"male": [...], "female": [...]} (noms courts)
    chirp3_template: str                 # "{lang}-Chirp3-HD-{name}"
    languages: list[str]
    gemini_voice_model: str
    gemini_note: str


class ModelSpecOut(BaseModel):
    model_config = {"protected_namespaces": ()}
    model_name: str
    provider_id: str


class ModelsInfo(BaseModel):
    roles: list[str]
    defaults: dict[str, ModelSpecOut]
    suggestions: dict[str, list[str]]


@router.get("/skills", response_model=list[SkillInfo])
def catalog_skills() -> list[SkillInfo]:
    out = []
    for name in list_skills():
        try:
            desc = get_skill(name).description
        except Exception:
            desc = ""
        out.append(SkillInfo(name=name, description=desc))
    return out


@router.get("/voices", response_model=VoicesInfo)
def catalog_voices() -> VoicesInfo:
    return VoicesInfo(
        chirp3=CHIRP3_HD_VOICES,
        chirp3_template="{lang}-Chirp3-HD-{name}",
        languages=["fr-FR", "en-US", "es-ES", "de-DE", "it-IT", "pt-BR"],
        gemini_voice_model="gemini-3.1-flash-tts-preview",
        gemini_note="Mode expressif : voice = nom court + voice_model + language ; débloque `style`.",
    )


@router.get("/providers", response_model=list[ProviderInfo])
def catalog_providers() -> list[ProviderInfo]:
    return [
        ProviderInfo(id=pid, base_url=cfg["base_url"], token_set=bool(cfg["token"]))
        for pid, cfg in PROVIDERS.items()
    ]


@router.get("/models", response_model=ModelsInfo)
def catalog_models() -> ModelsInfo:
    defaults = {
        role: ModelSpecOut(model_name=getattr(DEFAULT_POOL, role).model_name,
                           provider_id=getattr(DEFAULT_POOL, role).provider_id)
        for role in ROLES
    }
    suggestions = {
        "master_mind": ["anthropic/claude-opus-4-8", "anthropic/claude-sonnet-4-6", "openai/gpt-oss-120b"],
        "slm": ["anthropic/claude-opus-4-8", "anthropic/claude-haiku-4-5", "openai/gpt-oss-120b"],
        "lip_sync": ["PrunaAI/p-video-avatar"],
        "video_generator": ["Wan-AI/Wan2.7-R2V"],
        "voice_generator": ["fr-FR-Chirp3-HD-Vindemiatrix", "fr-FR-Chirp3-HD-Kore", "fr-FR-Chirp3-HD-Charon"],
    }
    return ModelsInfo(roles=list(ROLES), defaults=defaults, suggestions=suggestions)

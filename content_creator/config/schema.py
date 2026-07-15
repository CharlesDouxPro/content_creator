#!/usr/bin/env python3
"""
schema.py — Schéma TYPÉ unique de la configuration des channels (source de vérité).

Ce module est la couche basse partagée par la pipeline ET le backend du panneau de
contrôle. Il contient :
  - les modèles Pydantic éditables (`Channel`, `ModelPool`, `Character`, …) — c'est CE
    schéma qui est sérialisé dans `channels.json` et validé à chaque écriture ;
  - le registre `PROVIDERS` (base_url + token depuis l'env — les SECRETS ne sont jamais
    sérialisés dans le JSON, seul un `provider_id` l'est) ;
  - la résolution `to_pipeline_config()` : un `Channel` (forme éditable) -> le `dict`
    "résolu" que la pipeline consomme déjà (`PipelineConfig`, tokens injectés).

channels.py se contente d'appeler `load_channels()` + `to_pipeline_config()` pour produire
`PIPELINES`. Aucun consommateur en aval ne change.
"""

from __future__ import annotations

import os
import json
from typing import Optional, TypedDict

from pydantic import BaseModel, ConfigDict, Field, field_validator

from content_creator.config.config import API_KEYS, VIDEO_BACKEND_CONFIG
from content_creator.agentic.video_skills import list_skills

# ============================================================================
# Providers — base_url public + token depuis l'env. Le JSON ne référence qu'un
# `provider_id` ; le token est injecté ici à la résolution (jamais sérialisé).
# ============================================================================
class ProviderConfig(TypedDict):
    base_url: str
    token: str


PROVIDERS: dict[str, ProviderConfig] = {
    "arlq_deepinfra": {
        "base_url": "https://api.deepinfra.com/v1/openai",
        "token": os.getenv("ARLQ_DEEPINFRA_TOKEN", ""),
    },
    "charles_deepinfra": {
        "base_url": "https://api.deepinfra.com/v1/openai",
        "token": os.getenv("CHARLES_DEEPINFRA_TOKEN", ""),
    },
    # Le TTS n'est pas DeepInfra : provider = Google Cloud Text-to-Speech.
    "google_tts": {
        "base_url": "https://texttospeech.googleapis.com/v1",
        "token": str(API_KEYS.get("google_tts_api_key") or ""),
    },
    # Synthèse vocale ElevenLabs : token = ELEVENLABS_API_KEY. Le `model_name` du rôle
    # voice_generator (ou character.voice) = un voice_id ElevenLabs.
    "elevenlabs": {
        "base_url": "https://api.elevenlabs.io",
        "token": os.getenv("ELEVENLABS_API_KEY", ""),
    },
    # Serveur LTX-2.3 LOCAL (cf. repo LTX-video-server). Choisir ce provider sur le rôle
    # video_generator et/ou lip_sync -> ce channel génère via le serveur LTX (POST /generate,
    # /lipsync) au lieu de DeepInfra. Pas de token (serveur local sans auth). Le `model_name`
    # est cosmétique côté client : c'est le serveur qui décide du checkpoint chargé ; c'est le
    # provider_id qui aiguille le backend (cf. capabilities._is_ltx_provider). L'URL/timeout du
    # serveur restent pilotés par VIDEO_BACKEND_CONFIG (LTX_SERVER_URL, LTX_TIMEOUT).
    "ltx_local": {
        "base_url": str(VIDEO_BACKEND_CONFIG["ltx_server_url"]),
        "token": "",
    },
}

ROLES = ("master_mind", "slm", "lip_sync", "video_generator", "voice_generator")


# ============================================================================
# Formes "résolues" consommées par la pipeline (tokens présents). Ne PAS éditer
# à la main : produites par to_pipeline_config().
# ============================================================================
class ModelConfig(TypedDict):
    model_name: str
    provider_id: str          # conservé jusqu'à l'aval pour aiguiller le backend (ex. LTX local)
    provider: ProviderConfig


class PoolModelConfig(TypedDict):
    master_mind: ModelConfig
    slm: ModelConfig
    lip_sync: ModelConfig
    video_generator: ModelConfig
    voice_generator: ModelConfig


# ============================================================================
# Modèles ÉDITABLES (sérialisés dans channels.json, validés par le backend).
# ============================================================================
class ModelSpec(BaseModel):
    """Un modèle pour un rôle : le nom + une RÉFÉRENCE de provider (pas de token)."""
    # protected_namespaces=() : autorise le champ `model_name` (préfixe `model_` réservé sinon).
    model_config = ConfigDict(extra="forbid", protected_namespaces=())

    model_name: str = Field(min_length=1)
    provider_id: str

    @field_validator("provider_id")
    @classmethod
    def _known_provider(cls, v: str) -> str:
        if v not in PROVIDERS:
            raise ValueError(f"provider_id inconnu: {v!r} (dispo: {sorted(PROVIDERS)})")
        return v


class ModelPool(BaseModel):
    """Pool de modèles par rôle (le cœur d'un `models_config`)."""
    model_config = ConfigDict(extra="forbid")

    master_mind: ModelSpec
    slm: ModelSpec
    lip_sync: ModelSpec
    video_generator: ModelSpec
    voice_generator: ModelSpec


class Character(BaseModel):
    """Personnage nommé (miroir éditable de CharacterConfig). Tous champs optionnels."""
    model_config = ConfigDict(extra="forbid")

    image: Optional[str] = None          # chemin local OU URL GCS publique (i2v / lip-sync)
    voice: Optional[str] = None          # nom de voix (complet Chirp3 ou court Gemini)
    style: Optional[str] = None          # ton (Gemini TTS uniquement)
    voice_model: Optional[str] = None    # modelName TTS (requis pour `style`)
    language: Optional[str] = None       # locale (ex. "fr-FR")
    description: Optional[str] = None     # apparence/personnalité (injectée dans les shots)


class Ressources(BaseModel):
    """Matière première mise à disposition de l'agent (tout optionnel)."""
    model_config = ConfigDict(extra="forbid")

    urls: list[str] = Field(default_factory=list)
    local_paths: list[str] = Field(default_factory=list)
    audio_paths: list[str] = Field(default_factory=list)
    notes: Optional[str] = None


class Context(BaseModel):
    """Brief créatif du channel."""
    model_config = ConfigDict(extra="forbid")

    prompt: str = ""
    ressources: Ressources = Field(default_factory=Ressources)
    mood: str = ""
    characters: dict[str, Character] = Field(default_factory=dict)


class Channel(BaseModel):
    """Un channel = un type de vidéo à produire (unité éditable du panneau)."""
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    skill: str
    models: ModelPool
    context: Context = Field(default_factory=Context)

    @field_validator("skill")
    @classmethod
    def _known_skill(cls, v: str) -> str:
        available = list_skills()
        if available and v not in available:
            raise ValueError(f"skill inconnu: {v!r} (dispo: {available})")
        return v


# ============================================================================
# Pool par défaut (équivalent de l'ancien default_models_config, forme éditable).
# ============================================================================
DEFAULT_POOL = ModelPool(
    master_mind=ModelSpec(model_name="anthropic/claude-opus-4-8", provider_id="arlq_deepinfra"),
    slm=ModelSpec(model_name="anthropic/claude-opus-4-8", provider_id="arlq_deepinfra"),
    lip_sync=ModelSpec(model_name="PrunaAI/p-video-avatar", provider_id="charles_deepinfra"),
    video_generator=ModelSpec(model_name="Wan-AI/Wan2.7-R2V", provider_id="charles_deepinfra"),
    # Voix par défaut = ElevenLabs (model_name = voice_id ElevenLabs utilisé si un personnage
    # ne précise pas sa voix). Ici "Brian" (voix narrateur grave).
    voice_generator=ModelSpec(model_name="nPczCjzI2devNBz1zQrb", provider_id="elevenlabs"),
)


# ============================================================================
# Résolution -> formes consommées par la pipeline (tokens injectés)
# ============================================================================
def resolve_pool(pool: ModelPool) -> PoolModelConfig:
    """ModelPool éditable -> PoolModelConfig résolu (provider_id -> {base_url, token})."""
    return {  # type: ignore[return-value]
        role: {
            "model_name": spec.model_name,
            "provider_id": spec.provider_id,
            "provider": PROVIDERS[spec.provider_id],
        }
        for role, spec in ((r, getattr(pool, r)) for r in ROLES)
    }


def to_pipeline_config(channel: Channel) -> dict:
    """Channel éditable -> `PipelineConfig` (dict) que process_channel/run_agent consomment."""
    ctx = channel.context
    ressources = {k: v for k, v in ctx.ressources.model_dump(exclude_none=True).items() if v}
    characters = {
        name: c.model_dump(exclude_none=True) for name, c in ctx.characters.items()
    }
    return {
        "name": channel.name,
        "skill": channel.skill,
        "models_config": resolve_pool(channel.models),
        "context": {
            "prompt": ctx.prompt,
            "ressources": ressources,
            "mood": ctx.mood,
            "characters": characters,
        },
    }


# ============================================================================
# Persistance JSON (source de vérité) — écriture atomique.
# ============================================================================
CHANNELS_JSON = os.path.join(os.path.dirname(__file__), "channels.json")


def load_channels() -> list[Channel]:
    """Lit channels.json et valide chaque entrée. Fichier absent -> liste vide."""
    if not os.path.exists(CHANNELS_JSON):
        return []
    with open(CHANNELS_JSON, encoding="utf-8") as f:
        data = json.load(f)
    return [Channel.model_validate(item) for item in data]


def save_channels(channels: list[Channel]) -> None:
    """Écrit channels.json (validé, atomique tmp+rename)."""
    payload = [c.model_dump(exclude_none=True) for c in channels]
    tmp = f"{CHANNELS_JSON}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    os.replace(tmp, CHANNELS_JSON)

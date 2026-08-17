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

import json
import os
from typing import TypedDict

from pydantic import BaseModel, ConfigDict, Field, field_validator

from content_creator.agentic.video_skills import list_skills
from content_creator.config.providers import load_providers


# ============================================================================
# Providers — le REGISTRE (base_url + api_key, nommés) vit désormais dans
# content_creator/config/providers.py (défini/renseigné depuis le front, stocké en GCS).
# Ici on ne fait que le RÉSOUDRE vers la forme {base_url, token} attendue en aval.
# Le JSON channels ne référence qu'un `provider_id` (nom) ; la clé est injectée à la
# résolution (jamais sérialisée dans channels.json).
# ============================================================================
class ProviderConfig(TypedDict):
    base_url: str
    token: str


def get_providers() -> dict[str, ProviderConfig]:
    """Registre résolu {base_url, token} depuis inference_engine (source de vérité)."""
    return {
        name: {"base_url": p.base_url, "token": p.api_key}
        for name, p in load_providers().items()
    }


ROLES = ("master_mind", "slm", "video_avatar", "video_generator", "voice_generator",
         "image_generator")


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
    video_avatar: ModelConfig          # image portrait + audio -> talking head (Pruna p-video-avatar)
    video_generator: ModelConfig
    voice_generator: ModelConfig
    image_generator: ModelConfig       # text-to-image + édition de fond (avatars, 1re frame i2v)


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
        known = load_providers()
        if v not in known:
            raise ValueError(f"provider_id inconnu: {v!r} (dispo: {sorted(known)})")
        return v


class ModelPool(BaseModel):
    """Pool de modèles par rôle (le cœur d'un `models_config`)."""
    model_config = ConfigDict(extra="forbid")

    master_mind: ModelSpec
    slm: ModelSpec
    video_avatar: ModelSpec            # image portrait + audio -> talking head (Pruna p-video-avatar)
    video_generator: ModelSpec
    voice_generator: ModelSpec
    # OPTIONNEL (défaut) : les channels antérieurs à ce rôle restent valides sans le déclarer.
    # Provider par défaut = charles_deepinfra (garanti dans le registre ; modèle image dispo dessus).
    # model_name = modèle TEXT-TO-IMAGE ; l'édition de fond utilise IMAGE_EDIT_MODEL (Wan) via le même
    # provider. Pour une clé image dédiée, enregistrer `deepinfra_image` via le front puis le choisir.
    image_generator: ModelSpec = Field(
        default_factory=lambda: ModelSpec(model_name="stabilityai/sd3.5", provider_id="charles_deepinfra"))


class Character(BaseModel):
    """Personnage nommé (miroir éditable de CharacterConfig). Tous champs optionnels."""
    model_config = ConfigDict(extra="forbid")

    image: str | None = None          # chemin local OU URL GCS publique (i2v / lip-sync)
    voice: str | None = None          # nom de voix (complet Chirp3 ou court Gemini)
    style: str | None = None          # ton (Gemini TTS uniquement)
    voice_model: str | None = None    # modelName TTS (requis pour `style`)
    language: str | None = None       # locale (ex. "fr-FR")
    description: str | None = None     # apparence/personnalité (injectée dans les shots)
    avatar_prompt: str | None = None   # si aucune `image` : prompt de GÉNÉRATION de l'avatar (FLUX t2i).
                                       # À défaut, l'avatar est généré depuis `description`.


class Ressources(BaseModel):
    """Matière première mise à disposition de l'agent (tout optionnel)."""
    model_config = ConfigDict(extra="forbid")

    urls: list[str] = Field(default_factory=list)
    local_paths: list[str] = Field(default_factory=list)
    audio_paths: list[str] = Field(default_factory=list)
    notes: str | None = None


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
    video_avatar=ModelSpec(model_name="PrunaAI/p-video-avatar", provider_id="charles_deepinfra"),
    video_generator=ModelSpec(model_name="Wan-AI/Wan2.7-R2V", provider_id="charles_deepinfra"),
    # Voix par défaut = ElevenLabs (model_name = voice_id ElevenLabs utilisé si un personnage
    # ne précise pas sa voix). Ici "Brian" (voix narrateur grave).
    voice_generator=ModelSpec(model_name="nPczCjzI2devNBz1zQrb", provider_id="elevenlabs"),
    # Image : t2i sd3.5 sur DeepInfra. L'édition de fond utilise IMAGE_EDIT_MODEL (Wan) via le MÊME provider.
    image_generator=ModelSpec(model_name="stabilityai/sd3.5", provider_id="charles_deepinfra"),
)


# ============================================================================
# Résolution -> formes consommées par la pipeline (tokens injectés)
# ============================================================================
def resolve_pool(pool: ModelPool) -> PoolModelConfig:
    """ModelPool éditable -> PoolModelConfig résolu (provider_id -> {base_url, token})."""
    provs = get_providers()
    return {  # type: ignore[return-value]
        role: {
            "model_name": spec.model_name,
            "provider_id": spec.provider_id,
            "provider": provs[spec.provider_id],
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

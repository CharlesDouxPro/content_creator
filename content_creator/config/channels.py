#!/usr/bin/env python3
"""
channels.py — Liste des channels traités par pipeline_agentic.

Chaque channel :
  - name    : identifiant (sert au log, à la dédup, au dossier de sortie)
  - skill   : le type de vidéo (skill de l'agent) — ex. "avatar_story", "free_creator"
  - models_config : pool de modèles par rôle (master_mind / slm / lip_sync / video_generator /
                    voice_generator). Note : voice_generator = Google TTS (model_name = nom de voix).
  - context : le BRIEF passé à l'agent — il décide librement de l'ordre des opérations
              et des tools à utiliser pour produire la vidéo demandée.

`context` :
  - prompt      : description en langage naturel de la vidéo voulue (le brief créatif).
  - ressources  : matière première mise à disposition de l'agent (mixte) : urls (pages à scraper /
                  médias), local_paths (clips, images à monter), audio_paths (musique, voix off),
                  notes (contexte libre).
  - mood        : ton/ambiance ; prime sur les choix de réalisation par défaut.
  - characters  : personnages nommés (image/voix/style/description). PAS de notion d'« avatar » :
                  c'est le SKILL qui décide de l'usage (ex. avatar_story : un personnage = l'avatar
                  face caméra). Plusieurs personnages = plusieurs voix/visages.

Tous les channels sont traités EN PARALLÈLE par la pipeline.
"""

import os
from typing import Literal, TypedDict

from content_creator.config.config import API_KEYS

SkillName = Literal[
    "avatar_story",
    "free_creator",
]


class CharacterConfig(TypedDict, total=False):
    image: str           # chemin/URL d'une image de référence (lip-sync / i2v)
    voice: str           # nom de voix : court ("Achernar") si Gemini, complet ("fr-FR-Chirp3-HD-…") sinon
    style: str           # instructions de ton (ex. "ton bravache") — Gemini TTS uniquement
    voice_model: str     # modelName TTS, ex. "gemini-3.1-flash-tts-preview" ; REQUIS pour utiliser `style`
    language: str        # locale (ex. "fr-FR") ; sinon déduite du nom de voix (Chirp3)
    description: str      # apparence/personnalité, injectée dans les shot_description


# ============================================================================
# Catalogue de voix — pour renseigner CharacterConfig.voice / voice_generator.
#
# 2 modes de TTS (cf. modules.text_to_speech_google) :
#   • Chirp 3: HD (défaut, simple) — voice = nom COMPLET "fr-FR-Chirp3-HD-<Nom>".
#       Pas de `style`. Le préfixe locale (fr-FR, en-US, …) choisit la langue ;
#       le <Nom> d'astre est le même pour toutes les locales.
#   • Gemini TTS (expressif) — voice = nom COURT ("Orus"), + voice_model
#       ("gemini-3.1-flash-tts-preview") + language ("fr-FR"). Débloque `style`.
#
# Genre confirmé (jeu de base). Catalogue complet (~30 voix, autres noms d'astres :
# Vindemiatrix, Iapetus, Alnilam, Achernar…) : https://cloud.google.com/text-to-speech/docs/chirp3-hd
# ============================================================================
CHIRP3_HD_VOICES: dict[str, list[str]] = {
    "male":   ["Puck", "Charon", "Fenrir", "Orus"],
    "female": ["Aoede", "Kore", "Leda", "Zephyr"],
}


def chirp3(name: str, lang: str = "fr-FR") -> str:
    """Nom de voix Chirp3-HD complet à partir d'un nom court : chirp3("Orus") -> "fr-FR-Chirp3-HD-Orus"."""
    return f"{lang}-Chirp3-HD-{name}"


class ProviderConfig(TypedDict):
    base_url: str
    token: str


class ModelConfig(TypedDict):
    model_name: str
    provider: ProviderConfig


class PoolModelConfig(TypedDict):
    master_mind: ModelConfig
    video_generator: ModelConfig
    slm: ModelConfig
    lip_sync: ModelConfig
    voice_generator: ModelConfig


class PipelineRessources(TypedDict, total=False):
    """Matière première de l'agent (tous les champs OPTIONNELS). Mixte par nature :
    l'agent pioche selon le skill et le brief."""

    urls: list[str]
    local_paths: list[str]
    audio_paths: list[str]
    notes: str


class PipelineContext(TypedDict, total=False):
    prompt: str
    ressources: PipelineRessources
    mood: str
    characters: dict[str, CharacterConfig]


class PipelineConfig(TypedDict):
    name: str
    skill: SkillName
    models_config: PoolModelConfig
    context: PipelineContext


arlq_deepinfra_config: ProviderConfig = {
    "base_url": "https://api.deepinfra.com/v1/openai",
    "token": os.getenv("ARLQ_DEEPINFRA_TOKEN", ""),
}

charles_deepinfra_config: ProviderConfig = {
    "base_url": "https://api.deepinfra.com/v1/openai",
    "token": os.getenv("CHARLES_DEEPINFRA_TOKEN", ""),
}

# Le TTS n'est pas DeepInfra : provider = Google Cloud Text-to-Speech, token = clé Google.
google_tts_config: ProviderConfig = {
    "base_url": "https://texttospeech.googleapis.com/v1",
    "token": API_KEYS["google_tts_api_key"],
}

default_models_config: PoolModelConfig = {
    "master_mind": {
        "model_name": "anthropic/claude-opus-4-8",
        "provider": arlq_deepinfra_config,
    },
    "lip_sync": {
        "model_name": "PrunaAI/p-video-avatar",
        "provider": charles_deepinfra_config,
    },
    "slm": {
        "model_name": "anthropic/claude-opus-4-8",
        "provider": arlq_deepinfra_config,
    },
    "video_generator": {
        "model_name": "Wan-AI/Wan2.7-R2V",
        "provider": charles_deepinfra_config,
    },
    "voice_generator": {
        "model_name": "fr-FR-Chirp3-HD-Vindemiatrix",
        "provider": google_tts_config,
    },
}

PIPELINES: list[PipelineConfig] = [
    {

        "name": "20min-foot",
        "skill": "avatar_story",
        "models_config": default_models_config,
        "context": {
            "prompt": "Crée une vidéo TikTok verticale à partir du premier article foot "
            "non traité : accroche forte, narration rythmée, alternance face caméra "
            "et b-roll, conclusion qui donne envie de réagir. Raconte l'actualité de manière dynamique",
            "ressources": {
                "urls": ["https://www.20minutes.fr/sport/football/"],
            },
            "characters": {
                "presenter": {
                    "image": "avatars/avatar_femme_foot.png",
                    "voice": chirp3("Kore"),
                    "language": "fr-FR",
                    "description": "présentatrice mature, costume, posture assurée",
                },
            },
            "mood": "",
        },
    },
    # {
    #     "name": "20min-tennis",
    #     "skill": "avatar_story",
    #     "models_config": default_models_config,
    #     "context": {
    #         "prompt": "Crée une vidéo TikTok verticale à partir du premier article tennis "
    #         "non traité : accroche forte, narration rythmée, alternance face caméra "
    #         "et b-roll, conclusion qui donne envie de réagir.",
    #         "ressources": {
    #             "urls": ["https://www.20minutes.fr/sport/tennis/"],
    #             "avatar": "avatars/avatar_mature_men.jpeg",
    #         },
    #         "mood": "",
    #     },
    # },
    {
        "name": "fruits",
        "skill": "free_creator",
        "models_config": default_models_config,
        "context": {
            "prompt": """
                Crée une histoire totalement fictive.
                C'est l'histoire de 3 petits poneys qui sont enfaite trump khamenei et macron et ont des dialogues pour se battre pour le détroit d'ormuz.
                Fait une video qui résume avec humour en version annimé cet episode géopolitique.
                Fais PARLER chaque poney via son personnage (paramètre `character`) pour des voix
                distinctes ; alterne un personnage par plan pour le dialogue.
            """,
            "ressources": {},
            "mood": "dessin annimé pour adulte",
            "characters": {
                "trump": {
                    "voice": "fr-FR-Chirp3-HD-Charon",
                    "description": "petit poney orange à la mèche blonde, ton bravache et théâtral",
                },
                "khamenei": {
                    "voice": "fr-FR-Chirp3-HD-Fenrir",
                    "description": "petit poney sombre à turban, ton grave et solennel",
                },
                "macron": {
                    "voice": "fr-FR-Chirp3-HD-Iapetus",
                    "description": "petit poney bleu-blanc-rouge, ton posé et un peu pédant",
                },
            },
        },
    },
]

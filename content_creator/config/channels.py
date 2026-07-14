#!/usr/bin/env python3
"""
channels.py — Channels traités par pipeline_agentic, CHARGÉS depuis config/channels.json.

Le SCHÉMA typé et la persistance vivent dans `schema.py` (source de vérité éditée par le
panneau de contrôle). Ce module se contente d'exposer, pour la pipeline, les formes "résolues"
attendues en aval — sans qu'aucun consommateur ne change :

  - `PIPELINES`            : list[PipelineConfig] (dict), tokens providers injectés.
  - `default_models_config`: PoolModelConfig par défaut (fallback de run_agent).
  - `CHIRP3_HD_VOICES` / `chirp3()` : catalogue de voix (réutilisé par le catalogue du panneau).

Éditer un channel = passer par le panneau (ou éditer channels.json). PLUS de channels en dur ici.

Rappel structure d'un channel (cf. schema.Channel) :
  - name    : identifiant (log, dédup, dossier de sortie).
  - skill   : type de vidéo (fichier skills/<skill>.md).
  - models  : pool de modèles par rôle (master_mind / slm / lip_sync / video_generator /
              voice_generator), chacun {model_name, provider_id}. Le token est injecté à la
              résolution depuis PROVIDERS (jamais stocké dans le JSON).
  - context : brief (prompt / ressources / mood / characters).

Tous les channels sont traités EN PARALLÈLE par la pipeline.
"""

from content_creator.config.schema import (  # noqa: F401  (ré-exports pour l'aval)
    PROVIDERS,
    ProviderConfig,
    ModelConfig,
    PoolModelConfig,
    ModelPool,
    Channel,
    DEFAULT_POOL,
    load_channels,
    save_channels,
    resolve_pool,
    to_pipeline_config,
)


# ============================================================================
# Catalogue de voix — pour renseigner Character.voice / voice_generator.
#
# 2 modes de TTS (cf. modules.text_to_speech_google) :
#   • Chirp 3: HD (défaut, simple) — voice = nom COMPLET "fr-FR-Chirp3-HD-<Nom>".
#       Pas de `style`. Le préfixe locale (fr-FR, en-US, …) choisit la langue ;
#       le <Nom> d'astre est le même pour toutes les locales.
#   • Gemini TTS (expressif) — voice = nom COURT ("Orus"), + voice_model
#       ("gemini-3.1-flash-tts-preview") + language ("fr-FR"). Débloque `style`.
#
# Catalogue complet (~30 voix) : https://cloud.google.com/text-to-speech/docs/chirp3-hd
# ============================================================================
CHIRP3_HD_VOICES: dict[str, list[str]] = {
    "male":   ["Puck", "Charon", "Fenrir", "Orus"],
    "female": ["Aoede", "Kore", "Leda", "Zephyr"],
}


def chirp3(name: str, lang: str = "fr-FR") -> str:
    """Nom de voix Chirp3-HD complet à partir d'un nom court : chirp3("Orus") -> "fr-FR-Chirp3-HD-Orus"."""
    return f"{lang}-Chirp3-HD-{name}"


# ============================================================================
# Formes RÉSOLUES consommées par la pipeline (chargées depuis channels.json).
# ============================================================================
# Modèles par défaut (fallback de run_agent quand un channel n'en fournit pas).
default_models_config: PoolModelConfig = resolve_pool(DEFAULT_POOL)

# Channels résolus : Channel (éditable) -> PipelineConfig (dict, tokens injectés).
PIPELINES: list[dict] = [to_pipeline_config(c) for c in load_channels()]

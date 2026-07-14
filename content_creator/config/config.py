"""
Configuration file for the TikTok/Instagram content pipeline.
Modify the PIPELINE_CONFIG dictionary to change thematic and media settings.
"""

import os
from datetime import datetime

from dotenv import load_dotenv

# Charge le .env situé à la racine du repo content_creator (deux niveaux au-dessus
# de ce fichier : config/ -> content_creator/ -> repo). Les variables déjà présentes
# dans l'environnement priment (override=False) : on peut surcharger depuis le shell.
_ENV_PATH = os.path.join(os.path.dirname(__file__), "..", "..", ".env")
load_dotenv(_ENV_PATH, override=False)


def _env_bool(name: str, default: bool = False) -> bool:
    """Lit une variable d'env booléenne ('1', 'true', 'yes', 'on')."""
    val = os.getenv(name)
    if val is None:
        return default
    return val.strip().lower() in {"1", "true", "yes", "on"}


# ========================
# MAIN PIPELINE CONFIGURATION
# ========================
PIPELINE_CONFIG = {
    # Media sources configuration
    "media_sources": {
        "www.20minutes.fr": {
            "thematics": ["sport/football/", "sport/tennis/"],
            "sector": "articles",
            "enabled": True,
        },
        "www.lefigaro.fr": {
            "thematics": ["actualite-france", "international", "economie", "culture"],
            "sector": "articles",  # Secteur d'activité
            "enabled": False,  # Set to True to enable this source
        },
        # Add more media domains as needed
    },
    # Processing mode
    "processing_mode": "multi_thematic",  # Options: "single" or "multi_thematic"
    # Single mode config (used when processing_mode = "single")
    "single_media_domain": "www.20minutes.fr",
    "single_thematic": "sport",
    # Article processing
    "skip_processed": True,  # Skip articles that have already been processed
    "articles_per_thematic": 1,  # Number of unprocessed articles to process per thematic
    # Output settings
    "output_dir": "./output",
    "audio_filename": "output.mp3",
    # Video generation settings (removed hardcoded URLs - videos will be generated)
    # Outro settings
    "outro_duration": 4.0,  # seconds
    "extra_audio_padding": 3.0,  # seconds
    "outro_text": {"brand": "My Brand Realtors", "contact_name": "Elisabeth Parker"},
}

# ========================
# API KEYS AND CREDENTIALS
# ========================
# Tous les SECRETS proviennent du .env (cf. .env.example). Seules les valeurs NON sensibles
# (endpoints, ratios, ids de template) gardent un défaut en clair.
API_KEYS = {
    "deepinfra_api_key": os.getenv("DEEPINFRA_API_KEY"),
    "deepinfra_base_url": os.getenv(
        "DEEPINFRA_BASE_URL", "https://api.deepinfra.com/v1/openai"
    ),
    "google_tts_api_key": os.getenv("GOOGLE_TTS_API_KEY"),
    "creatomate_api_key": os.getenv("CREATOMATE_API_KEY"),
    "creatomate_url": "https://api.creatomate.com/v2/renders",
    "creatomate_subtitle_template_id": os.getenv(
        "CREATOMATE_SUBTITLE_TEMPLATE_ID", "5869abcc-052a-4f76-a04a-b157fe440ee5"
    ),
    # Veo3 API settings (Google's video generation)
    "veo3_api_key": os.getenv("VEO3_API_KEY"),
    "veo3_endpoint": "https://veo3.googleapis.com/v1/videos:generate",  # Update with actual endpoint
    # Google Custom Search API settings (for image search)
    "google_search_api_key": os.getenv("GOOGLE_SEARCH_API_KEY"),
    "google_search_cx": os.getenv("GOOGLE_SEARCH_CX"),
    # RunwayML API settings (for video generation)
    "runway_api_key": os.getenv("RUNWAY_API_KEY"),
    "runway_concurrency_limit": 1,
    "runway_video_ratio": "9:16",
    "runway_video_duration": 8,
    "runway_model": "gen4_turbo",
    # Pexels API settings (for stock video search)
    "pexels_api_key": os.getenv("PEXELS_API_KEY"),
}

# ========================
# GOOGLE CLOUD STORAGE
# ========================
GCS_CONFIG = {
    "json_key_path": "./api-key.json",
    "bucket_name": "content-bucket-charles-doux",
    "media_paths": {
        "articles": "media/articles/",  # Secteur: articles de presse
        # Ajoutez d'autres secteurs selon vos besoins:
        # "podcasts": "media/podcasts/",
        # "interviews": "media/interviews/",
    },
    "default_sector": "articles",  # Secteur par défaut
}


VIDEO_CONFIG = {
    "runway_model": "gen4_turbo",
    "runway_video_ratio": "720:1280",
    "runway_video_duration": 5,
    "video_source": "pexels",  # Options: "runway" (AI generation) or "pexels" (stock videos)
    "temp_dir": "temp_videos",  # Directory for temporary video downloads
}

# ========================
# VIDEO BACKEND (DeepInfra hébergé  vs  serveur LTX-2.3 local)
# ========================
# Deux flags INDÉPENDANTS pour router chaque type de plan :
#   USE_LTX_BROLL   -> les plans b-roll passent par le serveur LTX local (POST /generate)
#                      au lieu de Wan/DeepInfra.
#   USE_LTX_LIPSYNC -> les plans "tête parlante" passent par le serveur LTX local
#                      (POST /generate en image-to-video depuis le portrait, narration
#                      TTS gardée en piste audio) au lieu de Pruna/DeepInfra.
# Par défaut (False/False) : comportement actuel 100% DeepInfra, rien ne change.
# LTX_SERVER_URL : adresse du serveur LTX (cf. LTX-video-server, défaut port 8000).
VIDEO_BACKEND_CONFIG = {
    "use_ltx_broll": _env_bool("USE_LTX_BROLL", False),
    "use_ltx_lipsync": _env_bool("USE_LTX_LIPSYNC", False),
    "ltx_server_url": os.getenv("LTX_SERVER_URL", "http://localhost:8000"),
    # Timeout (s) des appels au serveur LTX : la génération est lente (2-3 min, +
    # pour la HDR / 4K). 1800s = 30 min de marge.
    "ltx_timeout": int(os.getenv("LTX_TIMEOUT", "1800")),
    # Résolution portrait 9:16 demandée au serveur LTX (arrondie à un multiple de 64
    # côté serveur). Alignée sur OUT_W/OUT_H de capabilities.py.
    "ltx_width": int(os.getenv("LTX_WIDTH", "720")),
    "ltx_height": int(os.getenv("LTX_HEIGHT", "1280")),
    "ltx_frame_rate": float(os.getenv("LTX_FRAME_RATE", "24")),
    # Passe HDR optionnelle sur le serveur LTX (double le temps de rendu).
    "ltx_hdr": _env_bool("LTX_HDR", False),
}

# ========================
# AI MODEL SETTINGS
# ========================
AI_CONFIG = {
    "model_name": "openai/gpt-oss-120b",
    "max_tokens": 64000,
    # La voix TTS n'est plus ici : elle est propagée depuis le channel
    # (models_config.voice_generator + context.characters[*].voice).
}

# ========================
# SCRAPER SETTINGS
# ========================
SCRAPER_CONFIG = {
    "cutoff_hours": 24,  # Look for articles older than this many hours
    "timeout": 20,  # Request timeout in seconds
}


# ========================
# GENERATED PATHS (DO NOT MODIFY)
# ========================
def get_generated_paths(media_domain=None, thematic=None, sector=None):
    """Generate dynamic paths based on configuration."""
    today_date = datetime.now().strftime("%Y-%m-%d")

    # Use provided params or fall back to single mode config
    if not media_domain:
        media_domain = PIPELINE_CONFIG.get("single_media_domain", "www.20minutes.fr")
    if not thematic:
        thematic = PIPELINE_CONFIG.get("single_thematic", "sport")
    if not sector:
        # Get sector from media_sources or use default
        sector = (
            PIPELINE_CONFIG.get("media_sources", {})
            .get(media_domain, {})
            .get("sector", GCS_CONFIG.get("default_sector", "articles"))
        )

    return {
        "scrape_url": f"https://{media_domain}/{thematic}/",
        "audio_gcs_path": f"media/{sector}/{media_domain}/{thematic}/{today_date}/audio.mp3",
        "local_audio_path": f"{PIPELINE_CONFIG['output_dir']}/{PIPELINE_CONFIG['audio_filename']}",
    }

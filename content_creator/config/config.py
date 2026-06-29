"""
Configuration file for the TikTok/Instagram content pipeline.
Modify the PIPELINE_CONFIG dictionary to change thematic and media settings.
"""

from datetime import datetime


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
API_KEYS = {
    "deepinfra_api_key": "7jIPsm1yv398SZpzLaE0qw2DIs2Y5CZG",
    "deepinfra_base_url": "https://api.deepinfra.com/v1/openai",
    "google_tts_api_key": "AIzaSyDD8i61OqNlRjgH7m1oCqQZen308jtvJmw",
    "creatomate_api_key": "83098d19134640efaf7da1bb70539437cd53351b2eb8b009ff435ffabbfd9b6b45ede9de692b8fdb6f55cf64053e448f",
    "creatomate_url": "https://api.creatomate.com/v2/renders",
    "creatomate_subtitle_template_id": "5869abcc-052a-4f76-a04a-b157fe440ee5",
    # Veo3 API settings (Google's video generation)
    "veo3_api_key": "AIzaSyD76bn26hclSlWL1JeBb-0gZXSTHqTGry8",  # Add your Veo3 API key here
    "veo3_endpoint": "https://veo3.googleapis.com/v1/videos:generate",  # Update with actual endpoint
    # Google Custom Search API settings (for image search)
    "google_search_api_key": "AIzaSyDD8i61OqNlRjgH7m1oCqQZen308jtvJmw",
    "google_search_cx": "e5ac4f1ee9fd646c1",
    # RunwayML API settings (for video generation)
    "runway_api_key": "key_e48b9d655fd970239a3750bf33746e3d51ec81ee99d80081ce29478aebd6bb4aaa145696fac1801de7b732c1acf3e13fb091469891791e037982182f8462ffdf",  # Add your RunwayML API key here
    "runway_concurrency_limit": 1,  # Tier 1: 1 concurrent task
    "runway_video_ratio": "9:16",  # Portrait format for TikTok/Instagram
    "runway_video_duration": 8,  # 8 seconds
    "runway_model": "gen4_turbo",
    # Pexels API settings (for stock video search)
    "pexels_api_key": "of0dud4qojl5BCoRziRhwyJJoMaJLhBdoUGFi0ShNEzPQZNlGROsZGl2",  # Your Pexels API key
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
# AI MODEL SETTINGS
# ========================
AI_CONFIG = {
    "model_name": "openai/gpt-oss-120b",
    "max_tokens": 64000,
    "tts_voice": {
        "language_code": "fr-FR",
        "name": "fr-FR-Chirp3-HD-Vindemiatrix",  # TODO : random choice a voice
        "speaking_rate": 1,
        "pitch": 0,
    },
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

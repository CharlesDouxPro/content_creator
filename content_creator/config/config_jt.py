"""
Configuration file for the Journal Télévisé (JT) video pipeline.
This is a separate config from the main pipeline to keep both modes independent.
"""

# ========================
# JT PIPELINE CONFIGURATION
# ========================
JT_CONFIG = {
    # Vidéos présentateur pré-générées sur GCS
    "presenter_videos": {
        "bucket": "content-bucket-charles-doux",
        "base_path": "media/presenter/",
        "videos": [
            "presenter_face_1.mp4",
            "presenter_face_2.mp4",
            "presenter_profile_left.mp4",
            "presenter_profile_right.mp4",
            "presenter_three_quarter.mp4",
        ],
        "max_segment_duration": 8,  # Durée max par plan (secondes)
    },
    # Configuration PiP overlay pour les images de contexte
    "pip_overlay": {
        "position": {"x": "65%", "y": "12%"},
        "size": {"width": "32%", "height": "auto"},
        "border_radius": "3%",
        "shadow": {"color": "rgba(0,0,0,0.4)", "blur": 8},
        "animation_in": {"type": "slide", "direction": "left", "duration": 0.4},
        "animation_out": {"type": "fade", "duration": 0.3},
    },
    # Configuration images de contexte
    "context_images": {
        "source": "google_images",
        "min_display_duration": 3.0,
        "max_display_duration": 8.0,
        "target_count": 5,  # Nombre cible d'images par vidéo
    },
    # LLM pour analyse du script (DeepInfra - GPT-OSS-120B)
    "script_analyzer": {
        "provider": "deepinfra",
        "model": "openai/gpt-oss-120b",
        "max_tokens": 2000,
    },
    # Outro settings (hérite de la config principale si non spécifié)
    "outro_duration": 4.0,
    "extra_audio_padding": 3.0,
}

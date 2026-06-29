"""
Modules specific to the Journal Télévisé (JT) video pipeline.
Contains ScriptAnalyzer, PresenterVideoManager, and JTVideoComposer.
"""

import json
import re
import requests
from dataclasses import dataclass
from typing import List, Dict, Optional
from openai import OpenAI

from config import API_KEYS
from config_jt import JT_CONFIG
from modules import GCSManager, VideoGenerator


@dataclass
class TimedImageSegment:
    """Segment temporel pour une image de contexte."""
    text: str                    # Portion du script associée
    start_time: float           # Début en secondes
    end_time: float             # Fin en secondes
    image_query: str            # Requête Google Images
    entities: List[str]         # Entités nommées (personnes, lieux)
    importance: str             # "high", "medium", "low"


class ScriptAnalyzer:
    """
    Analyse le script pour identifier les moments clés d'affichage d'images.
    Utilise DeepInfra (openai/gpt-oss-120b) pour une analyse sémantique intelligente.
    """

    def __init__(self):
        self.client = OpenAI(
            api_key=API_KEYS["deepinfra_api_key"],
            base_url=API_KEYS["deepinfra_base_url"],
        )
        self.model = JT_CONFIG["script_analyzer"]["model"]
        self.max_tokens = JT_CONFIG["script_analyzer"]["max_tokens"]

    def analyze_script(self, script: str, audio_length: float) -> List[TimedImageSegment]:
        """
        Analyse le script et retourne les segments temporels pour les images.

        Le LLM décide :
        - Quand afficher une image (position dans le script)
        - Quelle image chercher (requête optimisée)
        - Combien de temps l'afficher

        Args:
            script: Le texte du script de narration
            audio_length: Durée totale de l'audio en secondes

        Returns:
            Liste de TimedImageSegment avec les informations de timing
        """
        target_count = JT_CONFIG["context_images"]["target_count"]
        min_duration = JT_CONFIG["context_images"]["min_display_duration"]
        max_duration = JT_CONFIG["context_images"]["max_display_duration"]

        prompt = f"""Analyse ce script de narration pour une vidéo TikTok/Instagram au format "Journal Télévisé".

SCRIPT (durée audio: {audio_length:.1f} secondes):
---
{script}
---

MISSION:
Identifie les {target_count-1} à {target_count+2} moments clés où une image de contexte devrait apparaître en superposition (Picture-in-Picture).

Pour chaque moment, fournis:
1. "text": La phrase ou portion du script concernée (COPIE EXACTE du texte, mot pour mot)
2. "image_query": Une requête Google Images optimisée (2-5 mots, en français)
3. "entities": Liste des entités nommées mentionnées (personnes, lieux, événements)
4. "importance": "high" (personne/lieu clé), "medium" (contexte utile), "low" (illustration)
5. "suggested_duration": Durée d'affichage suggérée ({min_duration}-{max_duration} secondes)

RÈGLES STRICTES:
- Le champ "text" DOIT être une copie EXACTE d'une portion du script (pour permettre la localisation)
- Priorise les entités nommées (personnes célèbres, lieux, événements sportifs)
- Espace les images pour ne pas surcharger visuellement (minimum 5 secondes entre chaque)
- Les requêtes doivent être concrètes et visuelles (noms propres, lieux, objets)
- Évite les concepts abstraits difficiles à illustrer
- N'inclus PAS d'images pour l'intro ou l'outro

Retourne UNIQUEMENT un JSON valide (pas de markdown, pas de texte avant ou après):
{{
  "segments": [
    {{
      "text": "phrase exacte du script",
      "image_query": "requête google images",
      "entities": ["entité1"],
      "importance": "high",
      "suggested_duration": 5.0
    }}
  ]
}}"""

        try:
            response = self.client.chat.completions.create(
                model=self.model,
                max_tokens=self.max_tokens,
                messages=[{"role": "user", "content": prompt}]
            )

            response_text = response.choices[0].message.content.strip()

            # Clean up potential markdown formatting
            response_text = re.sub(r'^```json\s*', '', response_text)
            response_text = re.sub(r'\s*```$', '', response_text)

            # Parse JSON
            segments_data = json.loads(response_text)

            # Calculate timestamps
            return self._calculate_timestamps(script, segments_data["segments"], audio_length)

        except json.JSONDecodeError as e:
            print(f"   [ScriptAnalyzer] JSON parsing error: {e}")
            print(f"   Response was: {response_text[:500]}...")
            return []
        except Exception as e:
            print(f"   [ScriptAnalyzer] Error analyzing script: {e}")
            return []

    def _calculate_timestamps(
        self, script: str, segments: List[dict], audio_length: float
    ) -> List[TimedImageSegment]:
        """
        Calcule les timestamps basés sur la position du texte dans le script.

        Utilise une approche proportionnelle : position_texte / longueur_totale * durée_audio
        """
        total_chars = len(script)
        timed_segments = []
        min_duration = JT_CONFIG["context_images"]["min_display_duration"]
        max_duration = JT_CONFIG["context_images"]["max_display_duration"]

        for seg in segments:
            text = seg.get("text", "")
            if not text:
                continue

            # Trouver la position du segment dans le script
            pos = script.find(text)
            if pos == -1:
                # Try fuzzy match - find the most similar substring
                print(f"      Warning: Could not find exact text match for: '{text[:50]}...'")
                # Try to find a partial match
                words = text.split()[:5]  # First 5 words
                partial_text = " ".join(words)
                pos = script.find(partial_text)
                if pos == -1:
                    print("      Skipping segment - no match found")
                    continue

            # Convertir position en timestamp (proportionnel)
            start_time = (pos / total_chars) * audio_length

            # Durée suggérée avec contraintes
            suggested_duration = seg.get("suggested_duration", 5.0)
            duration = max(min_duration, min(suggested_duration, max_duration))
            end_time = min(start_time + duration, audio_length)

            timed_segments.append(TimedImageSegment(
                text=text,
                start_time=round(start_time, 2),
                end_time=round(end_time, 2),
                image_query=seg.get("image_query", ""),
                entities=seg.get("entities", []),
                importance=seg.get("importance", "medium")
            ))

        # Sort by start time
        timed_segments.sort(key=lambda x: x.start_time)

        return timed_segments


class PresenterVideoManager:
    """
    Gère les vidéos de présentateur pré-générées stockées sur GCS.
    Construit la timeline en alternant entre les différents plans.
    """

    def __init__(self, config: dict = None):
        self.config = config or JT_CONFIG
        self.gcs_manager = GCSManager()

    def get_presenter_urls(self) -> List[str]:
        """
        Récupère les URLs des vidéos présentateur depuis GCS.

        Returns:
            Liste des URLs publiques des vidéos présentateur
        """
        base_path = self.config["presenter_videos"]["base_path"]
        bucket = self.config["presenter_videos"]["bucket"]

        urls = []
        for video_name in self.config["presenter_videos"]["videos"]:
            # Construire l'URL publique GCS
            url = f"https://storage.googleapis.com/{bucket}/{base_path}{video_name}"
            urls.append(url)

        return urls

    def verify_presenter_videos_exist(self) -> Dict[str, bool]:
        """
        Vérifie que les vidéos présentateur existent sur GCS.

        Returns:
            Dict mapping video name -> exists (bool)
        """
        base_path = self.config["presenter_videos"]["base_path"]
        results = {}

        for video_name in self.config["presenter_videos"]["videos"]:
            blob_path = f"{base_path}{video_name}"
            exists = self.gcs_manager.check_blob_exists(blob_path)
            results[video_name] = exists
            if not exists:
                print(f"   Warning: Presenter video not found: {blob_path}")

        return results

    def build_presenter_timeline(self, audio_length: float) -> List[dict]:
        """
        Construit la timeline des plans présentateur pour couvrir la durée audio.
        Alterne entre les différents plans disponibles.

        Args:
            audio_length: Durée totale de l'audio en secondes

        Returns:
            Liste de segments avec url, start_time, duration
        """
        urls = self.get_presenter_urls()
        if not urls:
            raise ValueError("No presenter videos configured")

        max_segment = self.config["presenter_videos"]["max_segment_duration"]

        timeline = []
        current_time = 0.0
        video_index = 0

        while current_time < audio_length:
            # Durée du segment (max_segment ou ce qu'il reste)
            duration = min(max_segment, audio_length - current_time)

            timeline.append({
                "url": urls[video_index % len(urls)],
                "start_time": round(current_time, 2),
                "duration": round(duration, 2),
            })

            current_time += duration
            video_index += 1

        return timeline


class JTVideoComposer:
    """
    Compose la vidéo finale avec Creatomate.
    Gère les tracks : audio, présentateur (fond), images PiP (overlay), outro.
    """

    def __init__(self, config: dict = None):
        self.config = config or JT_CONFIG
        self.api_key = API_KEYS["creatomate_api_key"]
        self.url = API_KEYS["creatomate_url"]

    def build_creatomate_payload(
        self,
        audio_url: str,
        audio_length: float,
        presenter_timeline: List[dict],
        context_images: List[dict],
        outro_config: dict,
        outro_duration: float = None,
        extra_audio_padding: float = None,
    ) -> dict:
        """
        Construit le payload JSON pour Creatomate.

        Args:
            audio_url: URL de l'audio sur GCS
            audio_length: Durée de l'audio en secondes
            presenter_timeline: Liste des segments présentateur
            context_images: Liste des images de contexte avec timing
            outro_config: Configuration de l'outro (brand, contact_name)
            outro_duration: Durée de l'outro
            extra_audio_padding: Padding audio supplémentaire

        Returns:
            Payload JSON prêt pour l'API Creatomate
        """
        outro_duration = outro_duration or self.config.get("outro_duration", 4.0)
        extra_audio_padding = extra_audio_padding or self.config.get("extra_audio_padding", 3.0)
        pip_config = self.config["pip_overlay"]

        elements = []

        # Track 1: Audio
        elements.append({
            "type": "audio",
            "track": 1,
            "time": 0,
            "duration": audio_length + extra_audio_padding,
            "source": audio_url,
            "loop": False,
            "audio_fade_out": 2,
        })

        # Track 2: Vidéos présentateur (fond plein écran)
        for i, segment in enumerate(presenter_timeline):
            element = {
                "name": f"Presenter-{i+1}",
                "type": "video",
                "track": 2,
                "time": segment["start_time"],
                "duration": segment["duration"],
                "source": segment["url"],
                "volume": "0%",  # Mute presenter video
            }
            # Transition fade entre les plans (sauf le premier)
            if i > 0:
                element["animations"] = [
                    {"time": 0, "duration": 0.5, "transition": True, "type": "fade"}
                ]
            elements.append(element)

        # Track 3: Images de contexte en PiP (overlay)
        for i, img in enumerate(context_images):
            elements.append({
                "name": f"Context-{i+1}",
                "type": "image",
                "track": 3,
                "time": img["start_time"],
                "duration": img["duration"],
                "source": img["url"],
                "x": pip_config["position"]["x"],
                "y": pip_config["position"]["y"],
                "width": pip_config["size"]["width"],
                "height": pip_config["size"].get("height", "auto"),
                "border_radius": pip_config.get("border_radius", "0%"),
                "shadow_color": pip_config["shadow"]["color"],
                "shadow_blur": pip_config["shadow"]["blur"],
                "animations": [
                    {
                        "time": 0,
                        "duration": pip_config["animation_in"]["duration"],
                        "type": pip_config["animation_in"]["type"],
                        "direction": pip_config["animation_in"].get("direction", "left"),
                    },
                    {
                        "time": "end",
                        "duration": pip_config["animation_out"]["duration"],
                        "type": pip_config["animation_out"]["type"],
                        "fade_out": True,
                    },
                ],
            })

        # Track 4: Outro
        outro_time = max(0.0, audio_length - outro_duration)
        elements.append(self._create_outro_element(outro_time, outro_duration, outro_config))

        return {
            "output_format": "mp4",
            "width": 720,
            "height": 1280,
            "elements": elements,
        }

    def _create_outro_element(
        self, outro_time: float, outro_duration: float, outro_config: dict
    ) -> dict:
        """
        Crée l'élément outro (identique à la version originale de modules.py).
        """
        return {
            "name": "Outro",
            "type": "composition",
            "track": 4,
            "time": outro_time,
            "duration": outro_duration,
            "fill_color": "rgba(255,255,255,1)",
            "animations": [
                {"time": 0, "duration": 1, "transition": True, "type": "fade"}
            ],
            "elements": [
                {
                    "type": "text",
                    "track": 1,
                    "time": 0,
                    "x": "24.3774%",
                    "y": "60.795%",
                    "width": "51.2453%",
                    "height": "6.9826%",
                    "x_anchor": "0%",
                    "y_anchor": "100%",
                    "y_alignment": "100%",
                    "fill_color": "rgba(0,0,0,1)",
                    "text": outro_config.get("brand", "My Brand"),
                    "font_family": "Inter",
                    "background_border_radius": "39%",
                    "animations": [
                        {
                            "time": 0,
                            "duration": 1.24,
                            "easing": "quadratic-out",
                            "type": "text-slide",
                            "scope": "split-clip",
                            "split": "line",
                            "background_effect": "scaling-clip",
                        }
                    ],
                },
                {
                    "name": "Contact-Name",
                    "type": "text",
                    "track": 2,
                    "time": 0,
                    "x": "24.3774%",
                    "y": "52.7013%",
                    "width": "51.2453%",
                    "height": "13.4963%",
                    "x_anchor": "0%",
                    "y_anchor": "100%",
                    "y_alignment": "100%",
                    "fill_color": "rgba(0,0,0,1)",
                    "text": outro_config.get("contact_name", "Contact Name"),
                    "font_family": "Inter",
                    "font_weight": "800",
                    "background_border_radius": "39%",
                    "animations": [
                        {
                            "time": 0,
                            "duration": 1.24,
                            "easing": "quadratic-out",
                            "type": "text-slide",
                            "scope": "split-clip",
                            "split": "line",
                            "background_effect": "scaling-clip",
                        }
                    ],
                },
            ],
        }

    def render_video(self, payload: dict) -> Optional[dict]:
        """
        Envoie le payload à Creatomate pour le rendu.

        Args:
            payload: Payload JSON pour Creatomate

        Returns:
            Response dict avec id, status, url ou None si erreur
        """
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }

        try:
            response = requests.post(
                self.url, json=payload, headers=headers, timeout=60
            )
            response.raise_for_status()
            return response.json()
        except Exception as e:
            print(f"   [JTVideoComposer] Render error: {e}")
            return None


class ContextImageFetcher:
    """
    Récupère les images de contexte depuis Google Images.
    Réutilise la logique existante de VideoGenerator.
    """

    def __init__(self):
        self.api_key = API_KEYS["google_search_api_key"]
        self.cx = API_KEYS["google_search_cx"]

    def fetch_images(
        self, segments: List[TimedImageSegment], gcs_base_path: str = None
    ) -> List[dict]:
        """
        Récupère les images de contexte pour chaque segment.

        Args:
            segments: Liste des segments avec requêtes d'images
            gcs_base_path: Chemin GCS pour archiver les images (optionnel)

        Returns:
            Liste de dicts avec url, start_time, duration
        """
        context_images = []

        for i, seg in enumerate(segments):
            print(f"      Image {i+1}: '{seg.image_query}' ({seg.importance})")

            # Utilise la méthode existante
            image_url = VideoGenerator.google_image_search(
                query=seg.image_query,
                api_key=self.api_key,
                cx=self.cx,
                num_results=3,  # Get a few results to filter
            )

            if image_url:
                context_images.append({
                    "url": image_url,
                    "start_time": seg.start_time,
                    "duration": seg.end_time - seg.start_time,
                    "query": seg.image_query,
                    "importance": seg.importance,
                })
                print(f"         Found: {image_url[:60]}...")
            else:
                print(f"         No image found for: {seg.image_query}")

        return context_images

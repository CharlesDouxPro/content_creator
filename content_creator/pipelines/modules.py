"""
Core modules for the TikTok/Instagram content pipeline.
Contains all the scraping, AI processing, and video generation logic.
"""

import re
import os
import time
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Literal, Dict
from pydantic import BaseModel, HttpUrl
import json
from openai import OpenAI
import base64
import asyncio
from mutagen.mp3 import MP3
import mimetypes
from google.cloud import storage
from runwayml import AsyncRunwayML
from content_creator.config.config import API_KEYS, AI_CONFIG, VIDEO_CONFIG, GCS_CONFIG, SCRAPER_CONFIG


# --- Data Models ---
class ArticleBlock(BaseModel):
    title: str
    content: str
    images: List[HttpUrl] = []


class VeoPrompt(BaseModel):
    prompt: str
    entities: list[str]


class VideoQuery(BaseModel):
    index: Dict[str, VeoPrompt]  # Keys are strings in JSON, not ints


class RenderResponse(BaseModel):
    id: str
    status: str
    url: HttpUrl
    output_format: str


class ArticleLink(BaseModel):
    href: HttpUrl
    title: str
    published_at: datetime


class FullArticle(BaseModel):
    link: ArticleLink
    content: List[ArticleBlock]


# --- News Scraper ---
class NewsScraper:
    PARIS = timezone(timedelta(hours=2))  # Europe/Paris

    MOIS = {
        "janv": 1,
        "févr": 2,
        "fevr": 2,
        "mars": 3,
        "avr": 4,
        "mai": 5,
        "juin": 6,
        "juil": 7,
        "août": 8,
        "aout": 8,
        "sept": 9,
        "oct": 10,
        "nov": 11,
        "déc": 12,
        "dec": 12,
        "janvier": 1,
        "février": 2,
        "fevrier": 2,
        "avril": 4,
        "juillet": 7,
        "septembre": 9,
        "octobre": 10,
        "novembre": 11,
        "décembre": 12,
        "decembre": 12,
    }

    REL_RE = re.compile(
        r"^\s*il y a\s+(\d+)\s+(minute|minutes|heure|heures|jour|jours)\s*$", re.I
    )
    ABS_RE = re.compile(
        r"^\s*(\d{1,2})\s+([A-Za-zéèêûîôàâùïëç\.]+)\s+(\d{4})(?:\s+à\s+(\d{1,2})h(\d{2})?)?\s*$",
        re.I,
    )

    def __init__(self):
        self.NOW = datetime.now(self.PARIS)
        self.CUTOFF = self.NOW - timedelta(hours=SCRAPER_CONFIG["cutoff_hours"])

    @classmethod
    def parse_french_time_label(cls, txt: str, now: datetime) -> Optional[datetime]:
        txt = (txt or "").strip()
        m = cls.REL_RE.match(txt)
        if m:
            n = int(m.group(1))
            unit = m.group(2).lower()
            if unit.startswith("minute"):
                return now - timedelta(minutes=n)
            elif unit.startswith("heure"):
                return now - timedelta(hours=n)
            elif unit.startswith("jour"):
                return now - timedelta(days=n)
            return None

        m = cls.ABS_RE.match(txt.replace(" ", " "))
        if m:
            day = int(m.group(1))
            mois_raw = m.group(2).lower().replace(".", "")
            year = int(m.group(3))
            hh = int(m.group(4)) if m.group(4) else 0
            mm = int(m.group(5)) if m.group(5) else 0

            mois_raw = (
                mois_raw.replace("févr", "fevr")
                .replace("août", "aout")
                .replace("déc", "dec")
            )
            month = cls.MOIS.get(mois_raw) or cls.MOIS.get(mois_raw[:4])
            if not month:
                return None

            return datetime(year, month, day, hh, mm, tzinfo=cls.PARIS)
        return None

    def scrape_links_older_than_24h(self, url: str) -> List[ArticleLink]:
        r = requests.get(url, timeout=SCRAPER_CONFIG["timeout"])
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")

        results = []
        for article in soup.select("article"):
            time_el = article.select_one("time")
            a_el = article.select_one("h2 a, h3 a, a")

            if not time_el or not a_el or not a_el.get("href"):
                continue

            label = time_el.get_text(strip=True)
            dt = self.parse_french_time_label(label, self.NOW)
            if not dt:
                continue

            if dt < self.CUTOFF:
                try:
                    results.append(
                        ArticleLink(
                            href=a_el["href"],
                            title=a_el.get_text(strip=True),
                            published_at=dt,
                        )
                    )
                except Exception as e:
                    print(f"Validation error: {e}")

        return results

    def scrape_article(self, url: str) -> List[ArticleBlock]:
        try:
            r = requests.get(url, timeout=SCRAPER_CONFIG["timeout"])
            r.raise_for_status()
        except requests.RequestException as e:
            print(f"[Erreur réseau] Impossible de charger l'URL {url}: {e}")
            return []

        soup = BeautifulSoup(r.text, "html.parser")

        # Try multiple selector variants
        main_div = soup.select_one("div.c-content.c-content--theme-ultramarine")
        if not main_div:
            main_div = soup.select_one("div.c-content")
        if not main_div:
            print(f"[Erreur parsing] Impossible de trouver la div de contenu sur {url}")
            return []

        results = []
        current_title = "Introduction"
        buffer = []
        images = []

        for el in main_div.find_all(["h2", "p", "img"], recursive=True):
            if el.name == "h2":
                if buffer or images:
                    try:
                        results.append(
                            ArticleBlock(
                                title=current_title,
                                content=" ".join(buffer).strip(),
                                images=images,
                            )
                        )
                    except Exception as e:
                        print(f"[Erreur validation bloc] {e}")
                    buffer, images = [], []
                current_title = el.get_text(strip=True)

            elif el.name == "p":
                text = el.get_text(" ", strip=True)
                if text:
                    buffer.append(text)

            elif el.name == "img":
                src = el.get("src") or el.get("data-src")
                if src:
                    images.append(src)

        if buffer or images:
            try:
                results.append(
                    ArticleBlock(
                        title=current_title,
                        content=" ".join(buffer).strip(),
                        images=images,
                    )
                )
            except Exception as e:
                print(f"[Erreur validation dernier bloc] {e}")

        return results


# --- Article Summarizer ---
class ArticleSummarizer:
    def __init__(self, model_config: dict = None):
        """`model_config` = ModelConfig {model_name, provider{base_url, token}} du
        channel (rôle `slm`). Sans config -> globals du .env (comportement historique)."""
        if model_config:
            provider = model_config["provider"]
            self.client = OpenAI(api_key=provider["token"], base_url=provider["base_url"])
            self.model_name = model_config["model_name"]
        else:
            self.client = OpenAI(
                api_key=API_KEYS["deepinfra_api_key"],
                base_url=API_KEYS["deepinfra_base_url"],
            )
            self.model_name = AI_CONFIG["model_name"]

    @classmethod
    def generate_prompt(
        cls,
        content: str,
        usage: Literal["summary", "pexel"],
        video_time: int = 0,
        video_source: str = "runway",
    ) -> List[dict]:
        """Generate prompt for the language model."""
        if usage == "summary":
            prompt = [
                {
                    "role": "system",
                    "content": (
                        "Tu es un expert en storytelling digital. "
                        "Ta mission est de transformer des articles d'actualité en scripts courts, captivants et adaptés "
                        "à une lecture à voix haute sur TikTok ou Instagram. "
                        "Le style doit être clair, direct, accrocheur et donner envie d'en savoir plus. "
                        "La durée de lecture finale ne doit pas dépasser une minute (~120 à 150 mots). "
                        "Ignore les images et concentre-toi uniquement sur le texte fourni."
                        "Ne met pas d'emoji dans le script."
                        "N'ajoute pas d'informations type [fin du script]"
                        "Ajoute abonne toi pour plus de contenu à la fin du script."
                        "Met un maximum de 20 mots par phrase."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        "Voici le contenu d'un article découpé en sections (titre + contenu). "
                        "Résume-le en un script narratif percutant pour TikTok/Instagram, sans dépasser une minute :\n\n"
                        f"content: {content}\n"
                    ),
                },
            ]
        elif usage == "pexel":
            # Adapt prompt based on video source
            if video_source == "pexels":
                # Prompt optimized for searching stock videos on Pexels
                prompt = [
                    {
                        "role": "system",
                        "content": (
                            "You are an expert in visual storytelling and stock video selection.\n\n"
                            "OBJECTIVE:\n"
                            "Generate a structured Python dictionary with search queries to find relevant stock videos on Pexels.\n\n"
                            "EXPECTED STRUCTURE:\n"
                            "{\n"
                            '  "1": {\n'
                            '    "prompt": "concise search query for stock videos",\n'
                            '    "entities": ["Name1", "Name2"]\n'
                            "  },\n"
                            '  "2": { ... }\n'
                            "}\n\n"
                            "STRICT RULES FOR PEXELS SEARCH:\n"
                            '1. Structure: Each numeric key ("1", "2", "3"...) contains an object with:\n'
                            '   - "prompt": SHORT search query (2-5 keywords) that will find relevant stock footage\n'
                            '   - "entities": list of 0 to 2 relevant proper nouns (people, places, events) quoted in the context\n\n'
                            "2. Search query optimization:\n"
                            "   - Use SIMPLE, GENERIC terms that match common stock video categories\n"
                            "   - Focus on VISUAL CONCEPTS: actions, settings, moods, objects\n"
                            "   - Avoid specific names, dates, or overly detailed descriptions\n"
                            "   - Think about what stock videos are available: sports, nature, city, business, technology, etc.\n"
                            "   - Examples: 'soccer match', 'mountain sunrise', 'office meeting', 'technology innovation'\n\n"
                            "3. Diversity:\n"
                            "   - Vary the visual themes across different scenes\n"
                            "   - Each query should target different footage types\n\n"
                            "4. Named entities:\n"
                            "   - Extract proper nouns mentioned in the narration\n"
                            "   - These help contextualize but won't be in the Pexels search\n"
                            "   - Limit to 2 entities maximum per scene\n\n"
                            "5. Output format:\n"
                            "   - Return ONLY valid JSON, with no text before or after\n"
                            "   - No markdown, no explanation, no comments\n"
                            "   - Verify JSON syntax (quotes, commas, brackets)\n\n"
                            "EXAMPLES OF GOOD PEXELS QUERIES:\n"
                            '- "soccer ball goal" (not "Messi scores winning goal at World Cup final")\n'
                            '- "business handshake" (not "CEO signs merger agreement in boardroom")\n'
                            '- "mountain hiking" (not "Climbers ascending Mont Blanc at dawn")\n'
                            '- "city traffic night" (not "Rush hour in Tokyo with neon lights")\n'
                        ),
                    },
                    {
                        "role": "user",
                        "content": f"Narration to transform into Pexels search queries:\n\n{content}\n\nGenerate the structured JSON dictionary.",
                    },
                ]
            else:
                # Prompt optimized for AI video generation (Runway, Veo3, etc.)
                prompt = [
                    {
                        "role": "system",
                        "content": (
                            "You are an expert in visual storytelling and artistic direction for video creation.\n\n"
                            "OBJECTIVE:\n"
                            "Generate a structured Python dictionary to orchestrate narrative video creation with AI video generators.\n\n"
                            "EXPECTED STRUCTURE:\n"
                            "{\n"
                            '  "1": {\n'
                            '    "prompt": "detailed visual description of the scene",\n'
                            '    "entities": ["Name1", "Name2"]\n'
                            "  },\n"
                            '  "2": { ... }\n'
                            "}\n\n"
                            "STRICT RULES:\n"
                            '1. Structure: Each numeric key ("1", "2", "3"...) contains an object with:\n'
                            '   - "prompt": video edit visual description (50-100 words)\n'
                            '   - "entities": list of 0 to 2 relevant proper nouns (people, places, events) quoted in the context\n\n'
                            "2. Visual prompt quality:\n"
                            "   - Describe the ACTION, EMOTION, and ATMOSPHERE of each scene\n"
                            "   - Include sensory details: lighting, colors, movement, framing\n"
                            "   - Use varied cinematographic vocabulary\n"
                            "3. Lexical diversity MANDATORY:\n"
                            "   - Vary action verbs, descriptive adjectives, and visual references\n"
                            "   - If a concept must recur, completely rephrase it\n\n"
                            "4. Named entities:\n"
                            "   - Extract only proper nouns explicitly mentioned in the narration\n"
                            "   - Prioritize people and places that are visually searchable\n"
                            "   - Limit to 2 entities maximum per scene to stay focused\n\n"
                            "5. Output format:\n"
                            "   - Return ONLY valid JSON, with no text before or after\n"
                            "   - No markdown, no explanation, no comments\n"
                            "   - Verify JSON syntax (quotes, commas, brackets)\n\n"
                            "EXAMPLE OF QUALITY VISUAL PROMPT:\n"
                            '"Wide aerial shot at dusk over snow-covered mountains. Golden sunlight illuminates the peaks '
                            "as a solitary figure trudges through deep snow. Contemplative and majestic atmosphere, "
                            'stabilized camera fluidly following the movement."\n'
                        ),
                    },
                    {
                        "role": "user",
                        "content": f"Narration to transform into visual sequence:\n\n{content}\n\nGenerate the structured JSON dictionary.",
                    },
                ]
        return prompt

    def summarize_article(self, article: FullArticle, mood: str = None) -> Optional[str]:
        """Use the language model to summarize an article. `mood` tunes the script's tone."""
        content = json.dumps(
            article.model_dump(), ensure_ascii=False, indent=2, default=str
        )
        prompt = self.generate_prompt(content, usage="summary")
        if mood:
            prompt.append({
                "role": "system",
                "content": f"Écris le script avec ce ton / cette ambiance : {mood}.",
            })

        response = self.client.chat.completions.create(
            model=self.model_name,
            max_tokens=AI_CONFIG["max_tokens"],
            messages=prompt,
        )
        response_text = response.choices[0].message.content
        clean_text = self.clean_text(response_text).strip()
        return clean_text

    def generate_title(self, text: str) -> str:
        """Infère un titre court et accrocheur (sert à nommer le fichier vidéo)."""
        try:
            resp = self.client.chat.completions.create(
                model=self.model_name,
                max_tokens=1000,   # gpt-oss raisonne : budget suffisant sinon contenu vide
                messages=[
                    {"role": "system", "content": (
                        "Tu génères un titre court (3 à 8 mots), accrocheur, sans guillemets, "
                        "sans ponctuation finale, sans emoji. Réponds UNIQUEMENT par le titre."
                    )},
                    {"role": "user", "content": f"Texte de la vidéo :\n{text}\n\nTitre :"},
                ],
            )
            title = self.clean_text(resp.choices[0].message.content or "").strip()
            return title or "video"
        except Exception as e:
            print(f"[generate_title] {e}")
            return "video"

    def write_video_queries(
        self, content: str, video_time: int
    ) -> Optional[VideoQuery]:
        """Use the language model to generate video prompts."""
        # Get video source from config to adapt the prompt
        from content_creator.config.config import VIDEO_CONFIG

        video_source = VIDEO_CONFIG.get("video_source", "runway")

        prompt = self.generate_prompt(
            content, usage="pexel", video_time=video_time, video_source=video_source
        )

        response = self.client.chat.completions.create(
            model=self.model_name,
            max_tokens=AI_CONFIG["max_tokens"],
            messages=prompt,
        )
        response_text = response.choices[0].message.content
        clean_text = self.clean_text(response_text).strip()

        # Parse JSON response and validate with VideoQuery model
        try:
            raw_data = json.loads(
                clean_text.replace("```json\n", "").replace("\n```", "")
            )
            return VideoQuery(index=raw_data)
        except (json.JSONDecodeError, ValueError) as e:
            print(f"Failed to parse JSON or validate VideoQuery: {e}")
            print(f"Raw response: {clean_text}")
            return None

    @classmethod
    def clean_text(cls, text: str) -> str:
        text = re.sub(r"\*\*Script\s*:\*\*\s*", "", text, flags=re.IGNORECASE).strip()
        # Remove emojis
        emoji_pattern = re.compile(
            "["
            "\U0001f600-\U0001f64f"
            "\U0001f300-\U0001f5ff"
            "\U0001f680-\U0001f6ff"
            "\U0001f1e0-\U0001f1ff"
            "\U00002700-\U000027bf"
            "\U0001f900-\U0001f9ff"
            "\U00002600-\U000026ff"
            "]+",
            flags=re.UNICODE,
        )
        return emoji_pattern.sub("", text)

    def text_to_speech_google(
        self, text: str, output_file: str = "output.mp3",
        voice: str = None, api_key: str = None, base_url: str = None,
        style: str = None, voice_model: str = None, language: str = None,
    ) -> Optional[str]:
        """Convert text to speech using Google TTS. Tout est PROPAGÉ depuis le channel
        (voice_generator / characters) ; aucune valeur globale.

        Deux modes selon `voice_model` :
        - Gemini TTS (`voice_model` fourni, ex. 'gemini-3.1-flash-tts-preview') : supporte le
          `style` (instructions de ton via `input.prompt`), endpoint v1beta1, `voice.name` court
          (ex. 'Achernar'), `language` explicite (ex. 'fr-FR').
        - Chirp3-HD (défaut) : nom complet (ex. 'fr-FR-Chirp3-HD-Charon'), langue déduite du nom,
          pas de `style` ni de `pitch`."""
        try:
            if not voice or not api_key:
                print("[Erreur TTS] voix ou clé manquante (à propager depuis le channel "
                      "voice_generator / characters)")
                return None

            if voice_model:
                # --- Gemini TTS : style/prompt supporté, endpoint v1beta1 ---
                input_obj = {"text": text}
                if style:
                    input_obj["prompt"] = style
                body = {
                    "audioConfig": {"audioEncoding": "MP3", "speakingRate": 1.0},
                    "input": input_obj,
                    "voice": {"languageCode": language or "en-US", "name": voice,
                              "modelName": voice_model},
                }
                root = "https://texttospeech.googleapis.com/v1beta1"
            else:
                # --- Chirp3-HD : langue déduite du nom, pas de prompt ni pitch ---
                parts = voice.split("-")
                language_code = language or ("-".join(parts[:2]) if len(parts) >= 2 else "fr-FR")
                body = {
                    "audioConfig": {
                        "audioEncoding": "MP3",
                        "effectsProfileId": ["large-home-entertainment-class-device"],
                        "speakingRate": 1.0,
                    },
                    "input": {"text": text},
                    "voice": {"languageCode": language_code, "name": voice},
                }
                root = (base_url or "https://texttospeech.googleapis.com/v1").rstrip("/")

            url = f"{root}/text:synthesize?key={api_key}"
            headers = {"Content-Type": "application/json"}

            response = requests.post(url, headers=headers, json=body, timeout=30)
            response.raise_for_status()

            data = response.json()
            if "audioContent" not in data:
                print(f"[Erreur TTS] Pas de contenu audio dans la réponse: {data}")
                return None

            audio_bytes = base64.b64decode(data["audioContent"])

            with open(output_file, "wb") as f:
                f.write(audio_bytes)

            print(f"[OK] Fichier audio enregistré : {output_file}")
            return output_file

        except requests.exceptions.RequestException as e:
            print(f"[Erreur réseau TTS] {e}")
            return None

    def text_to_speech_elevenlabs(
        self, text: str, output_file: str = "output.mp3",
        voice: str = None, api_key: str = None, model: str = None, base_url: str = None,
    ) -> Optional[str]:
        """Synthèse vocale via ElevenLabs. Tout est PROPAGÉ depuis le channel :
        `voice` = voice_id ElevenLabs (character.voice ou voice_generator.model_name par défaut),
        `api_key` = ELEVENLABS_API_KEY, `model` = model_id (défaut 'eleven_multilingual_v2',
        multilingue, gère le français). Réponse = MP3 binaire écrit tel quel."""
        try:
            if not voice or not api_key:
                print("[Erreur TTS] voice_id ou clé ElevenLabs manquant(e) (à propager depuis le "
                      "channel voice_generator / characters)")
                return None
            root = (base_url or "https://api.elevenlabs.io").rstrip("/")
            url = f"{root}/v1/text-to-speech/{voice}"
            headers = {"xi-api-key": api_key, "Content-Type": "application/json",
                       "Accept": "audio/mpeg"}
            body = {"text": text, "model_id": model or "eleven_multilingual_v2"}
            # Retry/backoff sur 429 (limite de concurrence ElevenLabs : les plans sont rendus
            # en parallèle). On respecte `retry-after` si fourni, sinon backoff exponentiel.
            response = None
            for attempt in range(6):
                response = requests.post(url, headers=headers, json=body, timeout=60)
                if response.status_code != 429:
                    break
                wait = float(response.headers.get("retry-after") or min(2 ** attempt, 20))
                print(f"[TTS ElevenLabs] 429 (concurrence) — retry dans {wait:.0f}s "
                      f"(tentative {attempt + 1}/6)")
                time.sleep(wait)
            response.raise_for_status()
            with open(output_file, "wb") as f:
                f.write(response.content)
            print(f"[OK] Fichier audio enregistré (ElevenLabs) : {output_file}")
            return output_file
        except requests.exceptions.RequestException as e:
            print(f"[Erreur réseau TTS ElevenLabs] {e}")
            return None


# --- Google Cloud Storage Manager ---
class GCSManager:
    def __init__(self):
        self.json_key_path = GCS_CONFIG["json_key_path"]
        self.bucket_name = GCS_CONFIG["bucket_name"]

    def check_blob_exists(self, blob_path: str) -> bool:
        """Check if a blob exists in Google Cloud Storage."""
        try:
            client = storage.Client.from_service_account_json(self.json_key_path)
            bucket = client.bucket(self.bucket_name)
            blob = bucket.blob(blob_path)
            return blob.exists()
        except Exception as e:
            print(f"[Check Error] {e}")
            return False

    def list_blobs_with_prefix(self, prefix: str) -> List[str]:
        """List all blobs with a given prefix."""
        try:
            client = storage.Client.from_service_account_json(self.json_key_path)
            bucket = client.bucket(self.bucket_name)
            blobs = bucket.list_blobs(prefix=prefix)
            return [blob.name for blob in blobs]
        except Exception as e:
            print(f"[List Error] {e}")
            return []

    def upload_file(
        self, local_path: str, dest_path: str, make_public: bool = True
    ) -> Optional[Dict]:
        """Upload a file to Google Cloud Storage."""
        try:
            client = storage.Client.from_service_account_json(self.json_key_path)
            bucket = client.bucket(self.bucket_name)

            if not bucket.exists():
                raise RuntimeError(
                    f"Bucket introuvable ou non accessible: {self.bucket_name}"
                )

            blob = bucket.blob(dest_path)

            # Determine content type
            ctype, _ = mimetypes.guess_type(local_path)
            if not ctype:
                if local_path.lower().endswith(".mp3"):
                    ctype = "audio/mpeg"
                elif local_path.lower().endswith(".mp4"):
                    ctype = "video/mp4"
                else:
                    ctype = "application/octet-stream"

            blob.upload_from_filename(local_path, content_type=ctype, timeout=600)

            if make_public:
                blob.make_public()
                return {"url": blob.public_url, "public": True}
            else:
                url = blob.generate_signed_url(
                    version="v4", expiration=timedelta(hours=1), method="GET"
                )
                return {"url": url, "public": False}

        except Exception as e:
            print(f"[Upload Error] {e}")
            return None


# --- Video Generator with Veo3 Integration ---
class VideoGenerator:
    def __init__(self):
        self.api_key = API_KEYS["creatomate_api_key"]
        self.url = API_KEYS["creatomate_url"]
        self.subtitle_template_id = API_KEYS["creatomate_subtitle_template_id"]
        self.runway_api_key = API_KEYS.get("runway_api_key")
        self.pexels_api_key = API_KEYS.get("pexels_api_key")
        self.gcs_manager = None  # Will be set when needed
        # Initialize RunwayML async client
        self.runway_client = (
            AsyncRunwayML(api_key=self.runway_api_key) if self.runway_api_key else None
        )

    def get_audio_length(self, audio_path: str) -> float:
        """Get the length of an audio file in seconds."""
        return MP3(audio_path).info.length

    @staticmethod
    def google_image_search(
        query: str, api_key: str, cx: str, num_results: int = 1
    ) -> Optional[str]:
        """
        Fetch image URL using Google Custom Search API.

        Args:
            query: The search query (entity name)
            api_key: Google Custom Search API key
            cx: Custom Search Engine ID
            num_results: Number of results to fetch (default: 1)

        Returns:
            URL of the first image found, or None if no image found
        """
        # Unsupported image formats for RunwayML (SVG, etc.)
        unsupported_extensions = [".svg", ".ico", ".bmp", ".tiff", ".tif"]

        url = "https://www.googleapis.com/customsearch/v1"
        params = {
            "q": query,
            "key": api_key,
            "cx": cx,
            "searchType": "image",
            "num": max(
                num_results, 5
            ),  # Fetch more results to filter unsupported formats
        }

        try:
            response = requests.get(url, params=params, timeout=10)
            response.raise_for_status()
            results = response.json()

            # Find the first valid image (not SVG or other unsupported formats)
            if "items" in results:
                for item in results["items"]:
                    image_url = item.get("link", "")
                    if not image_url or not image_url.startswith("http"):
                        continue

                    # Check if the URL ends with an unsupported extension
                    url_lower = image_url.lower()
                    is_unsupported = any(
                        url_lower.endswith(ext) for ext in unsupported_extensions
                    )

                    if is_unsupported:
                        print(f"      Skipping unsupported format: {image_url}")
                        continue

                    return image_url

            return None
        except Exception as e:
            print(f"[Error fetching image for '{query}']: {e}")
            return None

    def verify_video_relevance(
        self, query: str, video_tags: List[str], video_url: str = None
    ) -> tuple[bool, float]:
        """
        Vérifie si une vidéo Pexels est pertinente par rapport à la requête.

        Utilise une comparaison de mots-clés entre la requête et les tags de la vidéo.

        Args:
            query: La requête de recherche originale
            video_tags: Liste des tags associés à la vidéo Pexels
            video_url: URL de la vidéo (pour log)

        Returns:
            Tuple (is_relevant: bool, score: float entre 0 et 1)
        """
        if not video_tags:
            return True, 0.5  # Pas de tags = on accepte par défaut

        # Normaliser la requête en mots-clés
        query_words = set(query.lower().split())
        # Enlever les mots vides communs
        stop_words = {
            "the",
            "a",
            "an",
            "in",
            "on",
            "at",
            "for",
            "to",
            "of",
            "and",
            "or",
        }
        query_words = query_words - stop_words

        # Normaliser les tags
        tags_words = set()
        for tag in video_tags:
            tags_words.update(tag.lower().split())
        tags_words = tags_words - stop_words

        # Calculer le score de correspondance
        if not query_words:
            return True, 0.5

        # Intersection des mots
        common_words = query_words & tags_words
        score = len(common_words) / len(query_words)

        # Seuil de pertinence : au moins 30% des mots de la requête doivent matcher
        is_relevant = score >= 0.3

        if not is_relevant:
            print(f"      ⚠️ Vidéo peu pertinente (score: {score:.2f})")
            print(f"         Requête: {query_words}")
            print(f"         Tags: {tags_words}")

        return is_relevant, score

    def search_pexels_video(
        self,
        query: str,
        orientation: str = None,
        per_page: int = 15,
        verify_relevance: bool = True,
        max_retries: int = 3,
    ) -> Optional[str]:
        """
        Cherche une vidéo sur Pexels et retourne l'URL directe du fichier MP4.

        Inclut une vérification de pertinence basée sur les tags de la vidéo.

        Args:
            query: Terme de recherche
            orientation: Orientation de la vidéo ('portrait', 'landscape', 'square') ou None pour toutes
            per_page: Nombre de résultats à récupérer
            verify_relevance: Si True, vérifie que la vidéo correspond au prompt
            max_retries: Nombre max de tentatives pour trouver une vidéo pertinente

        Returns:
            URL de la vidéo MP4 ou None si aucune vidéo trouvée
        """
        if not self.pexels_api_key:
            print("[Error] Pexels API key not configured")
            return None

        base_url = "https://api.pexels.com/videos/search"

        headers = {"Authorization": self.pexels_api_key}

        params = {
            "query": query,
            "per_page": per_page,
            "size": "medium",
        }
        # Ajouter l'orientation seulement si spécifiée
        if orientation:
            params["orientation"] = orientation

        try:
            response = requests.get(
                base_url, headers=headers, params=params, timeout=10
            )
            response.raise_for_status()
            data = response.json()

            if not data.get("videos"):
                print(f"Aucune vidéo trouvée pour le terme : {query}")
                return None

            import random

            # Copier la liste pour pouvoir retirer les vidéos non pertinentes
            available_videos = data["videos"].copy()
            attempts = 0

            while available_videos and attempts < max_retries:
                attempts += 1

                # Sélectionner une vidéo au hasard
                video_data = random.choice(available_videos)
                available_videos.remove(video_data)

                # Récupérer les tags de la vidéo pour vérification
                video_tags = video_data.get("tags", [])
                # Pexels stocke aussi des infos dans 'url' qui contient souvent des mots-clés
                pexels_url = video_data.get("url", "")
                # Extraire des mots-clés de l'URL (ex: /video/soccer-player-kicking-ball-12345/)
                url_keywords = (
                    pexels_url.split("/")[-2].replace("-", " ") if pexels_url else ""
                )

                # Combiner tags et mots-clés d'URL
                all_tags = video_tags + [url_keywords] if url_keywords else video_tags

                # Vérifier la pertinence si demandé
                if verify_relevance and all_tags:
                    is_relevant, score = self.verify_video_relevance(query, all_tags)
                    if not is_relevant:
                        print(
                            f"      🔄 Tentative {attempts}/{max_retries} - recherche d'une vidéo plus pertinente..."
                        )
                        continue
                    print(f"      ✓ Vidéo pertinente (score: {score:.2f})")

                # Trouver le bon fichier vidéo MP4
                target_width = 1080  # Largeur idéale pour du vertical HD

                video_files = video_data.get("video_files", [])
                mp4_files = [f for f in video_files if f["file_type"] == "video/mp4"]

                if not mp4_files:
                    continue

                # Chercher le fichier le plus proche de 1080p
                mp4_files.sort(key=lambda x: abs(x["width"] - target_width))
                best_link = mp4_files[0]["link"]

                print(
                    f"Vidéo trouvée ! Durée: {video_data['duration']}s | Auteur: {video_data['user']['name']}"
                )
                return best_link

            # Si on arrive ici, on n'a pas trouvé de vidéo pertinente
            # Fallback: prendre la première vidéo disponible même si pas parfaitement pertinente
            if data["videos"]:
                print(
                    "      ⚠️ Aucune vidéo parfaitement pertinente, utilisation du meilleur résultat disponible"
                )
                video_data = data["videos"][0]
                video_files = video_data.get("video_files", [])
                mp4_files = [f for f in video_files if f["file_type"] == "video/mp4"]
                if mp4_files:
                    mp4_files.sort(key=lambda x: abs(x["width"] - 1080))
                    return mp4_files[0]["link"]

            return None

        except requests.exceptions.RequestException as e:
            print(f"Erreur lors de la connexion à l'API Pexels : {e}")
            return None

    def download_pexels_video(self, video_url: str, output_path: str) -> Optional[str]:
        """
        Télécharge une vidéo depuis Pexels.

        Args:
            video_url: URL de la vidéo à télécharger
            output_path: Chemin local où sauvegarder la vidéo

        Returns:
            Chemin du fichier téléchargé ou None si échec
        """
        try:
            print("      Téléchargement de la vidéo Pexels...")
            response = requests.get(video_url, timeout=30)
            response.raise_for_status()

            with open(output_path, "wb") as f:
                f.write(response.content)

            print(f"      Vidéo téléchargée : {output_path}")
            return output_path

        except Exception as e:
            print(f"      Erreur lors du téléchargement : {e}")
            return None

    async def get_pexels_videos_from_prompts(
        self,
        video_prompts: VideoQuery,
        article_gcs_base_path: str,
        gcs_manager: GCSManager = None,
    ) -> List[str]:
        """
        Recherche et télécharge des vidéos depuis Pexels basées sur les prompts,
        puis les stocke dans GCS.

        Args:
            video_prompts: VideoQuery model contenant les prompts pour chaque vidéo
            article_gcs_base_path: Base GCS path pour l'article
            gcs_manager: GCS manager instance

        Returns:
            List des URLs de vidéos stockées dans GCS
        """
        if not gcs_manager:
            gcs_manager = GCSManager()

        if article_gcs_base_path.endswith("/"):
            video_base_path = article_gcs_base_path
        else:
            video_base_path = article_gcs_base_path.replace("/audio.mp3", "/").replace(
                "media/audio/", "media/videos/"
            )

        print(f"   🎬 Recherche de {len(video_prompts.index)} vidéos sur Pexels...")

        video_urls = []
        temp_dir = VIDEO_CONFIG.get("temp_dir", "temp_videos")
        os.makedirs(temp_dir, exist_ok=True)

        for idx, veo_prompt in video_prompts.index.items():
            try:
                print(f"      Vidéo {idx}: {veo_prompt.prompt[:60]}...")

                # Rechercher une vidéo sur Pexels
                pexels_url = self.search_pexels_video(veo_prompt.prompt)

                if not pexels_url:
                    print(f"      ⚠️ Aucune vidéo trouvée pour : {veo_prompt.prompt}")
                    continue

                # Télécharger la vidéo
                local_video_path = os.path.join(temp_dir, f"pexels_video_{idx}.mp4")
                downloaded_path = self.download_pexels_video(
                    pexels_url, local_video_path
                )

                if not downloaded_path:
                    continue

                # Upload vers GCS
                gcs_video_path = f"{video_base_path}video_{idx}.mp4"
                upload_result = gcs_manager.upload_file(downloaded_path, gcs_video_path)

                if upload_result:
                    video_urls.append(upload_result["url"])
                    print(f"\033[92mSUCCESS : {upload_result['url']}\033[0m")

                # Nettoyer le fichier local
                try:
                    os.remove(downloaded_path)
                except:
                    pass

            except Exception as e:
                print(f"      ❌ Erreur pour la vidéo {idx}: {str(e)}")
                continue

        print(f"   ✅ Récupéré {len(video_urls)} vidéos depuis Pexels")
        return video_urls

    async def generate_videos_from_prompts(
        self,
        video_prompts: VideoQuery,
        article_gcs_base_path: str,
        gcs_manager: GCSManager = None,
    ) -> List[str]:
        """
        Generate videos using RunwayML API from prompts and store them in GCS.
        Uses async parallelization with concurrency limit based on tier.

        Args:
            video_prompts: VideoQuery model containing prompts and entities for each video
            article_gcs_base_path: Base GCS path for the article (e.g., media/articles/domain/thematic/date/article-hash/)
                                   All media files (audio, videos, metadata) are stored in this same folder.
            gcs_manager: GCS manager instance

        Returns:
            List of video URLs stored in GCS
        """
        if not gcs_manager:
            gcs_manager = GCSManager()

        # Use the base path directly - all media files are now in the same folder
        if article_gcs_base_path.endswith("/"):
            video_base_path = article_gcs_base_path
        else:
            # For backward compatibility if old path format is used
            video_base_path = article_gcs_base_path.replace("/audio.mp3", "/").replace(
                "media/audio/", "media/videos/"
            )

        print(f"   🎬 Generating {len(video_prompts.index)} videos with RunwayML...")

        # Create semaphore for concurrency control
        concurrency_limit = len(video_prompts.index)
        print(f"videos number sent for generatio: {concurrency_limit}")
        semaphore = asyncio.Semaphore(concurrency_limit)

        # Create tasks for all videos
        tasks = []
        for idx, veo_prompt in video_prompts.index.items():
            task = self._generate_single_video(
                idx=idx,
                veo_prompt=veo_prompt,
                video_base_path=video_base_path,
                gcs_manager=gcs_manager,
                semaphore=semaphore,
            )
            tasks.append(task)

        # Run all tasks in parallel with concurrency limit
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Collect successful video URLs
        video_urls = []
        for idx, result in enumerate(results):
            if isinstance(result, Exception):
                print(f"      ❌ Error generating video {idx + 1}: {str(result)}")
            elif result:
                video_urls.append(result)

        print(f"   ✅ Generated {len(video_urls)} videos successfully")
        return video_urls

    async def _generate_single_video(
        self,
        idx: int,
        veo_prompt,
        video_base_path: str,
        gcs_manager: GCSManager,
        semaphore: asyncio.Semaphore,
    ) -> Optional[str]:
        """
        Generate a single video using RunwayML API with base64 encoded images.

        Uses the new approach where images are encoded to base64 data URIs
        and sent directly to RunwayML without needing GCS upload first.
        Runway's internal queue system handles parallel requests automatically.

        Args:
            idx: Video index
            veo_prompt: VeoPrompt object with prompt and entities
            video_base_path: Base GCS path for storing videos
            gcs_manager: GCS manager instance
            semaphore: Asyncio semaphore for concurrency control

        Returns:
            GCS URL of generated video, or None if failed
        """
        async with semaphore:
            reference_image_url = None
            local_image_path = None
            local_video_path = None

            try:
                print(
                    f"      Video {idx}: Generating from prompt: {veo_prompt.prompt}..."
                )

                # Prepare reference image URL from first entity only
                if veo_prompt.entities and len(veo_prompt.entities) > 0:
                    # Take only the first entity
                    first_entity = veo_prompt.entities[0]
                    print(f"      Using first entity: '{first_entity}'")

                    # Get only one image URL
                    reference_image_url = self.google_image_search(
                        query=first_entity,
                        api_key=API_KEYS["google_search_api_key"],
                        cx=API_KEYS["google_search_cx"],
                        num_results=1,  # Get only 1 image
                    )

                    if reference_image_url:
                        print(f"         Reference image URL: {reference_image_url}")
                    else:
                        print(f"         No valid image found for '{first_entity}'")

                # Fallback: If no reference image found, search based on the prompt itself
                if not reference_image_url:
                    # Extract key visual terms from the prompt (first 5-6 words)
                    prompt_words = veo_prompt.prompt.split()[:6]
                    search_query = " ".join(prompt_words)
                    print(
                        f"      No entity image found, searching based on prompt: '{search_query}'"
                    )

                    reference_image_url = self.google_image_search(
                        query=search_query,
                        api_key=API_KEYS["google_search_api_key"],
                        cx=API_KEYS["google_search_cx"],
                        num_results=1,
                    )

                    if reference_image_url:
                        print(f"         Fallback image URL: {reference_image_url}")
                    else:
                        print("         ⚠ No fallback image found either")

                # Download and encode reference image to base64 if found
                image_data_uri = None
                if reference_image_url:
                    print(f"      Using reference image: {reference_image_url}")
                    try:
                        # Download the image
                        print("      Downloading reference image...")
                        # Add User-Agent header to avoid 403 errors from Wikipedia and other sites
                        headers = {
                            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
                        }
                        img_response = requests.get(
                            reference_image_url, timeout=30, headers=headers
                        )
                        img_response.raise_for_status()
                        print(img_response)

                        # Determine file extension from content type or URL
                        content_type = img_response.headers.get(
                            "content-type", "image/jpeg"
                        )

                        # Check for unsupported content types
                        unsupported_content_types = [
                            "image/svg+xml",
                            "image/x-icon",
                            "image/bmp",
                        ]
                        if any(
                            ct in content_type.lower()
                            for ct in unsupported_content_types
                        ):
                            print(
                                f"      ⚠ Unsupported content-type: {content_type}, skipping image"
                            )
                            raise ValueError(
                                f"Unsupported content-type: {content_type}"
                            )

                        ext_map = {
                            "image/jpeg": ".jpg",
                            "image/png": ".png",
                            "image/webp": ".webp",
                            "image/gif": ".gif",
                        }
                        file_ext = ext_map.get(
                            content_type.split(";")[0].strip(), ".jpg"
                        )

                        # Save image temporarily
                        local_image_path = f"./temp_reference_image_{idx}{file_ext}"
                        with open(local_image_path, "wb") as f:
                            f.write(img_response.content)
                        print(f"      Image saved locally: {local_image_path}")

                        # Validate image with PIL to ensure it's a valid raster image
                        from PIL import Image

                        try:
                            with Image.open(local_image_path) as img:
                                img_format = img.format
                                img_width, img_height = img.size
                                print(
                                    f"      Image validated: {img_format} {img_width}x{img_height}"
                                )

                                # Check minimum size (RunwayML needs reasonable resolution)
                                if img_width < 64 or img_height < 64:
                                    raise ValueError(
                                        f"Image too small: {img_width}x{img_height}"
                                    )

                                # Convert to JPEG if format is not well supported
                                if img_format not in ["JPEG", "PNG", "WEBP"]:
                                    print(f"      Converting {img_format} to JPEG...")
                                    # Convert to RGB if necessary (e.g., for RGBA or P mode)
                                    if img.mode in ("RGBA", "P", "LA"):
                                        img = img.convert("RGB")
                                    local_image_path_jpg = (
                                        f"./temp_reference_image_{idx}.jpg"
                                    )
                                    img.save(local_image_path_jpg, "JPEG", quality=90)
                                    os.remove(local_image_path)
                                    local_image_path = local_image_path_jpg
                                    file_ext = ".jpg"
                        except Exception as pil_error:
                            print(f"      ⚠ Invalid image file: {pil_error}")
                            if os.path.exists(local_image_path):
                                os.remove(local_image_path)
                            raise ValueError(f"Invalid image: {pil_error}")

                        # Encode image to base64 data URI
                        import base64

                        with open(local_image_path, "rb") as f:
                            base64_image = base64.b64encode(f.read()).decode("utf-8")

                        # Determine media type for data URI
                        media_type_map = {
                            ".jpg": "image/jpeg",
                            ".jpeg": "image/jpeg",
                            ".png": "image/png",
                            ".webp": "image/webp",
                            ".gif": "image/gif",
                        }
                        media_type = media_type_map.get(file_ext, "image/jpeg")
                        image_data_uri = f"data:{media_type};base64,{base64_image}"
                        print("      Image encoded to base64 data URI")

                        # Upload original image to GCS for reference/archive
                        image_gcs_path = (
                            f"{video_base_path}reference_image_{idx}{file_ext}"
                        )
                        print(
                            f"      Uploading reference image to GCS: {image_gcs_path}"
                        )
                        gcs_manager.upload_file(local_image_path, image_gcs_path)

                        # Clean up local image file
                        try:
                            if os.path.exists(local_image_path):
                                os.remove(local_image_path)
                        except Exception as cleanup_error:
                            print(
                                f"      Warning: Could not remove temp image: {cleanup_error}"
                            )

                    except Exception as e:
                        print(f"      ⚠ Failed to process reference image: {e}")
                        # Reset to try fallback
                        image_data_uri = None
                        reference_image_url = None

                # If still no valid image, try a second fallback search with simpler terms
                if not image_data_uri:
                    print("      Attempting second fallback image search...")
                    # Use more generic terms from the prompt
                    prompt_words = veo_prompt.prompt.split()
                    # Try different word combinations
                    for start_idx in [0, 2, 4]:
                        if start_idx >= len(prompt_words):
                            continue
                        search_query = " ".join(prompt_words[start_idx : start_idx + 4])
                        print(f"      Trying search: '{search_query}'")

                        fallback_url = self.google_image_search(
                            query=search_query,
                            api_key=API_KEYS["google_search_api_key"],
                            cx=API_KEYS["google_search_cx"],
                            num_results=1,
                        )

                        if fallback_url:
                            try:
                                headers = {
                                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
                                }
                                img_response = requests.get(
                                    fallback_url, timeout=30, headers=headers
                                )
                                img_response.raise_for_status()

                                content_type = img_response.headers.get(
                                    "content-type", "image/jpeg"
                                )
                                if (
                                    "svg" in content_type.lower()
                                    or "icon" in content_type.lower()
                                ):
                                    continue

                                local_image_path = f"./temp_reference_image_{idx}.jpg"
                                with open(local_image_path, "wb") as f:
                                    f.write(img_response.content)

                                # Validate and convert with PIL
                                from PIL import Image

                                with Image.open(local_image_path) as img:
                                    if img.size[0] < 64 or img.size[1] < 64:
                                        os.remove(local_image_path)
                                        continue
                                    if img.mode in ("RGBA", "P", "LA"):
                                        img = img.convert("RGB")
                                    img.save(local_image_path, "JPEG", quality=90)

                                import base64

                                with open(local_image_path, "rb") as f:
                                    base64_image = base64.b64encode(f.read()).decode(
                                        "utf-8"
                                    )

                                image_data_uri = (
                                    f"data:image/jpeg;base64,{base64_image}"
                                )
                                reference_image_url = fallback_url
                                print(
                                    f"      ✓ Second fallback image found: {fallback_url}"
                                )
                                break
                            except Exception as fallback_error:
                                print(f"      Fallback image failed: {fallback_error}")
                                continue

                # Call RunwayML API
                print("      Calling RunwayML API...")

                # Get configuration
                model = VIDEO_CONFIG.get("runway_model", "gen4_turbo")
                ratio = VIDEO_CONFIG.get("runway_video_ratio", "9:16")
                duration = VIDEO_CONFIG.get("runway_video_duration", 8)

                # Create video generation task and wait for completion
                # RunwayML handles the queue internally, so we can send all requests in parallel
                print(
                    "      Task submitted to Runway's queue - will process when ready"
                )

                try:
                    if image_data_uri:
                        print(
                            "      Creating image-to-video task with base64 encoded image..."
                        )
                        # Create task and wait for output
                        task = await self.runway_client.image_to_video.create(
                            model=model,
                            prompt_image=image_data_uri,
                            prompt_text=veo_prompt.prompt,
                            ratio=ratio,
                            duration=duration,
                        )
                        # Wait for task completion
                        print(
                            f"      Task created with ID: {task.id}, waiting for completion..."
                        )
                        task = await task.wait_for_task_output()
                    else:
                        # Fallback: text-to-video without image
                        print(
                            "      Creating text-to-video task without reference image..."
                        )
                        task = await self.runway_client.image_to_video.create(
                            model=model,
                            prompt_text=veo_prompt.prompt,
                            ratio=ratio,
                            duration=duration,
                        )
                        # Wait for task completion
                        print(
                            f"      Task created with ID: {task.id}, waiting for completion..."
                        )
                        task = await task.wait_for_task_output()

                    print("      Video generation completed!")

                except Exception as e:
                    if "failed" in str(e).lower():
                        print(f"      The video failed to generate: {e}")
                        raise ValueError(f"RunwayML task failed: {e}")
                    raise

                # Download the video from RunwayML
                video_url = task.output[0] if task.output else None
                if not video_url:
                    raise ValueError("No video URL in task output")

                local_video_path = f"./temp_video_{idx}.mp4"
                print("      Downloading generated video from RunwayML...")
                video_response = requests.get(video_url, timeout=60)
                video_response.raise_for_status()

                with open(local_video_path, "wb") as f:
                    f.write(video_response.content)
                print(f"      Video saved to {local_video_path}")

                # Upload to GCS
                video_gcs_path = f"{video_base_path}video_{idx}.mp4"
                print(f"      Uploading to GCS: {video_gcs_path}")

                upload_result = gcs_manager.upload_file(
                    local_video_path, video_gcs_path
                )
                if not upload_result:
                    raise ValueError("Failed to upload video to GCS")

                final_video_url = upload_result["url"]
                print(f"\033[92mSUCCESS : {final_video_url}\033[0m")

                # Clean up temporary file
                try:
                    if os.path.exists(local_video_path):
                        os.remove(local_video_path)
                        print(f"      Cleaned up temporary file: {local_video_path}")
                except Exception as e:
                    print(f"      Warning: Could not remove temporary file: {e}")

                if reference_image_url:
                    print(f"      Reference image used: {reference_image_url}")

                return final_video_url

            except Exception as e:
                print(f"      ❌ Error generating video {idx}: {str(e)}")
                # Clean up on error
                if local_video_path and os.path.exists(local_video_path):
                    try:
                        os.remove(local_video_path)
                    except:
                        pass
                return None

    def create_video(
        self,
        audio_url: str,
        video_urls: List[str],
        audio_length: float,
        outro_config: Dict,
        outro_duration: float = 4.0,
        extra_audio_padding: float = 3.0,
    ) -> Optional[RenderResponse]:
        """Create a video using Creatomate API."""

        # Timeline calculations
        video_count = len(video_urls)
        visual_duration = max(0.0, audio_length - outro_duration)
        segment_duration = visual_duration / video_count if video_count > 0 else 0.0
        outro_time = max(0.0, video_count * segment_duration)

        # Build video elements
        video_elements = []
        for i, url in enumerate(video_urls):
            element = {
                "name": f"Video-{i+1}",
                "type": "video",
                "track": 1,
                "time": i * segment_duration,
                "duration": segment_duration,
                "source": url,
                "volume": "0%",  # Couper le son des vidéos Pexels
            }

            if i > 0:
                element["animations"] = [
                    {"time": 0, "duration": 1, "transition": True, "type": "fade"}
                ]
            video_elements.append(element)

        # Main composition
        main_composition_elements = [
            {
                "name": "Videos",
                "type": "composition",
                "track": 1,
                "time": 0,
                "elements": video_elements,
            },
            self._create_description_element(),
        ]

        # Outro element
        outro = self._create_outro_element(outro_time, outro_duration, outro_config)

        # Final payload
        data_clip = {
            "output_format": "mp4",
            "width": 720,
            "height": 1280,
            "elements": [
                {
                    "type": "audio",
                    "fit": "cover",
                    "track": 1,
                    "time": 0,
                    "duration": audio_length + extra_audio_padding,
                    "source": audio_url,
                    "loop": False,
                    "audio_fade_out": 2,
                },
                {
                    "type": "composition",
                    "track": 2,
                    "time": 0,
                    "elements": main_composition_elements,
                },
                outro,
            ],
        }

        # API call
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }

        try:
            resp_clip = requests.post(
                self.url, json=data_clip, headers=headers, timeout=60
            )
            resp_clip.raise_for_status()
            return RenderResponse(**resp_clip.json())
        except Exception as e:
            print(f"[Video Creation Error] {e}")
            return None

    def add_subtitles(self, video_url: str) -> Optional[RenderResponse]:
        """Add subtitles to a video using Creatomate template."""
        data_sub = {
            "template_id": self.subtitle_template_id,
            "modifications": {"Video-DHM.source": str(video_url)},
        }

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }

        try:
            resp_sub = requests.post(
                self.url, json=data_sub, headers=headers, timeout=60
            )
            resp_sub.raise_for_status()
            return RenderResponse(**resp_sub.json())
        except Exception as e:
            print(f"[Subtitle Error] {e}")
            return None

    def wait_for_render(
        self, render_id: str, max_wait: int = 120, poll_interval: int = 2
    ) -> Optional[RenderResponse]:
        """Poll Creatomate API to check render status.

        Args:
            render_id: The render ID to check
            max_wait: Maximum time to wait in seconds (default: 120)
            poll_interval: Time between status checks in seconds (default: 2)

        Returns:
            RenderResponse with final status and URL, or None if failed/timeout
        """
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }

        check_url = f"https://api.creatomate.com/v2/renders/{render_id}"
        start_time = time.time()

        while time.time() - start_time < max_wait:
            try:
                resp = requests.get(check_url, headers=headers, timeout=10)
                resp.raise_for_status()
                render_data = resp.json()

                status = render_data.get("status", "").lower()

                if status == "succeeded":
                    print("      ✅ Render completed successfully")
                    return RenderResponse(**render_data)
                elif status in ["failed", "error"]:
                    print(f"      ❌ Render failed with status: {status}")
                    return None
                else:
                    # Status is still "rendering" or similar
                    print(
                        f"      ⏳ Render status: {status}... waiting {poll_interval}s"
                    )
                    time.sleep(poll_interval)

            except Exception as e:
                print(f"      ⚠️ Error checking render status: {e}")
                time.sleep(poll_interval)

        print(f"      ⏱️ Render timed out after {max_wait}s")
        return None

    def _create_description_element(self) -> Dict:
        """Create the description text element."""
        return {
            "name": "Description",
            "type": "text",
            "track": 2,
            "time": 0,
            "x": "3.743%",
            "y": "93.5601%",
            "width": "88.031%",
            "height": "18.9311%",
            "x_anchor": "0%",
            "y_anchor": "100%",
            "x_alignment": "50%",
            "y_alignment": "50%",
            "fill_color": "rgba(0,0,0,1)",
            "font_family": "Montserrat",
            "font_weight": "800",
            "line_height": "234%",
            "background_color": "rgba(255,255,255,1)",
            "background_x_padding": "73%",
            "background_y_padding": "35%",
            "background_align_threshold": "0%",
            "animations": [
                {
                    "time": 0,
                    "duration": 0.8,
                    "easing": "quadratic-out",
                    "type": "text-slide",
                    "scope": "split-clip",
                    "split": "line",
                    "overlap": "100%",
                    "direction": "up",
                    "background_effect": "scaling-clip",
                }
            ],
        }

    def _create_outro_element(
        self, outro_time: float, outro_duration: float, outro_config: Dict
    ) -> Dict:
        """Create the outro element."""
        return {
            "name": "Outro",
            "type": "composition",
            "track": 2,
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

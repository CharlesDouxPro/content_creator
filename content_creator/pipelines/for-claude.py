# %%
import re
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta, timezone
from typing import List, Optional
from pydantic import BaseModel, HttpUrl
import json
from openai import InternalServerError, OpenAI, RateLimitError
import base64
from typing import Optional, Literal
import time
from mutagen.mp3 import MP3
import pandas as pd

class RenderResponse(BaseModel):
    id: str
    status: str
    url: HttpUrl
    output_format: str

    
# --- Modèle de données ---
class ArticleBlock(BaseModel):
    title: str
    content: str
    images: List[HttpUrl] = []

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

# --- Scraper ---
class NewsScraper:
    PARIS = timezone(timedelta(hours=2))  # Europe/Paris
    NOW = datetime.now(PARIS)
    CUTOFF = NOW - timedelta(hours=24)

    MOIS = {
        "janv": 1, "févr": 2, "fevr": 2, "mars": 3, "avr": 4, "mai": 5, "juin": 6,
        "juil": 7, "août": 8, "aout": 8, "sept": 9, "oct": 10, "nov": 11, "déc": 12, "dec": 12,
        "janvier": 1, "février": 2, "fevrier": 2, "avril": 4, "juillet": 7, "septembre": 9,
        "octobre": 10, "novembre": 11, "décembre": 12, "decembre": 12,
    }

    REL_RE = re.compile(r"^\s*il y a\s+(\d+)\s+(minute|minutes|heure|heures|jour|jours)\s*$", re.I)
    ABS_RE = re.compile(
        r"^\s*(\d{1,2})\s+([A-Za-zéèêûîôàâùïëç\.]+)\s+(\d{4})(?:\s+à\s+(\d{1,2})h(\d{2})?)?\s*$",
        re.I
    )

    @classmethod
    def parse_french_time_label(cls, txt: str) -> Optional[datetime]:
        txt = (txt or "").strip()
        m = cls.REL_RE.match(txt)
        if m:
            n = int(m.group(1))
            unit = m.group(2).lower()
            if unit.startswith("minute"):
                return cls.NOW - timedelta(minutes=n)
            elif unit.startswith("heure"):
                return cls.NOW - timedelta(hours=n)
            elif unit.startswith("jour"):
                return cls.NOW - timedelta(days=n)
            return None

        m = cls.ABS_RE.match(txt.replace(" ", " "))
        if m:
            day = int(m.group(1))
            mois_raw = m.group(2).lower().replace(".", "")
            year = int(m.group(3))
            hh = int(m.group(4)) if m.group(4) else 0
            mm = int(m.group(5)) if m.group(5) else 0

            mois_raw = (mois_raw
                        .replace("févr", "fevr")
                        .replace("août", "aout")
                        .replace("déc", "dec"))
            month = cls.MOIS.get(mois_raw) or cls.MOIS.get(mois_raw[:4])
            if not month:
                return None

            return datetime(year, month, day, hh, mm, tzinfo=cls.PARIS)
        return None

    @classmethod
    def scrape_links_older_than_24h(cls, url: str) -> List[ArticleLink]:
        r = requests.get(url, timeout=20)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")

        results = []
        for article in soup.select("article"):
            time_el = article.select_one("time")
            a_el = article.select_one("h2 a, h3 a, a")

            if not time_el or not a_el or not a_el.get("href"):
                continue

            label = time_el.get_text(strip=True)
            dt = cls.parse_french_time_label(label)
            if not dt:
                continue

            if dt < cls.CUTOFF:
                try:
                    results.append(ArticleLink(
                        href=a_el["href"],
                        title=a_el.get_text(strip=True),
                        published_at=dt
                    ))
                except Exception as e:
                    print(f"Validation error: {e}")

        return results

    @classmethod
    def scrape_article(cls, url: str) -> List[ArticleBlock]:
        try:
            r = requests.get(url, timeout=20)
            r.raise_for_status()
        except requests.RequestException as e:
            print(f"[Erreur réseau] Impossible de charger l’URL {url}: {e}")
            return []

        soup = BeautifulSoup(r.text, "html.parser")

        # essaie plusieurs variantes de sélecteur
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
                        results.append(ArticleBlock(
                            title=current_title,
                            content=" ".join(buffer).strip(),
                            images=images
                        ))
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
                results.append(ArticleBlock(
                    title=current_title,
                    content=" ".join(buffer).strip(),
                    images=images
                ))
            except Exception as e:
                print(f"[Erreur validation dernier bloc] {e}")

        return results


class ArticleSummarizer:
    deepinfra_api_key="7jIPsm1yv398SZpzLaE0qw2DIs2Y5CZG"
    deepinfra_base_url="https://api.deepinfra.com/v1/openai"
    creatomate_url = "https://api.creatomate.com/v2/renders"
    api_key_creatomate = "e09589bdeb3348cc8e692d63a746b5aa18ade886f463481eb30176705e1e2b8dee3bbe67746bce4c64fe712c0b23096c"
    client = OpenAI(
                api_key=deepinfra_api_key, base_url=deepinfra_base_url
            )

    model_name = "mistralai/Mistral-Small-3.2-24B-Instruct-2506"


    @classmethod
    def generate_prompt(cls, content: str, usage : Literal["summary", "pexel"], video_time : int = 0) -> List[dict]:
        """Génère un prompt pour le modèle de langage. a partir du contenu de l’article."""
        if usage == "summary":
            prompt = [
                {
                    "role": "system",
                    "content": (
                        "Tu es un expert en storytelling digital. "
                        "Ta mission est de transformer des articles d’actualité en scripts courts, captivants et adaptés "
                        "à une lecture à voix haute sur TikTok ou Instagram. "
                        "Le style doit être clair, direct, accrocheur et donner envie d’en savoir plus. "
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
                        "Voici le contenu d’un article découpé en sections (titre + contenu). "
                        "Résume-le en un script narratif percutant pour TikTok/Instagram, sans dépasser une minute :\n\n"
                        f"content: {content}\n"
                    ),
                },
            ]
        elif usage == "pexel":
            prompt = [
            {
            "role": "system",
            "content": (
                "Tu es un expert en storytelling digital et en création visuelle. "
                "Ta mission est de générer un dictionnaire Python clé/valeur pour illustrer une narration TikTok avec des images générées par VEO 3.\n\n"
                "Contraintes :\n"
                "- Chaque clé est l’index de l’image (1, 2, 3…).\n"
                "- Chaque valeur est un prompt d’image en anglais, descriptif et visuel.\n"
                "- Les prompts doivent être adaptés au contexte de la narration et rester dynamiques, émotionnels et variés, comme dans un montage TikTok (ex: 'cinematic close up of a dramatic face under neon lights', 'crowd cheering in a stadium, shot from above, cinematic lighting', 'dark room spotlight with abstract shapes', 'slow motion water splash with high contrast').\n"
                "- Évite les noms propres.\n"
                "- Interdiction de répéter les mêmes mots exacts d’un prompt à l’autre.\n"
                "- Inspire-toi de styles visuels percutants (dramatic, cinematic, abstract, audience reaction, neon, silhouette, glitch, slow motion effect).\n"
                "- Chaque image doit pouvoir être utilisée comme frame clé dans une vidéo de 5 secondes.\n"
                "- Renvoie uniquement le JSON final, sans texte ni explication autour."
            ),
            },
            {
            "role": "user",
            "content": (
                "Voici la narration à illustrer :\n\n"
                f"{content}\n"
            ),
            },
            ]

            
        return prompt
    
    @classmethod
    def summarize_article(cls, content: ArticleBlock) -> Optional[str]:
        """ Utilise le modèle de langage pour résumer un article."""
        prompt = cls.generate_prompt(json.dumps(content.model_dump(), ensure_ascii=False, indent=2, default=str), usage="summary")
        response = cls.client.chat.completions.create(
                        model=cls.model_name,
                        max_tokens=64000,
                        messages=prompt,
                    )
        response_text = response.choices[0].message.content

        clean_text = cls.clean_text(response_text).strip()
        return clean_text
    
    @classmethod
    def write_video_queries(cls, content: str, video_time : int) -> Optional[str]:
        """ Utilise le modèle de langage pour générer une tram video"""
        prompt = cls.generate_prompt(json.dumps(content, ensure_ascii=False, indent=2, default=str), usage="pexel", video_time=video_time)
        response = cls.client.chat.completions.create(
                        model=cls.model_name,
                        max_tokens=64000,
                        messages=prompt,
                    )
        response_text = response.choices[0].message.content

        clean_text = cls.clean_text(response_text).strip()
        return clean_text
    
    @classmethod
    def clean_text(cls,text: str) -> str:
        text = re.sub(r"\*\*Script\s*:\*\*\s*", "", text, flags=re.IGNORECASE).strip()
    # Regex qui capture les emojis & pictogrammes
        emoji_pattern = re.compile(
            "["
            "\U0001F600-\U0001F64F"  # emoticônes
            "\U0001F300-\U0001F5FF"  # symboles & pictogrammes
            "\U0001F680-\U0001F6FF"  # transport & cartes
            "\U0001F1E0-\U0001F1FF"  # drapeaux
            "\U00002700-\U000027BF"  # symboles divers
            "\U0001F900-\U0001F9FF"  # emojis supplémentaires
            "\U00002600-\U000026FF"  # symboles météo, etc.
            "]+",
            flags=re.UNICODE,
        )
        return emoji_pattern.sub("", text)
    
    @classmethod
    def text_to_speech_google(cls, text: str, api_key: str= "AIzaSyDD8i61OqNlRjgH7m1oCqQZen308jtvJmw", output_file: str = "output.mp3") -> Optional[str]:
        """
        Convertit un texte en parole avec Google TTS et enregistre l'audio en MP3.
        
        Args:
            text (str): Le texte à convertir
            api_key (str): La clé API Google Cloud
            output_file (str): Le nom du fichier de sortie MP3

        Returns:
            Optional[str]: Le chemin du fichier généré ou None en cas d'erreur
        """
        try:
            body = {
                "audioConfig": {
                    "audioEncoding": "MP3",  # 🔹 MP3 au lieu de LINEAR16
                    "effectsProfileId": ["large-home-entertainment-class-device"],
                    "pitch": 0,
                    "speakingRate": 1
                },
                "input": {"text": text},
                "voice": {
                    "languageCode": "fr-FR",
                    "name": "fr-FR-Chirp3-HD-Vindemiatrix"
                }
            }

            url = f"https://texttospeech.googleapis.com/v1beta1/text:synthesize?key={api_key}"
            headers = {"Content-Type": "application/json"}

            response = requests.post(url, headers=headers, json=body, timeout=30)
            response.raise_for_status()

            data = response.json()
            if "audioContent" not in data:
                print(f"[Erreur TTS] Pas de contenu audio dans la réponse: {data}")
                return None

            # 🔹 Décodage du base64
            audio_bytes = base64.b64decode(data["audioContent"])

            # 🔹 Sauvegarde en MP3
            with open(output_file, "wb") as f:
                f.write(audio_bytes)

            print(f"[OK] Fichier audio enregistré : {output_file}")
            return output_file

        except requests.exceptions.RequestException as e:
            print(f"[Erreur réseau TTS] {e}")
            return None





# %%
URL = "https://www.20minutes.fr/medias/radio-france/"
scraper = NewsScraper()

print("=== Liens plus vieux que 24h ===")
links = scraper.scrape_links_older_than_24h(URL)

# 🔹 enlever les doublons par href
unique_links = {link.href: link for link in links}.values()

print(json.dumps([l.model_dump() for l in unique_links], ensure_ascii=False, indent=2, default=str))

print("\n=== Contenu des articles ===")
articles: List[FullArticle] = []

for l in unique_links:
    blocks = scraper.scrape_article(l.href)
    full = FullArticle(link=l, content=blocks)
    articles.append(full)

# 🔹 impression finale : liste d’articles complets
print(json.dumps([a.model_dump() for a in articles], ensure_ascii=False, indent=2, default=str))


# %%
ai_agent = ArticleSummarizer()

article = articles[0]
response = ai_agent.summarize_article(article)
video_time = MP3("output.mp3").info.length
videos_query_dict = ai_agent.write_video_queries(response, video_time)
videos_query_dict = json.loads(videos_query_dict.replace("```json\n","").replace("\n```", "").replace("\n",""))

# %%
videos_query_dict

# %%
ai_agent = ArticleSummarizer()
reponse = ai_agent.text_to_speech_google(response)




# %%
# import requests

# url = "https://api.creatomate.com/v2/renders"
# api_key = "e09589bdeb3348cc8e692d63a746b5aa18ade886f463481eb30176705e1e2b8dee3bbe67746bce4c64fe712c0b23096c"

# data = {
#   "template_id": "5f69a953-1529-4844-80d9-50346f3295d7",
#   "modifications": {
#     "News-Image.source": "https://creatomate.com/files/assets/94b5cd60-6ea2-44d3-9936-44a9b2484c49",
#     "Voiceover.source": ""
#   }
# }

# headers = {
#     "Content-Type": "application/json",
#     "Authorization": f"Bearer {api_key}"
# }

# response = requests.post(url, json=data, headers=headers)
# print(response.text)

# %%
audio = MP3("output.mp3")
print(audio.info.length + 2)

# %%
import mimetypes
from datetime import timedelta
from google.cloud import storage

def upload_file_with_key(
    json_key_path: str,
    bucket_name: str,
    local_path: str,
    dest_path: str,
    make_public: bool = False,
):
    client = storage.Client.from_service_account_json(json_key_path)
    bucket = client.bucket(bucket_name)

    # (facultatif) vérifier que le bucket existe et est accessible
    if not bucket.exists():
        raise RuntimeError(f"Bucket introuvable ou non accessible: {bucket_name}")

    blob = bucket.blob(dest_path)

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
        url = blob.generate_signed_url(version="v4", expiration=timedelta(hours=1), method="GET")
        return {"url": url, "public": False}


# %%
#UPLOAD gogole storage l'audio

res = upload_file_with_key(
    json_key_path="./api-key.json",
    bucket_name="content-bucket-charles-doux",
    local_path="./output.mp3",
    dest_path="media/audio/output.mp3",
    make_public=True,
)
print(res["url"])


# %%
# UPLOAD google storage les videos, A gere dynamiquement
res = upload_file_with_key(
    json_key_path="./api-key.json",
    bucket_name="content-bucket-charles-doux",
    local_path="./video3.mp4",
    dest_path="media/video/video3.mp4",
    make_public=True,
)
print(res["url"])

# %%


# %%
def upload_medias_on_bucket(media_path : list[str],  media_type : Literal["mp3","mp4"]) -> list[str]:
    if media_type == "mp3":
        res = upload_file_with_key(
        json_key_path="./api-key.json",
        bucket_name="content-bucket-charles-doux",
        local_path=media_path,
        dest_path="media/audio/output.mp3",
        make_public=True,
        )
    audio_url = res["url"]
    print(audio_url)
    return audio_url

def upload_video_on_bucket():


# %%
import os
import json
import time
import requests
from mutagen.mp3 import MP3
from pydantic import BaseModel, HttpUrl


creatomate_url = "https://api.creatomate.com/v2/renders"
api_key = "e09589bdeb3348cc8e692d63a746b5aa18ade886f463481eb30176705e1e2b8dee3bbe67746bce4c64fe712c0b23096c"
def get_audio_length():
    return MP3("./output.mp3").info.length
    

# --- Entrées dynamiques ---

AUDIO_PATH = "https://storage.googleapis.com/content-bucket-charles-doux/media/audio/output.mp3"


#le lien des video sur le bucket 
video_urls = [
    "https://storage.googleapis.com/content-bucket-charles-doux/media/video/video1.mp4",
    "https://storage.googleapis.com/content-bucket-charles-doux/media/video/video2.mp4",
    "https://storage.googleapis.com/content-bucket-charles-doux/media/video/video3.mp4",
    "https://storage.googleapis.com/content-bucket-charles-doux/media/video/video2.mp4",
    "https://storage.googleapis.com/content-bucket-charles-doux/media/video/video3.mp4",
    "https://storage.googleapis.com/content-bucket-charles-doux/media/video/video1.mp4",
     "https://storage.googleapis.com/content-bucket-charles-doux/media/video/video3.mp4",
]
outro_duration = 4.0         # durée de l'outro (secondes)
extra_audio_padding = 3.0    # marge pour l'audio + fade-out
audio_length = get_audio_length()  # durée de l'audio (secondes)
# --- Calculs de timeline ---
video_time = len(video_urls)
visual_duration = max(0.0, audio_length - outro_duration)  # portion dédiée aux vidéos
segment_duration = visual_duration / video_time if video_time > 0 else 0.0 # temps pour une video 
outro_time = max(0.0, video_time * segment_duration)

# --- Construire la pile "Videos" dynamiquement ---
# liste d'objet element qui sont mes videos
video_elements = []

for i, url in enumerate(video_urls):
    #je crée un objet element 
    element = {
        "name": f"Video-{i+1}", #nom de la video, video 1, 2, 3
        "type": "video", # element de type video
        "track": 1, 
        "time": i * segment_duration,       # position de départ
        "duration": segment_duration,        # durée forcée du segment
        "source": url,
        # Dé-commente si tu veux assurer le bouclage si la source est trop courte :
        # "loop": True,
    }
    # Ajoute une transition (fade) sauf sur la toute première
    if i > 0:
        element["animations"] = [
            {
                "time": 0,
                "duration": 1,
                "transition": True,
                "type": "fade"
            }
        ]
    video_elements.append(element)

# --- Composition "Videos" (piste 1) + une zone de texte (piste 2) ---
main_composition_elements = [
    {
        "name": "Videos",
        "type": "composition",
        "track": 1,
        "time": 0,
        "elements": video_elements
    },
    #ici je gere la layout 2 qui est mon texte
    {
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
                "background_effect": "scaling-clip"
            }
        ]
    }
]

# --- Objet "Outro" qui démarre à la fin des segments vidéo ---
outro = {
    "name": "Outro",
    "type": "composition",
    "track": 2,
    "time": outro_time,     # commence juste après les vidéos
    "duration": outro_duration,
    "fill_color": "rgba(255,255,255,1)",
    "animations": [
        {
            "time": 0,
            "duration": 1,
            "transition": True,
            "type": "fade"
        }
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
            "text": "My Brand Realtors",
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
                    "background_effect": "scaling-clip"
                }
            ]
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
            "text": "Elisabeth Parker",
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
                    "background_effect": "scaling-clip"
                }
            ]
        }
    ]
}

# --- Construction finale du payload ---
data_clip = {
    "output_format": "mp4",
    "width": 720,
    "height": 1280,
    
    "elements": [
        # Audio global (couvre toute la durée + petit padding pour fade out)
        {
            "type": "audio",
            "fit": "cover",
            "track": 1,
            "time": 0,
            "duration": audio_length + extra_audio_padding,
            "source": AUDIO_PATH,
            "loop": False,
            "audio_fade_out": 2
        },
        # Composition principale (vidéos + texte)
        {
            "type": "composition",
            "track": 2,
            "time": 0,
            "elements": main_composition_elements
        },
        # Outro à la fin
        outro
    ]
}

# --- Appel API Creatomate ---
headers = {
    "Content-Type": "application/json",
    "Authorization": f"Bearer {api_key}"
}

resp_clip = requests.post(creatomate_url, json=data_clip, headers=headers, timeout=60)
response_clip = RenderResponse(**resp_clip.json())
time.sleep(40)
print("clip done")
print(response_clip.url)



data_sub = {
    "template_id": "d40af67d-6eb2-4d97-bfec-68b3040a8ec4",
    "modifications": {"Video-DHM.source": str(response_clip.url)},
}
resp_sub = requests.post(creatomate_url, json=data_sub, headers=headers, timeout=60)
response_sub = RenderResponse(**resp_sub.json())

time.sleep(30)

print("Lien final:", str(response_sub.url))




# %% [markdown]
# ## A jour : 
# - Scrappeur lien
# - Scappeur contenues
# - Generation d'un speetch en ecrit
# - Generation text to speetch mp3
# - Upload bucket cloud storage public
# - Montage video automatique
# - Generation de sous titres
# 
# ## Manquant : 
# - Generation automatique de video par l'IA (difficile, long)
# - Créer le compte tiktok avec repertoire (username, mot de passe) (long)
# - Créer le compte instagram avec repertoire (username, mot de passe) (long)
# - Function d'upload sur tiktok (facile)
# - Function d'upload sur instagram (facile)
# - Generation description + hashtags (moyen)
# - Video d'outro de 3 secondes 'abonne toi' (facile)
# 
# ## Warning : 
# - La somme du temps des videos aggrégé doit etre supérieur à l'audio
# - Charges peuvent monter rapidement 
# - La pipeline <2 minutes actuellement sans generation video, objectif rester <5 minutes.
# 
# ## Prix : 
# Tout est en version gratuite mais les prix seront : 
# - Generation llm -> gratuit, api du boulot
# - Speech to text -> 10 euros / mois (mais esssaie gratuit de 3 mois)
# - Stockage cloud medias -> 4 euros / mois (mais esssaie gratuit de 3 mois)
# - Monteur video dynamique ->  54 euros / mois (les technos gratuite ne permettent pas cette qualité)
# - Generation images -> ? (ça va pricer ça aussi....)

# %% [markdown]
# 

# %% [markdown]
# # Building complte pipeline

# %%
import re
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta, timezone
from typing import List, Optional
from pydantic import BaseModel, HttpUrl
import json
from openai import InternalServerError, OpenAI, RateLimitError
import base64
from typing import Optional, Literal
import time
from mutagen.mp3 import MP3
import pandas as pd

# %% [markdown]
# Setting pipeline parameters

# %%

media_domain = "www.20minutes.fr"
thematic = "medias"
url = f"https://{media_domain}/{thematic}/"
today_date = datetime.now().strftime('%Y-%m-%d')
audio_file_name = f"{thematic}/{media_domain}/{today_date}/{article_url}/audio.mp3"

print(url)



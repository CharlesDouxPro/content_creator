#!/usr/bin/env python3
"""
pipeline_agentic.py — Pipeline "scrape -> script -> vidéo par l'agent".

Pour chaque channel du config (content_creator/config/channels.py), EN PARALLÈLE :
  1. scrape la page (NewsScraper)
  2. prend le 1er article NON traité (dédup locale)
  3. génère le script (ArticleSummarizer.summarize_article, ton = --mood)
  4. envoie le script à l'agent (run_agent) qui produit la vidéo

Usage :
    python -m content_creator.pipelines.pipeline_agentic
    python -m content_creator.pipelines.pipeline_agentic --mood "ton calme et inspirant"
    python -m content_creator.pipelines.pipeline_agentic --workers 1
"""

import os
import re
import json
import hashlib
import argparse
import threading
import unicodedata
from typing import List, Optional
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor

from content_creator.config.channels import PIPELINES
from content_creator.pipelines.modules import NewsScraper, ArticleSummarizer, FullArticle, GCSManager
from content_creator.agentic.video_agent import run_agent


def slugify(text: str, maxlen: int = 60) -> str:
    """Titre -> nom de fichier sûr (ascii, minuscules, tirets)."""
    text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode()
    text = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    return text[:maxlen].strip("-") or "video"


def _article_text(article) -> str:
    """Rend un article scrapé en texte (titre + sections), pour le master et l'inférence du titre."""
    parts = [article.link.title]
    for b in article.content:
        txt = getattr(b, "content", "") or ""
        if txt:
            parts.append(txt)
    return "\n".join(parts)[:6000]

PROCESSED_PATH = "runs/processed.json"
_lock = threading.Lock()


# ========================
# Dédup (journal local, protégé par lock)
# ========================
def _url_key(url: str) -> str:
    return hashlib.md5(str(url).encode()).hexdigest()[:10]


def _load_processed() -> dict:
    if os.path.exists(PROCESSED_PATH):
        with open(PROCESSED_PATH, encoding="utf-8") as f:
            return json.load(f)
    return {}


def is_processed(name: str, url: str) -> bool:
    with _lock:
        return _url_key(url) in _load_processed().get(name, [])


def mark_processed(name: str, url: str):
    with _lock:
        data = _load_processed()
        data.setdefault(name, [])
        key = _url_key(url)
        if key not in data[name]:
            data[name].append(key)
        os.makedirs(os.path.dirname(PROCESSED_PATH) or ".", exist_ok=True)
        with open(PROCESSED_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)


# ========================
# Scrape + sélection
# ========================
def scrape_channel(url: str) -> List[FullArticle]:
    """Scrape la page d'un channel -> articles complets (même logique que pipeline_multi)."""
    scraper = NewsScraper()
    links = scraper.scrape_links_older_than_24h(url)
    unique = {link.href: link for link in links}.values()
    articles = []
    for link in unique:
        blocks = scraper.scrape_article(link.href)
        if blocks:
            articles.append(FullArticle(link=link, content=blocks))
    return articles


def find_first_unprocessed(articles: List[FullArticle], name: str) -> Optional[FullArticle]:
    for article in articles:
        if not is_processed(name, article.link.href):
            return article
    return None


# ========================
# Traitement d'un channel (threadable)
# ========================
def process_channel(channel: dict, mood: str = None) -> dict:
    name = channel["name"]
    res = {"name": name, "ok": False, "video": None, "gcs_url": None,
           "title": None, "article": None, "error": None}
    try:
        print(f"[{name}] 📡 scrape {channel['url']}", flush=True)
        articles = scrape_channel(channel["url"])
        if not articles:
            res["error"] = "aucun article scrapé"
            print(f"[{name}] ⚠️ {res['error']}", flush=True)
            return res

        article = find_first_unprocessed(articles, name)
        if not article:
            res["error"] = "tous les articles déjà traités"
            print(f"[{name}] ⏭️ {res['error']}", flush=True)
            return res
        res["article"] = article.link.title
        print(f"[{name}] ✏️ article: {article.link.title[:70]}", flush=True)

        # Le master écrit le script (style selon mood) PUIS réalise la vidéo
        summarizer = ArticleSummarizer()
        article_text = _article_text(article)
        result = run_agent(article_text, skill_name=channel["skill"],
                           avatar=channel.get("avatar"), mood=mood, article=article, label=name)
        video = result.get("video")
        script = result.get("script") or article_text
        if not video:
            res["error"] = "échec génération vidéo"
            mark_processed(name, article.link.href)
            return res
        res["video"] = video

        # Titre inféré (du script écrit par le master) -> chemin GCS : channel / date / titre.mp4
        title = summarizer.generate_title(script)
        res["title"] = title
        date = datetime.now().strftime("%Y-%m-%d")
        dest = f"{name}/{date}/{slugify(title)}.mp4"
        up = GCSManager().upload_file(video, dest)
        res["gcs_url"] = up["url"] if up else None
        print(f"[{name}] ☁️ upload: {dest} -> {res['gcs_url']}", flush=True)

        mark_processed(name, article.link.href)
        res["ok"] = True
        return res
    except Exception as e:
        res["error"] = str(e)
        print(f"[{name}] ❌ {e}", flush=True)
        return res


def main():
    ap = argparse.ArgumentParser(description="Pipeline agentic (scrape -> script -> vidéo)")
    ap.add_argument("--mood", default=None,
                    help="Ton/ambiance appliqué au script ET au system prompt de l'agent")
    ap.add_argument("--workers", type=int, default=0,
                    help="Channels en parallèle (0 = tous)")
    args = ap.parse_args()

    workers = args.workers or len(PIPELINES)
    print("=" * 60)
    print(f"🎬 PIPELINE AGENTIC — {len(PIPELINES)} channel(s) en parallèle ({workers} workers)")
    if args.mood:
        print(f"   mood: {args.mood}")
    print("=" * 60)

    with ThreadPoolExecutor(max_workers=workers) as ex:
        results = list(ex.map(lambda c: process_channel(c, args.mood), PIPELINES))

    print("\n" + "=" * 60)
    print("RÉCAP")
    for r in results:
        if r["ok"]:
            print(f"  ✅ {r['name']} — {r.get('title')}")
            print(f"     GCS: {r.get('gcs_url') or '(upload échoué)'}")
            print(f"     local: {r['video']}")
        else:
            print(f"  ❌ {r['name']}: {r['error']}")
    print("=" * 60)


if __name__ == "__main__":
    main()

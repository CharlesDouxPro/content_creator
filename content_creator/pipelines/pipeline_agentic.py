#!/usr/bin/env python3
"""
pipeline_agentic.py — Pipeline "brief -> vidéo par l'agent".

Pour chaque channel (content_creator/config/channels.py), EN PARALLÈLE, la pipeline
se contente de passer le `context` (prompt + ressources + mood + characters) et le pool de
modèles à l'agent : c'est l'AGENT qui décide de l'ordre des opérations et des tools à utiliser
(scraping inclus, via le tool `scrape_article`) pour produire la vidéo demandée. La pipeline
ne fait ensuite que nommer/uploader le résultat.

TOUT est piloté par le channel config — aucun paramètre en ligne de commande.

Usage :
    python -m content_creator.pipelines.pipeline_agentic
"""

import re
import argparse
import unicodedata
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor

from content_creator.config.channels import PIPELINES
from content_creator.pipelines.modules import ArticleSummarizer, GCSManager
from content_creator.agentic.video_agent import run_agent


def slugify(text: str, maxlen: int = 60) -> str:
    """Titre -> nom de fichier sûr (ascii, minuscules, tirets)."""
    text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode()
    text = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    return text[:maxlen].strip("-") or "video"


# ========================
# Traitement d'un channel (threadable)
# ========================
def process_channel(channel: dict) -> dict:
    name = channel["name"]
    res = {
        "name": name,
        "ok": False,
        "video": None,
        "gcs_url": None,
        "title": None,
        "article": None,
        "error": None,
    }
    try:
        context = channel.get("context") or {}
        models_config = channel.get("models_config")
        slm_config = models_config.get("slm") if models_config else None

        # L'agent orchestre tout depuis le brief : acquisition du contenu (tool scrape_article),
        # écriture, réalisation. La pipeline ne lui impose plus de déroulé.
        print(f"[{name}] 🎬 brief -> agent ({channel['skill']})", flush=True)
        result = run_agent(
            skill_name=channel["skill"],
            label=name,
            models_config=models_config,
            context=context,
        )
        video = result.get("video")
        res["article"] = result.get("article")
        if not video:
            res["error"] = "échec génération vidéo"
            print(f"[{name}] ⚠️ {res['error']}", flush=True)
            return res
        res["video"] = video

        # Titre inféré (script du master, sinon brief/nom) -> chemin GCS : channel / date / titre.mp4
        summarizer = ArticleSummarizer(slm_config)
        script = result.get("script") or context.get("prompt") or name
        title = summarizer.generate_title(script)
        res["title"] = title
        date = datetime.now().strftime("%Y-%m-%d")
        dest = f"{name}/{date}/{slugify(title)}.mp4"
        up = GCSManager().upload_file(video, dest)
        res["gcs_url"] = up["url"] if up else None
        print(f"[{name}] ☁️ upload: {dest} -> {res['gcs_url']}", flush=True)

        res["ok"] = True
        return res
    except Exception as e:
        res["error"] = str(e)
        print(f"[{name}] ❌ {e}", flush=True)
        return res


def main():
    ap = argparse.ArgumentParser(description="Pipeline agentic (brief -> vidéo par l'agent)")
    ap.add_argument("--only", action="append", metavar="CHANNEL",
                    help="Ne traiter que ce(s) channel(s) par nom (répétable). Défaut: tous. "
                         "Idéal pour un premier test isolé.")
    ap.add_argument("--list", action="store_true", help="Lister les channels disponibles et quitter.")
    args = ap.parse_args()

    if args.list:
        print("Channels disponibles :")
        for p in PIPELINES:
            print(f"  - {p['name']}  (skill={p['skill']})")
        return

    channels = PIPELINES
    if args.only:
        wanted = set(args.only)
        channels = [p for p in PIPELINES if p["name"] in wanted]
        missing = wanted - {p["name"] for p in channels}
        if missing:
            ap.error(f"channel(s) inconnu(s): {', '.join(sorted(missing))}. "
                     f"Dispo: {', '.join(p['name'] for p in PIPELINES)}")

    workers = max(1, len(channels))
    print("=" * 60)
    print(f"🎬 PIPELINE AGENTIC — {len(channels)} channel(s) en parallèle")
    print("=" * 60)

    with ThreadPoolExecutor(max_workers=workers) as ex:
        results = list(ex.map(process_channel, channels))

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

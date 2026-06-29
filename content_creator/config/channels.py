#!/usr/bin/env python3
"""
channels.py — Liste des channels traités par pipeline_agentic.

Chaque channel :
  - name   : identifiant (sert au log, à la dédup, au dossier de sortie)
  - url    : la page à scraper (liste d'articles)
  - skill  : le type de vidéo (skill de l'agent) — ex. "avatar_story"
  - avatar : chemin local OU URL d'une image d'avatar (requis pour avatar_story)

Tous les channels sont traités EN PARALLÈLE par la pipeline.
"""

PIPELINES = [
    {
        "name": "20min-foot",
        "url": "https://www.20minutes.fr/sport/football/",
        "skill": "avatar_story",
        "avatar": "image.png",
    },
    {
        "name": "20min-tennis",
        "url": "https://www.20minutes.fr/sport/tennis/",
        "skill": "avatar_story",
        "avatar": "image.png",
    },
]

#!/usr/bin/env python3
"""
processed.py — Journal local de dédup (articles déjà traités), partagé entre la pipeline
et le tool de scraping de l'agent.

Clé = namespace `name` (le channel) -> liste de hash d'URLs déjà consommées. Protégé par un
lock pour les traitements parallèles.
"""

import os
import json
import hashlib
import threading

PROCESSED_PATH = "runs/processed.json"
_lock = threading.Lock()


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

"""
routeur.py — Routeur d'inférence unique.

Toutes les requêtes (texte/agent, image, audio, plus tard vidéo) partent vers un
provider OpenAI-compatible via `AsyncOpenAI`. Le champ `task` choisit la méthode.

Concurrence : un `asyncio.Semaphore` plafonne les requêtes simultanées (le
"max_future") pour éviter OOM / rate-limit. Lancer autant de `send()` qu'on veut
(via `send_many` / `asyncio.gather`) : seules `max_concurrent` partent en même temps.
"""

from __future__ import annotations

import asyncio
import os

from openai import AsyncOpenAI
from pydantic import BaseModel

from .providers import get_provider


class Request(BaseModel):
    task: str                        # "text" | "image" | "audio" | "video"
    model: str
    prompt: str = ""
    images: list[str] = []
    voice: str | None = None         # pour audio
    size: str | None = None          # pour image
    provider: str = "common"         # nom du provider (un seul pour l'instant)


class Router:
    def __init__(self, max_concurrent: int | None = None):
        limit = max_concurrent or int(os.getenv("INFERENCE_MAX_CONCURRENT", "4"))
        self.sem = asyncio.Semaphore(limit)

    def _client(self, name: str) -> AsyncOpenAI:
        p = get_provider(name)
        return AsyncOpenAI(base_url=p.base_url, api_key=p.api_key)

    async def send(self, req: Request):
        """Envoie une requête et renvoie la réponse brute du SDK. Le sémaphore
        garantit qu'au plus `max_concurrent` requêtes sont en vol simultanément."""
        async with self.sem:
            client = self._client(req.provider)
            if req.task == "text":
                return await client.chat.completions.create(
                    model=req.model,
                    messages=[{"role": "user", "content": req.prompt}],
                )
            if req.task == "image":
                return await client.images.generate(
                    model=req.model, prompt=req.prompt, size=req.size or "1024x1024"
                )
            if req.task == "audio":
                return await client.audio.speech.create(
                    model=req.model, voice=req.voice or "alloy", input=req.prompt
                )
            if req.task == "video":
                raise NotImplementedError("video: à brancher (sglang) plus tard")
            raise ValueError(f"task inconnue: {req.task!r}")

    async def send_many(self, reqs: list[Request]) -> list:
        """Envoie un lot en parallèle ; le sémaphore régule la concurrence."""
        return await asyncio.gather(*(self.send(r) for r in reqs))

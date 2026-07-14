"""
main.py — App FastAPI du panneau de contrôle (POC local).

Lancement :
    uv run uvicorn control_panel.backend.main:app --reload --port 8080

Expose une API typée (OpenAPI en /openapi.json) consommée par le frontend React+TS.
Pas d'auth (POC), CORS ouvert au serveur de dev Vite (localhost:5173).
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from control_panel.backend.routers import catalog, channels, characters, runs, gallery

app = FastAPI(
    title="content_creator — Panneau de contrôle",
    description="Gérer channels, personnages, voix, model configs ; lancer des runs ; voir la galerie.",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173", "http://127.0.0.1:5173",
        "http://localhost:4173", "http://127.0.0.1:4173",
    ],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(catalog.router)
app.include_router(channels.router)
app.include_router(characters.router)
app.include_router(runs.router)
app.include_router(gallery.router)


@app.get("/api/health", tags=["health"])
def health() -> dict:
    return {"status": "ok"}

# Panneau de contrôle (POC local)

Interface pour gérer les **channels**, **personnages** (upload GCS), **voix**, **model
configs**, **lancer** la pipeline et voir la **galerie** des vidéos produites.

- **Backend** : FastAPI + Pydantic, réutilise `content_creator` (schéma typé, GCS, pipeline).
- **Frontend** : React + Vite + TypeScript, types générés depuis l'OpenAPI du backend.
- **Source de vérité** : `content_creator/config/channels.json` (éditée via l'API). Les
  **secrets** (tokens providers) restent dans le `.env` — jamais dans le JSON.
- Pas d'auth, storage GCS public (choix assumé du POC).

## Démarrer

### 1. Backend (port 8080)

```bash
uv sync --extra panel
uv run uvicorn control_panel.backend.main:app --reload --port 8080
```

Docs interactives : http://localhost:8080/docs · OpenAPI : http://localhost:8080/openapi.json

### 2. Frontend (port 5173)

```bash
cd control_panel/frontend
npm install
npm run dev
```

Ouvre http://localhost:5173. Le proxy Vite renvoie `/api/*` vers le backend (port 8080).

### Régénérer les types TS (après un changement d'API)

Backend démarré, puis :

```bash
cd control_panel/frontend && npm run gen-types
```

## Onglets

| Onglet | Rôle |
|--------|------|
| **Channels** | CRUD complet : skill, brief (prompt/mood), ressources, pool de modèles par rôle, personnages (voix + image). Validé côté serveur (skill/provider inconnus → 422). |
| **Personnages** | Upload d'images vers GCS (`avatars/`) + bibliothèque. L'URL sert d'`image` de personnage. |
| **Runs** | Lance un channel (`process_channel`) dans un thread, logs en direct (polling). |
| **Galerie** | Vidéos produites listées depuis GCS (`<channel>/<date>/*.mp4`). |
| **Catalogue** | Référence : skills, providers, voix (Chirp3/Gemini), modèles par défaut. |

## Architecture

```
content_creator/config/
  schema.py      modèles Pydantic (source de vérité) + load/save JSON + résolution providers
  channels.json  channels éditables (provider_id, pas de token)
  channels.py    loader : PIPELINES = [to_pipeline_config(c) for c in JSON]  (inchangé en aval)
control_panel/backend/
  main.py, store.py, run_manager.py, gcs_util.py, routers/{catalog,channels,characters,runs,gallery}.py
control_panel/frontend/
  src/api/{types.ts (généré), schemas.ts, client.ts}, src/components/*Tab.tsx, ChannelEditor.tsx
```

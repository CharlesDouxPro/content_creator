# content_creator

Agent agentique qui génère des vidéos verticales (TikTok/Insta) à partir d'un
article/script et d'un `--mood`. Le master (Claude Opus 4.8 via DeepInfra, function
calling) décide lui-même des plans (têtes parlantes + b-roll) et les assemble.

```bash
uv sync
uv run python -m content_creator.agentic.video_agent "Mon script..." \
    --avatar image.png --mood "dramatique, tendu" --scene
```

## Backend vidéo : DeepInfra (hébergé) ou serveur LTX-2.3 LOCAL

Le rendu de chaque plan peut être routé soit vers **DeepInfra** (Wan pour le
b-roll, Pruna pour les têtes parlantes), soit vers le **serveur LTX-2.3 local**
(cf. repo voisin `LTX-video-server`). Tout se configure dans le **`.env`**
(voir `.env.example`), chargé automatiquement par `config/config.py`.

| Variable | Effet |
|----------|-------|
| `USE_LTX_BROLL` | `true` → b-roll via LTX `POST /generate` (i2v) au lieu de Wan |
| `USE_LTX_LIPSYNC` | `true` → têtes parlantes via LTX `POST /generate` (i2v depuis le portrait) au lieu de Pruna |
| `LTX_SERVER_URL` | adresse du serveur LTX (défaut `http://localhost:8000`) |
| `LTX_TIMEOUT` / `LTX_WIDTH` / `LTX_HEIGHT` / `LTX_FRAME_RATE` / `LTX_HDR` | réglages de rendu LTX |

Les deux flags sont **indépendants** : on peut basculer le b-roll sur LTX tout en
gardant les têtes parlantes sur DeepInfra, ou inversement.

> **Note lip-sync.** Le serveur LTX n'expose pas d'équivalent direct du lip-sync
> « image + audio » de Pruna (son `/lipsync` attend une vidéo source + un dialogue
> texte). En mode `USE_LTX_LIPSYNC=true`, les têtes parlantes sont donc rendues en
> **image-to-video** depuis le portrait ; la narration TTS (Google) est conservée
> comme bande-son via ffmpeg.

DeepInfra reste utilisé dans tous les cas pour le **cerveau de l'agent** et pour
**FLUX Kontext** (génération du décor), indépendamment de ces flags.

### Démarrer le serveur LTX

Voir `../LTX-video-server/README.md`. En résumé : `./run_server.sh`, puis vérifier
`curl -s http://localhost:8000/health` → `{"ready":true,...}`. Le client LTX
(`agentic/ltx_client.py`) fait un `/health` en fail-fast avant chaque rendu, donc
un serveur éteint produit une erreur claire au lieu d'un timeout.

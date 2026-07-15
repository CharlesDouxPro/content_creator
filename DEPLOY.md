# Déploiement — content_creator sur un VPS (Docker + Caddy)

Mise en ligne simple : **1 VPS**, **Docker Compose**, **HTTPS automatique** (Caddy)
et **mot de passe** sur tout le site. Le front et l'API sont servis sur le même
domaine (aucun CORS à gérer).

> ⚠️ **Coût / sécurité.** L'app déclenche des générations qui consomment tes clés
> API payantes. Le mot de passe (étape 5) est **obligatoire** avant d'exposer le site.

---

## 1. Prérequis
- Un **nom de domaine** (ex. OVH, Namecheap, Cloudflare) — ~10 €/an.
- Un **VPS** (Hetzner CX22 ~4 €/mois, ou Scaleway/DigitalOcean). Tu obtiens une
  **IP publique**, ex. `203.0.113.42`. Prends au moins 2 vCPU / 4 Go de RAM.

## 2. DNS (une seule ligne)
Chez ton registrar, crée **un enregistrement A** :

| Type | Nom (hôte)        | Valeur (IP du VPS) |
|------|-------------------|--------------------|
| `A`  | `content-creator` | `203.0.113.42`     |

→ ton site sera `https://content-creator.tondomaine.com`. Propagation : quelques
minutes à ~1 h. (Pour la racine du domaine, mets `@` comme nom.)

## 3. Installer Docker sur le VPS
```bash
ssh root@203.0.113.42
curl -fsSL https://get.docker.com | sh
```

## 4. Récupérer le code + les secrets
```bash
git clone <URL_DE_TON_REPO> content_creator
cd content_creator

# Crée le .env (tes clés API) — copie ton .env local, NE PAS le committer.
nano .env

# Dépose le service account GCS à la racine.
nano api-key.json          # colle le JSON, ou scp depuis ta machine
```

## 5. Régler le domaine + le mot de passe (fichier `Caddyfile`)
```bash
# a) Génère le hash du mot de passe :
docker run --rm caddy caddy hash-password --plaintext 'TON_MOT_DE_PASSE'
#    -> copie la sortie $2a$14$....

# b) Édite le Caddyfile :
nano Caddyfile
#    - remplace "content-creator.tondomaine.com" par ton domaine
#    - remplace REMPLACE_PAR_LE_HASH par le hash ci-dessus
#      (et "admin" par l'identifiant voulu)
```

## 6. Lancer
```bash
docker compose up -d --build
docker compose logs -f          # Ctrl+C pour quitter les logs
```
Caddy récupère le certificat TLS automatiquement. Ouvre
`https://content-creator.tondomaine.com` → il demande le mot de passe. C'est en ligne.

## 7. Vérifier
```bash
curl -u admin:TON_MOT_DE_PASSE https://content-creator.tondomaine.com/api/health
# -> {"status":"ok"}
```

---

## Mettre à jour (après un `git push`)
```bash
git pull
docker compose up -d --build
```

## Notes
- **Données persistantes** : `content_creator/config/channels.json`, `runs/`,
  `avatars/` et les certificats (volume `caddy_data`) survivent aux rebuilds.
- **Runs interrompus** : un run en cours est perdu si le conteneur redémarre (POC).
- **LTX** : non hébergé. Garde `USE_LTX_BROLL=false` / `USE_LTX_LIPSYNC=false`
  dans `.env` (tout passe par DeepInfra).
- **Si les runs sont lents / OOM** : passe le VPS à une taille supérieure.

#!/usr/bin/env python3
"""
ltx_prompting.py — "Skill" de prompting du moteur de génération vidéo (LTX-2).

Expertise injectée dans le system prompt du master pour qu'il rédige des prompts
vidéo de qualité (les `shot_description` envoyés au moteur), en suivant le mood et
le format réseaux sociaux. Distillé de la doc de prompting LTX-2.

Le guide est désormais ADAPTÉ AU BACKEND ACTIF (cf. build_prompt_guide) : en mode
LTX local, le rendu se fait en IMAGE-TO-VIDEO (l'image fournit déjà la scène), donc
le prompt doit décrire le MOUVEMENT / la CAMÉRA / l'audio, PAS la scène. On injecte
aussi la résolution / le fps / la durée par défaut réellement utilisés.
"""

from content_creator.config.config import VIDEO_BACKEND_CONFIG


LTX_PROMPT_GUIDE = """\
# COMPÉTENCE — Prompting du moteur vidéo (LTX-2)

CONTEXTE : on produit du contenu COURT et VERTICAL (9:16) pour les RÉSEAUX SOCIAUX
(TikTok, Instagram Reels, Facebook, Shorts) : accrocheur, dynamique, lisible sur mobile.

MOOD : le mood/ton de la vidéo doit TRANSPARAÎTRE dans CHAQUE prompt vidéo — via la
lumière, la palette, le rythme, l'énergie de la caméra et l'attitude du sujet. Le mood prime.

Quand tu rédiges un prompt vidéo (ex. `shot_description`), applique la méthode LTX :

STRUCTURE — un seul paragraphe fluide, au PRÉSENT, CHRONOLOGIQUE (début → fin) :
1. Plan : terme de cinéma + échelle (close-up, medium, wide, low angle, over-the-shoulder, tracking…).
2. Scène : lumière, palette de couleurs, textures, atmosphère (golden hour, néons, brume, grain…).
3. Action : une séquence naturelle qui s'enchaîne du début à la fin.
4. Sujet : âge, cheveux, vêtements, détails distinctifs ; émotions par GESTES / POSTURE / VISAGE
   (jamais de label abstrait type "triste" ou "tendu").
5. Caméra : quand et comment elle bouge (slow dolly in, handheld tracking, pan left, static…)
   + à quoi ressemble le plan APRÈS le mouvement.
6. Audio (si pertinent) : ambiance, musique ; dialogues entre guillemets avec langue/accent.

RÈGLES :
- UN sujet, UNE action principale, UN comportement de caméra. UN SEUL plan continu (pas de multi-scènes).
- Chronologique, verbes au PRÉSENT, 4–8 phrases, < 200 mots.
- Adapte le niveau de détail à l'échelle (gros plan = plus de détails que plan large).
- Décris la RELATION caméra ↔ sujet pour les mouvements.
- Rédige le prompt vidéo en ANGLAIS (LTX y répond le mieux), même si la narration est en français.

À ÉVITER (sinon artefacts) :
- Labels émotionnels abstraits → montre l'émotion par la posture, les gestes, le visage.
- Texte ou logos lisibles (non fiables).
- Physique chaotique (sauts, jonglage) ; la danse passe bien.
- Surcharge (trop de sujets / actions / objets) → ça dilue le rendu.
- Éclairages contradictoires (ex. coucher de soleil chaud + néon froid) sauf intention claire.

VOCABULAIRE UTILE (pioche dedans pour rester concret) :
- Caméra : follows, tracks, pans across, circles around, tilts up, push in / pull back, overhead,
  handheld, over-the-shoulder, wide establishing shot, static frame, slow dolly in.
- Échelle / rythme : intimate, epic, claustrophobic ; slow motion, time-lapse, lingering shot,
  continuous shot, seamless transition, sudden stop.
- Lumière : natural sunlight, golden hour, neon glow, flickering candles, dramatic shadows, rim / backlight.
- Atmosphère / texture : fog, rain, dust, smoke, particles ; rough stone, smooth metal, worn fabric, glossy.
- Palette : vibrant, muted, monochromatic, high contrast.
- Style (nomme-le TÔT) : documentary, film noir, thriller, modern romance, fashion editorial,
  painterly, cyberpunk, 2D/3D animation, claymation.
- VFX : motion blur, depth of field, lens flares, film grain, particle systems.

EXEMPLE (prompt b-roll au format cible, en anglais) :
"Cinematic medium shot, golden-hour light raking across an empty football stadium. The camera slowly
pushes in past the touchline as the man from Image 1, in a dark suit, walks toward the pitch, hands in
pockets, gaze fixed ahead. Warm rim light catches his shoulders; dust motes drift in the air. The crowd
stands blurred and quiet in the background. Calm, contemplative atmosphere, shallow depth of field."

DIALOGUE (si le plan parle) : mets le texte entre guillemets, précise langue/accent, et découpe en
courtes répliques avec une indication de jeu (geste, pause, regard) entre chaque.
"""


# Tools qui déclenchent l'injection du LIPDUB_GUIDE (doublage vidéo→vidéo, IC-LoRA).
# Tant qu'aucun tool lipdub n'est enregistré, le guide reste dormant (pas de référence
# fantôme dans le system prompt). Ajoute ici le nom du tool le jour où tu l'exposes.
LIPDUB_TOOL_NAMES = frozenset({"add_lipdub_clip"})


# Règle TOUJOURS injectée quand le skill peut faire parler un avatar à l'écran
# (tool `add_talking_clip` disponible) : un personnage qui parle À L'IMAGE doit
# TOUJOURS être lip-syncé, quel que soit le type de vidéo.
LIPSYNC_TOOL_NAMES = frozenset({"add_talking_clip"})

LIPSYNC_POLICY = """\
# LIP-SYNC — un personnage qui PARLE À L'ÉCRAN a TOUJOURS les lèvres synchronisées

RÈGLE (prioritaire, quel que soit le type de vidéo) : dès qu'un personnage possédant un
avatar (portrait) doit PARLER À L'IMAGE, planifie ce segment avec `add_talking_clip`
(lip-sync). Ne le montre JAMAIS en train de parler via un plan b-roll ou média sans
lip-sync : des lèvres désynchronisées sont un artefact rédhibitoire.

La VOIX OFF (b-roll / média sans lip-sync) reste réservée aux passages où le personnage
n'est PAS visible en train de parler : narration hors-champ, plans d'ambiance,
illustration. Si le sujet parle et qu'on le voit, c'est `add_talking_clip`."""


LIPDUB_GUIDE = """\
# COMPÉTENCE — LipDub (remplacement de voix, vidéo→vidéo, IC-LoRA)

LipDub remplace le dialogue parlé dans une vidéo SOURCE existante (doublage vers une autre langue,
OU reformulation dans la langue d'origine). Ce n'est PAS du text-to-video : tu fournis une vidéo
source + un prompt décrivant ce que le locuteur doit dire à la place.

Langues validées : anglais, français, espagnol, allemand, russe.

GABARIT DE PROMPT :
  [Speaker] is speaking [Langue/Accent], saying: "[Dialogue]"

EXEMPLE :
  A woman speaking in French saying: "Aujourd'hui est une superbe journée pour tester LTX."
(Tu peux ajouter des précisions d'émotion ou de débit.)

EXIGENCES :
- Fournis le dialogue COMPLET — le modèle suit le texte du prompt, il ne TRADUIT pas pour toi.
- Écris en SCRIPT NATIF de la langue cible (ex. cyrillique pour le russe, caractères chinois pour le mandarin).
- UN SEUL locuteur (l'IC-LoRA bêta ne distingue pas plusieurs locuteurs).

BONNES PRATIQUES :
- Cale la LONGUEUR / le nombre de SYLLABES sur le dialogue d'origine (un peu plus long > trop court) :
  - prompt trop long  → le modèle peut sauter des mots ;
  - prompt trop court → le rendu paraît lent et peu naturel.
"""


# Section injectée UNIQUEMENT quand le rendu se fait en image-to-video sur le serveur
# LTX local (USE_LTX_BROLL / USE_LTX_LIPSYNC). En i2v, l'IMAGE fournit déjà la scène
# (décor, sujet, palette) : décrire à nouveau la scène entre en conflit avec l'image.
_LTX_I2V_GUIDE = """\
# MODE ACTIF — serveur LTX local : DEUX RÉGIMES selon le plan

Règle d'or : quand le moteur part d'une IMAGE de référence (image-to-video), l'image FOURNIT
DÉJÀ le décor + le sujet + l'apparence. Redécrire tout ça dans le prompt ENTRE EN CONFLIT avec
l'image → visages déformés, scènes surréalistes. C'est LA cause n°1 d'artefacts. Distingue donc :

1) PLAN AVEC RÉFÉRENCE D'IMAGE (tête parlante `add_talking_clip` ; b-roll `add_broll_clip`
   AVEC `character` ; b-roll avec `reference_image`) → IMAGE-TO-VIDEO.
   Dans le prompt, décris UNIQUEMENT :
   - le MOUVEMENT du sujet (gestes, démarche, regard qui se tourne…),
   - la CAMÉRA (slow push in, handheld tracking, pan, static…) et l'état du plan APRÈS,
   - l'AUDIO/l'ambiance si pertinent.
   NE redécris PAS le décor, les vêtements, le visage : ils viennent de l'image.
   Exemple : "The camera slowly pushes in as the subject turns toward the lens and gives a
   calm, confident nod; subtle natural motion, shallow depth of field, soft room tone."

2) PLAN D'AMBIANCE / CUTAWAY SANS ton personnage (stade, foule, objet, paysage…) → appelle
   `add_broll_clip` SANS `character` : c'est du TEXT-TO-VIDEO, le moteur génère tout depuis ton
   texte. LÀ tu décris la SCÈNE COMPLÈTE (cadrage + lumière + palette + action + caméra), en
   détail (prompt long = meilleur rendu).
   Exemple : "Cinematic wide shot inside a packed stadium at night, vibrant floodlights, fans in
   colorful jerseys waving flags and chanting, confetti drifting, handheld camera sweeping across
   the crowd, shallow depth of field, electric festive atmosphere, roaring crowd ambience."

NE JAMAIS mettre ton personnage (`character`) sur un plan d'ambiance où il n'apparaît pas : ancrer
un portrait studio à une scène de stade produit un rendu déformé. Reste bref en i2v (2–4 phrases),
détaillé en t2v.

PARAMÈTRES PAR PLAN (optionnels) — `add_talking_clip` / `add_broll_clip` acceptent :
- `duration_s` : durée du plan (défaut = longueur de la narration). Étire un plan
  d'ambiance, raccourcis une punchline. Reste dans 2–10 s.
- `image_strength` (i2v, 0–1) : adhérence à l'image de réf. 1.0 = très fidèle (peu de
  mouvement) ; 0.7–0.85 = plus de liberté de mouvement/caméra. Pour du b-roll vivant,
  préfère ~0.8 ; pour une tête parlante stable, garde ~1.0.
- `hdr: true` : passe de raffinement (≈2× plus lent) — réserve aux plans CLÉS.
- `num_inference_steps` : qualité/temps (défaut 30). Monte (40–50) seulement si demandé.
- `width`/`height`/`frame_rate` : NE LES CHANGE QUE si explicitement demandé — des
  tailles hétérogènes compliquent l'assemblage final (concat).
N'envoie un paramètre QUE si tu veux dévier du défaut ; sinon laisse-le vide."""


def _format_specs() -> str:
    """Bloc rappelant les contraintes de FORMAT réellement appliquées (résolution,
    fps, durée par défaut). Permet au master de prompter en cohérence avec le rendu."""
    c = VIDEO_BACKEND_CONFIG
    w, h, fr = c["ltx_width"], c["ltx_height"], c["ltx_frame_rate"]
    return f"""\
# FORMAT RÉELLEMENT RENDU (respecte-le dans tes prompts)
- Cadre VERTICAL 9:16 — {w}×{h}px @ {fr:g} fps. Compose pour le mobile : sujet centré/haut,
  marge en bas pour les sous-titres, action lisible en petit.
- DURÉE d'un plan : par défaut calée sur la longueur de sa narration. Tu peux la forcer
  via `duration_s` (ex. un plan d'ambiance plus long) — elle sera arrondie au format
  valide du moteur (8k+1 frames). Garde des plans COURTS (2–10 s) pour le format réseaux.
- Le moteur arrondit la résolution à un multiple de 64 ; n'essaie pas de la pré-ajuster.
"""


def build_prompt_guide(tool_names=None) -> str:
    """Assemble le guide de prompting ADAPTÉ AU BACKEND ACTIF et aux TOOLS du skill.

    - DeepInfra (défaut) : guide classique (l'image de réf existe aussi côté Wan, mais
      la doc historique reste valable) + rappel de format.
    - LTX local (i2v) : ajoute la section i2v (mouvement/caméra, pas la scène).
    - LipDub : ajoute LIPDUB_GUIDE UNIQUEMENT si le skill expose un tool lipdub
      (cf. LIPDUB_TOOL_NAMES) — sinon le guide reste dormant (pas de référence fantôme).
    `tool_names` = liste des tools du skill (None => tous les tools enregistrés)."""
    c = VIDEO_BACKEND_CONFIG
    parts = [LTX_PROMPT_GUIDE, _format_specs()]
    if c["use_ltx_broll"] or c["use_ltx_lipsync"]:
        parts.append(_LTX_I2V_GUIDE)
    # tool_names=None => créateur libre (accès à TOUS les tools) : on résout la liste
    # réelle des tools enregistrés pour décider des sections conditionnelles.
    from content_creator.agentic.video_tools import TOOLS
    available = set(tool_names) if tool_names is not None else set(TOOLS)
    # Politique lip-sync : dès que le skill peut faire parler un avatar à l'écran.
    if available & LIPSYNC_TOOL_NAMES:
        parts.append(LIPSYNC_POLICY)
    # LipDub (doublage vidéo→vidéo) : uniquement si un tool lipdub est enregistré.
    if available & LIPDUB_TOOL_NAMES:
        parts.append(LIPDUB_GUIDE)
    return "\n\n".join(parts)


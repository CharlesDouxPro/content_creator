#!/usr/bin/env python3
"""
ltx_prompting.py — Assemblage du guide injecté dans le system prompt du master + bits FORMAT/backend.

`build_prompt_guide()` compose ce que voit le master : (1) le skill de PROMPTING pioché SELON LE
MODÈLE `video_generator` (délégué à model_prompting ; défaut = `ltx`, grammaire cinématographique
générique — ce module n'héberge plus le texte, il vit dans model_skills/ltx/), (2) les contraintes
de FORMAT qui dépendent du BACKEND (rendu LTX local i2v : specs LTX + régime image-to-video ; sinon
rappel 9:16 social générique), (3) les politiques de TOOLS agnostiques (lip-sync, LipDub).

Le FORMAT reste ici (et non dans model_skills) car il dépend de `.env`/VIDEO_BACKEND_CONFIG et du
backend de rendu, pas du modèle.
"""

from content_creator.config.config import VIDEO_BACKEND_CONFIG




# Tools qui déclenchent l'injection du LIPDUB_GUIDE (doublage vidéo→vidéo, IC-LoRA).
# Tant qu'aucun tool lipdub n'est enregistré, le guide reste dormant (pas de référence
# fantôme dans le system prompt). Ajoute ici le nom du tool le jour où tu l'exposes.
LIPDUB_TOOL_NAMES = frozenset({"add_lipdub_clip"})


# Règle TOUJOURS injectée quand le skill peut faire parler un avatar à l'écran
# (tool `add_talking_clip` disponible) : un personnage qui parle À L'IMAGE doit
# TOUJOURS être lip-syncé, quel que soit le type de vidéo.
LIPSYNC_TOOL_NAMES = frozenset({"add_talking_clip"})

LIPSYNC_POLICY = """\
# LIP-SYNC — a character who SPEAKS ON SCREEN ALWAYS has synced lips

RULE (top priority, whatever the video type): as soon as a character that has an
avatar (portrait) must SPEAK ON SCREEN, plan that segment with `add_talking_clip`
(lip-sync). NEVER show them speaking via a b-roll or media shot without
lip-sync: out-of-sync lips are a dealbreaker artifact.

The VOICE-OVER (b-roll / media without lip-sync) stays reserved for passages where the character
is NOT visibly speaking: off-screen narration, ambience shots,
illustration. If the subject speaks and is on screen, it's `add_talking_clip`."""


LIPDUB_GUIDE = """\
# SKILL — LipDub (voice replacement, video→video, IC-LoRA)

LipDub replaces the spoken dialogue in an existing SOURCE video (dubbing into another language,
OR rephrasing in the original language). This is NOT text-to-video: you provide a source video
+ a prompt describing what the speaker should say instead.

Validated languages: English, French, Spanish, German, Russian.

PROMPT TEMPLATE:
  [Speaker] is speaking [Language/Accent], saying: "[Dialogue]"

EXAMPLE:
  A woman speaking in French saying: "Aujourd'hui est une superbe journée pour tester LTX."
(You can add emotion or delivery details.)

REQUIREMENTS:
- Provide the FULL dialogue — the model follows the prompt text, it does NOT translate for you.
- Write in the NATIVE SCRIPT of the target language (e.g. Cyrillic for Russian, Chinese characters for Mandarin).
- A SINGLE speaker (the beta IC-LoRA does not distinguish multiple speakers).

BEST PRACTICES:
- Match the LENGTH / SYLLABLE count to the original dialogue (slightly longer > too short):
  - prompt too long  → the model may skip words;
  - prompt too short → the result looks slow and unnatural.
"""


# Section injectée UNIQUEMENT quand le rendu se fait en image-to-video sur le serveur
# LTX local (USE_LTX_BROLL / USE_LTX_LIPSYNC). En i2v, l'IMAGE fournit déjà la scène
# (décor, sujet, palette) : décrire à nouveau la scène entre en conflit avec l'image.
_LTX_I2V_GUIDE = """\
# ACTIVE MODE — local LTX server: TWO REGIMES depending on the shot

Golden rule: when the engine starts from a reference IMAGE (image-to-video), the image ALREADY
PROVIDES the background + the subject + the appearance. Re-describing all of that in the prompt CONFLICTS with
the image → distorted faces, surreal scenes. It is THE #1 cause of artifacts. So distinguish:

1) SHOT WITH AN IMAGE REFERENCE (talking head `add_talking_clip`; b-roll `add_broll_clip`
   WITH `character`; b-roll with `reference_image`) → IMAGE-TO-VIDEO.
   In the prompt, describe ONLY:
   - the subject's MOVEMENT (gestures, gait, gaze turning…),
   - the CAMERA (slow push in, handheld tracking, pan, static…) and the state of the shot AFTER,
   - the AUDIO/ambience if relevant.
   Do NOT re-describe the background, the clothing, the face: they come from the image.
   Example: "The camera slowly pushes in as the subject turns toward the lens and gives a
   calm, confident nod; subtle natural motion, shallow depth of field, soft room tone."

2) AMBIENCE / CUTAWAY SHOT WITHOUT your character (stadium, crowd, object, landscape…) → call
   `add_broll_clip` WITHOUT `character`: this is TEXT-TO-VIDEO, the engine generates everything from your
   text. THERE you describe the FULL SCENE (framing + light + palette + action + camera), in
   detail (long prompt = better result).
   Example: "Cinematic wide shot inside a packed stadium at night, vibrant floodlights, fans in
   colorful jerseys waving flags and chanting, confetti drifting, handheld camera sweeping across
   the crowd, shallow depth of field, electric festive atmosphere, roaring crowd ambience."

NEVER put your character (`character`) on an ambience shot where they do not appear: anchoring
a studio portrait to a stadium scene produces a distorted result. Stay brief in i2v (2–4 sentences),
detailed in t2v.

PER-SHOT PARAMETERS (optional) — `add_talking_clip` / `add_broll_clip` accept:
- `duration_s`: shot duration (default = narration length). Stretch an ambience
  shot, shorten a punchline. Stay within 2–10 s.
- `image_strength` (i2v, 0–1): adherence to the reference image. 1.0 = very faithful (little
  movement); 0.7–0.85 = more movement/camera freedom. For lively b-roll,
  prefer ~0.8; for a stable talking head, keep ~1.0.
- `hdr: true`: refinement pass (≈2× slower) — reserve for KEY shots.
- `num_inference_steps`: quality/time (default 30). Raise it (40–50) only if requested.
- `width`/`height`/`frame_rate`: ONLY CHANGE THEM if explicitly requested — heterogeneous
  sizes complicate the final assembly (concat).
Only send a parameter IF you want to deviate from the default; otherwise leave it empty."""


def _format_specs() -> str:
    """Bloc rappelant les contraintes de FORMAT réellement appliquées (résolution,
    fps, durée par défaut). Permet au master de prompter en cohérence avec le rendu."""
    c = VIDEO_BACKEND_CONFIG
    w, h, fr = c["ltx_width"], c["ltx_height"], c["ltx_frame_rate"]
    return f"""\
# FORMAT ACTUALLY RENDERED (respect it in your prompts)
- VERTICAL 9:16 frame — {w}×{h}px @ {fr:g} fps. Compose for mobile: subject centered/high,
  margin at the bottom for subtitles, action readable at small size.
- SHOT DURATION: by default matched to its narration length. You can force it
  via `duration_s` (e.g. a longer ambience shot) — it will be rounded to the engine's
  valid format (8k+1 frames). Keep shots SHORT (2–10 s) for the social format.
- The engine rounds the resolution to a multiple of 64; do not try to pre-adjust it.
"""


# Rappel de FORMAT générique (moteurs non-LTX) : _format_specs() ci-dessus est propre à LTX
# (clés ltx_* de VIDEO_BACKEND_CONFIG). Pour un moteur qui apporte son propre skill de
# prompting (cf. model_prompting), on ne garde que la contrainte réseaux sociaux, agnostique.
_SOCIAL_FORMAT_NOTE = """\
# FORMAT — vertical 9:16 for social media (respect it in your prompts)
Compose for MOBILE: VERTICAL 9:16 frame, subject centered/high, margin at the bottom for
subtitles, action readable at small size. Keep shots SHORT (2–10 s) and the pacing dynamic.
"""


def build_prompt_guide(tool_names=None, video_model=None) -> str:
    """Assemble le guide injecté dans le system prompt : PROMPTING (par modèle) + FORMAT
    (par backend) + politiques de TOOLS (agnostiques).

    - PROMPTING : piochée SELON LE MODÈLE `video_generator` dans model_skills/<model>/ (cf.
      model_prompting.load_prompting_guide). Modèle sans skill dédié -> prompting par défaut
      (`ltx`, grammaire cinématographique générique valable aussi Wan). Inclut le CATALOGUE de
      styles si le modèle en a (le master en choisit un et le charge via `load_style_skill`).
    - FORMAT (dépend du BACKEND, pas du modèle) : en rendu LTX local (i2v), specs LTX (résolution/
      fps depuis .env) + section i2v (mouvement/caméra, pas la scène) ; sinon rappel 9:16 social générique.
    - LipDub / lip-sync : politiques AGNOSTIQUES au moteur, ajoutées selon les TOOLS du skill.

    `tool_names` = liste des tools du skill (None => tous les tools enregistrés).
    `video_model` = ModelConfig du rôle video_generator (aiguille le skill de prompting du moteur)."""
    c = VIDEO_BACKEND_CONFIG
    is_ltx_render = c["use_ltx_broll"] or c["use_ltx_lipsync"]
    # 1) PROMPTING — piochée selon le modèle (défaut = ltx). Toujours non vide.
    from content_creator.agentic.model_prompting import load_prompting_guide
    parts = [load_prompting_guide(video_model)]
    # 2) FORMAT — selon le BACKEND de rendu. LTX local : specs LTX (+ régime i2v) ; sinon générique.
    if is_ltx_render:
        parts += [_format_specs(), _LTX_I2V_GUIDE]
    else:
        parts.append(_SOCIAL_FORMAT_NOTE)
    # 3) POLITIQUES DE TOOLS (agnostiques). tool_names=None => créateur libre (TOUS les tools) :
    # on résout la liste réelle des tools enregistrés pour décider des sections conditionnelles.
    from content_creator.agentic.video_tools import TOOLS
    available = set(tool_names) if tool_names is not None else set(TOOLS)
    # Politique lip-sync : dès que le skill peut faire parler un avatar à l'écran.
    if available & LIPSYNC_TOOL_NAMES:
        parts.append(LIPSYNC_POLICY)
    # LipDub (doublage vidéo→vidéo) : uniquement si un tool lipdub est enregistré.
    if available & LIPDUB_TOOL_NAMES:
        parts.append(LIPDUB_GUIDE)
    return "\n\n".join(parts)


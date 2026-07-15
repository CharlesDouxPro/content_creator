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
# SKILL — Video engine prompting (LTX-2)

CONTEXT: we produce SHORT and VERTICAL content (9:16) for SOCIAL MEDIA
(TikTok, Instagram Reels, Facebook, Shorts): catchy, dynamic, readable on mobile.

MOOD: the video's mood/tone must SHOW THROUGH in EVERY video prompt — via the
light, the palette, the pacing, the camera energy and the subject's attitude. The mood prevails.

LANGUAGE (mandatory): ALWAYS write the video prompts (e.g. `shot_description`, and any
movement/camera description you send to the engine) in ENGLISH — LTX responds best to English.
This is independent of the narration language: the SPOKEN text (`text` in add_talking_clip,
`narration_text` in add_broll_clip/add_media_clip) stays in the video's language (e.g. French)
and must NOT be translated. Only the visual prompt is in English.

When you write a video prompt (e.g. `shot_description`), apply the LTX method:

STRUCTURE — a single flowing paragraph, in the PRESENT tense, CHRONOLOGICAL (start → end):
1. Shot: cinema term + scale (close-up, medium, wide, low angle, over-the-shoulder, tracking…).
2. Scene: light, color palette, textures, atmosphere (golden hour, neon, mist, grain…).
3. Action: a natural sequence that flows from start to end.
4. Subject: age, hair, clothing, distinctive details; emotions through GESTURES / POSTURE / FACE
   (never an abstract label like "sad" or "tense").
5. Camera: when and how it moves (slow dolly in, handheld tracking, pan left, static…)
   + what the shot looks like AFTER the movement.
6. Audio (if relevant): ambience, music; dialogue in quotes with language/accent.

RULES:
- ONE subject, ONE main action, ONE camera behavior. ONE SINGLE continuous shot (no multi-scenes).
- Chronological, verbs in the PRESENT tense, 4–8 sentences, < 200 words.
- Match the level of detail to the scale (close-up = more detail than a wide shot).
- Describe the camera ↔ subject RELATIONSHIP for movements.
- Write the video prompt in ENGLISH (LTX responds best to it), even if the narration is in French.

TO AVOID (otherwise artifacts):
- Abstract emotional labels → show the emotion through posture, gestures, face.
- Readable text or logos (unreliable).
- Chaotic physics (jumps, juggling); dancing works well.
- Overload (too many subjects / actions / objects) → it dilutes the result.
- Contradictory lighting (e.g. warm sunset + cold neon) unless clearly intended.

USEFUL VOCABULARY (draw from it to stay concrete):
- Camera: follows, tracks, pans across, circles around, tilts up, push in / pull back, overhead,
  handheld, over-the-shoulder, wide establishing shot, static frame, slow dolly in.
- Scale / pacing: intimate, epic, claustrophobic; slow motion, time-lapse, lingering shot,
  continuous shot, seamless transition, sudden stop.
- Light: natural sunlight, golden hour, neon glow, flickering candles, dramatic shadows, rim / backlight.
- Atmosphere / texture: fog, rain, dust, smoke, particles; rough stone, smooth metal, worn fabric, glossy.
- Palette: vibrant, muted, monochromatic, high contrast.
- Style (name it EARLY): documentary, film noir, thriller, modern romance, fashion editorial,
  painterly, cyberpunk, 2D/3D animation, claymation.
- VFX: motion blur, depth of field, lens flares, film grain, particle systems.

EXAMPLE (b-roll prompt in the target format, in English):
"Cinematic medium shot, golden-hour light raking across an empty football stadium. The camera slowly
pushes in past the touchline as the man from Image 1, in a dark suit, walks toward the pitch, hands in
pockets, gaze fixed ahead. Warm rim light catches his shoulders; dust motes drift in the air. The crowd
stands blurred and quiet in the background. Calm, contemplative atmosphere, shallow depth of field."

DIALOGUE (if the shot speaks): put the text in quotes, specify language/accent, and break it into
short lines with an acting cue (gesture, pause, glance) between each.
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


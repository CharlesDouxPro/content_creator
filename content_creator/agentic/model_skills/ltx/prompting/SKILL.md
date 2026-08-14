---
name: ltx-prompt-writing
description: Write high-quality video prompts for the LTX-2 engine (and other cinematic i2v/t2v engines). Generic cinematic prompting grammar — used by default when the generation model has no dedicated prompt-writing skill.
---

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

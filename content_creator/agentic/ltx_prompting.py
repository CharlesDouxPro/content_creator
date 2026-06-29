#!/usr/bin/env python3
"""
ltx_prompting.py — "Skill" de prompting du moteur de génération vidéo (LTX-2).

Expertise injectée dans le system prompt du master pour qu'il rédige des prompts
vidéo de qualité (les `shot_description` envoyés au moteur), en suivant le mood et
le format réseaux sociaux. Distillé de la doc de prompting LTX-2.

(Le branchement technique réel sur l'API LTX viendra plus tard ; ici on outille le
master pour qu'il PROMPTE bien le moteur.)
"""

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

LIPDUB (doublage / remplacement de voix, vidéo→vidéo) : workflow DISTINCT — voir LIPDUB_GUIDE.
"""


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


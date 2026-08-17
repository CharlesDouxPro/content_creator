#!/usr/bin/env python3
"""
humanizer.py — Guide anti-« écriture IA » pour les scripts narratifs.

Adapté du skill `humanizer` (basé sur Wikipedia "Signs of AI writing", WikiProject AI Cleanup)
au cas d'usage réel ici : de courts scripts PARLÉS pour TikTok / Reels / Shorts. On garde les
tells qui s'entendent à l'oral et on laisse tomber les règles propres à la prose encyclopédique.

`HUMANIZER_GUIDE` est un bloc à injecter tel quel dans le system prompt d'écriture de script
(cf. ArticleSummarizer.generate_prompt, usage="summary"). Le script reste dans la langue voulue.
"""

# Bloc injecté dans le prompt système du rédacteur de script. Rédigé en français car les scripts
# le sont ; les consignes s'appliquent quelle que soit la langue de sortie.
HUMANIZER_GUIDE = """\
Écris comme un humain, pas comme une IA. Un script qui « sent » l'IA fait fuir. Évite ces tells :

- VOCABULAIRE IA : bannis les mots-tics « plonger/plongeons », « il est important de noter »,
  « souligne/met en lumière », « témoigne de », « paysage » (au figuré), « riche », « vibrant »,
  « fascinant », « incontournable », « au cœur de », « véritable », « à l'ère de ». Dis les choses
  simplement.
- PAS D'EMPHASE CREUSE sur l'importance ou l'héritage : pas de « marque un tournant »,
  « restera dans les mémoires », « change la donne », « plus qu'un X, c'est un Y ». Donne le fait,
  pas son auréole.
- PAS DE PARTICIPES EMPILÉS pour faire profond : évite les fins de phrase en « ..., soulignant... »,
  « ..., rappelant... », « ..., témoignant de... ». Fais une vraie phrase.
- PAS DE RÈGLE DE TROIS automatique : n'aligne pas systématiquement trois adjectifs ou trois idées
  pour faire complet. Deux suffisent souvent ; une seule, bien choisie, frappe plus fort.
- VARIE LA LONGUEUR DES PHRASES. Alterne court et long. Une IA écrit des phrases toutes de la même
  taille. Mais n'enchaîne pas non plus les phrases hyper courtes en rafale pour dramatiser : une
  punchline isolée, oui ; dix d'affilée, non.
- PAS DE PARALLÉLISMES NÉGATIFS ni de négations en fin de phrase : évite « ce n'est pas seulement...,
  c'est... » et les bouts collés du genre « sans chichi », « sans détour ».
- PAS D'ATTRIBUTIONS VAGUES : pas de « les experts affirment », « selon certaines sources », « des
  observateurs notent ». Nomme la source réelle de l'article, ou n'attribue rien.
- PAS DE CONCLUSION BATEAU ni de « signposting » : ne finis pas sur « l'avenir s'annonce
  radieux » / « une chose est sûre... », et n'annonce pas ce que tu vas dire (« découvrons
  ensemble », « voici ce qu'il faut savoir »). Dis-le, c'est tout.
- PAS D'INVENTION : aucun fait, chiffre, date, nom ou citation qui ne soit pas dans l'article.
- TON HUMAIN : une vraie voix, un point de vue, un peu d'aspérité. Guillemets droits, pas d'emoji.
- ZÉRO TIRET CADRATIN (—) ni tiret demi-cadratin (–) : c'est le tell IA le plus reconnaissable.
  Remplace par un point, une virgule, deux-points ou une parenthèse. Relis et supprime-les tous.
"""

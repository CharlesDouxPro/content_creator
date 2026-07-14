// Helpers de construction/normalisation des channels côté UI.
import type { Channel, Character, ModelPool, ModelsInfo } from './api/schemas'
import { ROLES } from './api/schemas'

// Formes "pleines" (tous les champs présents) pour éditer sans se battre avec les
// optionnels du schéma OpenAPI (défauts Pydantic -> champs non requis).
export interface FullRessources {
  urls: string[]
  local_paths: string[]
  audio_paths: string[]
  notes: string | null
}
export interface FullContext {
  prompt: string
  mood: string
  ressources: FullRessources
  characters: Record<string, Character>
}
export interface FullChannel {
  name: string
  skill: string
  models: ModelPool
  context: FullContext
}

/** Complète un Channel (issu de l'API) en FullChannel éditable (structurellement un Channel). */
export function normalize(c: Channel): FullChannel {
  const ctx = c.context
  const r = ctx?.ressources
  return {
    name: c.name,
    skill: c.skill,
    models: c.models,
    context: {
      prompt: ctx?.prompt ?? '',
      mood: ctx?.mood ?? '',
      ressources: {
        urls: r?.urls ?? [],
        local_paths: r?.local_paths ?? [],
        audio_paths: r?.audio_paths ?? [],
        notes: r?.notes ?? null,
      },
      characters: ctx?.characters ?? {},
    },
  }
}

/** Pool de modèles par défaut à partir du catalogue (defaults renvoyés par l'API). */
export function defaultPool(models: ModelsInfo): ModelPool {
  const pool = {} as ModelPool
  for (const role of ROLES) {
    const d = models.defaults[role]
    pool[role] = { model_name: d.model_name, provider_id: d.provider_id }
  }
  return pool
}

/** Channel vierge prêt à éditer. */
export function blankChannel(skill: string, models: ModelsInfo): Channel {
  return {
    name: '',
    skill,
    models: defaultPool(models),
    context: {
      prompt: '',
      mood: '',
      ressources: { urls: [], local_paths: [], audio_paths: [], notes: null },
      characters: {},
    },
  }
}

/** Copie profonde simple (structures JSON only) — pour éditer sans muter l'état source. */
export function clone<T>(v: T): T {
  return JSON.parse(JSON.stringify(v))
}

/** Compose un nom de voix Chirp3 complet : composeChirp3("Kore","fr-FR") -> "fr-FR-Chirp3-HD-Kore". */
export function composeChirp3(name: string, lang: string): string {
  return `${lang}-Chirp3-HD-${name}`
}

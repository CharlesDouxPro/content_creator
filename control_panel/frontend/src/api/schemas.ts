// Aliases typés dérivés du schéma OpenAPI (généré depuis les modèles Pydantic).
// Régénérer types.ts : `npm run gen-types` (backend démarré).
import type { components } from './types'

type S = components['schemas']

export type Channel = S['Channel']
export type ModelPool = S['ModelPool']
export type ModelSpec = S['ModelSpec']
export type Character = S['Character']
export type Context = S['Context']
export type Ressources = S['Ressources']

export type SkillInfo = S['SkillInfo']
export type ProviderInfo = S['ProviderInfo']
export type VoicesInfo = S['VoicesInfo']
export type ModelsInfo = S['ModelsInfo']
export type CharacterAsset = S['CharacterAsset']
export type VideoItem = S['VideoItem']
export type RunInfo = S['RunInfo']
export type ElevenLabsVoice = S['ElevenLabsVoice']

// Credentials des providers d'inférence (base_url + api_key, définis une seule fois).
// Manuels (pas encore dans types.ts) : régénérer types.ts pour les dériver.
export type ProviderConfig = { name: string; base_url: string; api_key_set: boolean }
export type ProviderUpdate = { base_url: string; api_key: string }

export type Role = keyof ModelPool
export const ROLES: Role[] = [
  'master_mind', 'slm', 'lip_sync', 'video_generator', 'voice_generator',
]

// Petit client fetch typé. Toutes les routes sont relatives (proxy Vite -> FastAPI).
import type {
  Channel, SkillInfo, ProviderInfo, VoicesInfo, ModelsInfo,
  CharacterAsset, VideoItem, RunInfo, ElevenLabsVoice,
} from './schemas'

async function req<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, {
    headers: { 'Content-Type': 'application/json' },
    ...init,
  })
  if (!res.ok) {
    let detail = res.statusText
    try {
      const body = await res.json()
      detail = typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail)
    } catch { /* ignore */ }
    throw new Error(`${res.status} — ${detail}`)
  }
  if (res.status === 204) return undefined as T
  return res.json() as Promise<T>
}

export const api = {
  // Catalogue
  skills: () => req<SkillInfo[]>('/api/catalog/skills'),
  providers: () => req<ProviderInfo[]>('/api/catalog/providers'),
  voices: () => req<VoicesInfo>('/api/catalog/voices'),
  models: () => req<ModelsInfo>('/api/catalog/models'),
  elevenLabsVoices: () => req<ElevenLabsVoice[]>('/api/catalog/elevenlabs-voices'),

  // Channels
  listChannels: () => req<Channel[]>('/api/channels'),
  createChannel: (c: Channel) => req<Channel>('/api/channels', { method: 'POST', body: JSON.stringify(c) }),
  updateChannel: (name: string, c: Channel) =>
    req<Channel>(`/api/channels/${encodeURIComponent(name)}`, { method: 'PUT', body: JSON.stringify(c) }),
  deleteChannel: (name: string) =>
    req<void>(`/api/channels/${encodeURIComponent(name)}`, { method: 'DELETE' }),

  // Personnages
  library: () => req<CharacterAsset[]>('/api/characters/library'),
  uploadCharacter: async (file: File): Promise<CharacterAsset> => {
    const form = new FormData()
    form.append('file', file)
    const res = await fetch('/api/characters/upload', { method: 'POST', body: form })
    if (!res.ok) throw new Error(`${res.status} — ${(await res.text())}`)
    return res.json()
  },

  // Runs
  launchRun: (channel: string) =>
    req<RunInfo>('/api/runs', { method: 'POST', body: JSON.stringify({ channel }) }),
  listRuns: () => req<RunInfo[]>('/api/runs'),
  getRun: (id: string) => req<RunInfo>(`/api/runs/${encodeURIComponent(id)}`),

  // Galerie
  gallery: () => req<VideoItem[]>('/api/gallery'),
}

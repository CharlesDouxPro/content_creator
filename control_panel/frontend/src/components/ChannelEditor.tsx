import { useEffect, useState } from 'react'
import { useCatalog } from '../catalog'
import { api } from '../api/client'
import type { Channel, Character, CharacterAsset } from '../api/schemas'
import { ROLES } from '../api/schemas'
import { clone, composeChirp3, normalize } from '../lib'
import type { FullChannel } from '../lib'

interface Props {
  value: Channel
  onChange: (c: Channel) => void
}

export function ChannelEditor({ value, onChange }: Props) {
  const { skills, providers, voices, models } = useCatalog()
  const [library, setLibrary] = useState<CharacterAsset[]>([])
  useEffect(() => { api.library().then(setLibrary).catch(() => {}) }, [])

  const v = normalize(value)

  // Applique une mutation sur une copie pleine et remonte le nouveau channel.
  const set = (mut: (c: FullChannel) => void) => { const next = clone(v); mut(next); onChange(next) }

  // Voix Chirp3 pré-composées (autocomplétion), toutes langues.
  const voiceOptions: string[] = []
  for (const lang of voices.languages) {
    for (const name of [...voices.chirp3.male, ...voices.chirp3.female]) {
      voiceOptions.push(composeChirp3(name, lang))
    }
  }

  const characters = Object.entries(v.context.characters)

  function renameCharacter(oldKey: string, newKey: string) {
    set((c) => {
      c.context.characters = Object.fromEntries(
        Object.entries(c.context.characters).map(([k, val]) => [k === oldKey ? newKey : k, val]),
      )
    })
  }
  function updateCharacter(key: string, patch: Partial<Character>) {
    set((c) => { c.context.characters[key] = { ...c.context.characters[key], ...patch } })
  }
  function addCharacter() {
    let key = 'perso'; let i = 1
    while (v.context.characters[key]) key = `perso${++i}`
    set((c) => { c.context.characters[key] = {} })
  }
  function removeCharacter(key: string) {
    set((c) => {
      const { [key]: _drop, ...rest } = c.context.characters
      void _drop
      c.context.characters = rest
    })
  }

  return (
    <div className="form">
      {/* ---- Général ---- */}
      <fieldset>
        <legend>Général</legend>
        <label className="field">
          <span>Nom</span>
          <input value={v.name} onChange={(e) => set((c) => { c.name = e.target.value })} placeholder="mon-channel" />
        </label>
        <label className="field">
          <span>Skill</span>
          <select value={v.skill} onChange={(e) => set((c) => { c.skill = e.target.value })}>
            {skills.map((s) => <option key={s.name} value={s.name}>{s.name}</option>)}
          </select>
          <small className="muted">{skills.find((s) => s.name === v.skill)?.description}</small>
        </label>
      </fieldset>

      {/* ---- Brief ---- */}
      <fieldset>
        <legend>Brief</legend>
        <label className="field">
          <span>Prompt</span>
          <textarea rows={5} value={v.context.prompt}
            onChange={(e) => set((c) => { c.context.prompt = e.target.value })}
            placeholder="Décris la vidéo voulue…" />
        </label>
        <label className="field">
          <span>Mood</span>
          <input value={v.context.mood} onChange={(e) => set((c) => { c.context.mood = e.target.value })}
            placeholder="ex. dramatique, tendu" />
        </label>
      </fieldset>

      {/* ---- Ressources ---- */}
      <fieldset>
        <legend>Ressources</legend>
        <StringList label="URLs (à scraper / médias)" items={v.context.ressources.urls}
          onChange={(items) => set((c) => { c.context.ressources.urls = items })} />
        <StringList label="Fichiers locaux (clips/images)" items={v.context.ressources.local_paths}
          onChange={(items) => set((c) => { c.context.ressources.local_paths = items })} />
        <StringList label="Pistes audio (musique/voix)" items={v.context.ressources.audio_paths}
          onChange={(items) => set((c) => { c.context.ressources.audio_paths = items })} />
        <label className="field">
          <span>Notes</span>
          <textarea rows={2} value={v.context.ressources.notes ?? ''}
            onChange={(e) => set((c) => { c.context.ressources.notes = e.target.value || null })} />
        </label>
      </fieldset>

      {/* ---- Modèles ---- */}
      <fieldset>
        <legend>Model config (par rôle)</legend>
        <div className="models-grid">
          {ROLES.map((role) => (
            <div key={role} className="model-row">
              <span className="role">{role}</span>
              <input list={`dl-${role}`} value={v.models[role].model_name}
                onChange={(e) => set((c) => { c.models[role].model_name = e.target.value })} placeholder="model_name" />
              <datalist id={`dl-${role}`}>{(models.suggestions[role] ?? []).map((m) => <option key={m} value={m} />)}</datalist>
              <select value={v.models[role].provider_id}
                onChange={(e) => set((c) => { c.models[role].provider_id = e.target.value })}>
                {providers.map((p) => <option key={p.id} value={p.id}>{p.id}</option>)}
              </select>
            </div>
          ))}
        </div>
      </fieldset>

      {/* ---- Personnages ---- */}
      <fieldset>
        <legend>Personnages</legend>
        <datalist id="dl-voices">{voiceOptions.map((vo) => <option key={vo} value={vo} />)}</datalist>
        <datalist id="dl-images">{library.map((a) => <option key={a.blob} value={a.url}>{a.name}</option>)}</datalist>
        <div className="characters">
          {characters.map(([key, ch]) => (
            <div key={key} className="character-card">
              <div className="row between">
                <input className="char-name" value={key} onChange={(e) => renameCharacter(key, e.target.value)} />
                <button className="btn danger tiny" onClick={() => removeCharacter(key)}>Retirer</button>
              </div>
              <div className="char-body">
                <div className="char-image">
                  {ch.image
                    ? <img src={ch.image} alt={key} onError={(e) => { (e.target as HTMLImageElement).style.opacity = '0.2' }} />
                    : <div className="noimg">pas d'image</div>}
                </div>
                <div className="char-fields">
                  <label className="field">
                    <span>Image (URL GCS)</span>
                    <input list="dl-images" value={ch.image ?? ''} placeholder="https://storage.googleapis.com/…"
                      onChange={(e) => updateCharacter(key, { image: e.target.value || null })} />
                  </label>
                  <label className="field">
                    <span>Voix</span>
                    <input list="dl-voices" value={ch.voice ?? ''} placeholder="fr-FR-Chirp3-HD-Kore"
                      onChange={(e) => updateCharacter(key, { voice: e.target.value || null })} />
                  </label>
                  <label className="field">
                    <span>Description</span>
                    <input value={ch.description ?? ''} placeholder="apparence / personnalité"
                      onChange={(e) => updateCharacter(key, { description: e.target.value || null })} />
                  </label>
                  <details className="advanced">
                    <summary>Voix expressive (Gemini)</summary>
                    <label className="field">
                      <span>Style</span>
                      <input value={ch.style ?? ''} placeholder="ton bravache"
                        onChange={(e) => updateCharacter(key, { style: e.target.value || null })} />
                    </label>
                    <label className="field">
                      <span>voice_model</span>
                      <input value={ch.voice_model ?? ''} placeholder="gemini-3.1-flash-tts-preview"
                        onChange={(e) => updateCharacter(key, { voice_model: e.target.value || null })} />
                    </label>
                    <label className="field">
                      <span>language</span>
                      <input value={ch.language ?? ''} placeholder="fr-FR"
                        onChange={(e) => updateCharacter(key, { language: e.target.value || null })} />
                    </label>
                  </details>
                </div>
              </div>
            </div>
          ))}
        </div>
        <button className="btn tiny" onClick={addCharacter}>+ Ajouter un personnage</button>
      </fieldset>
    </div>
  )
}

// --- Éditeur de liste de chaînes (URLs, chemins…) ---
function StringList({ label, items, onChange }: { label: string; items: string[]; onChange: (v: string[]) => void }) {
  return (
    <div className="field">
      <span>{label}</span>
      {items.map((it, i) => (
        <div key={i} className="row gap">
          <input value={it} onChange={(e) => onChange(items.map((x, j) => (j === i ? e.target.value : x)))} />
          <button className="btn danger tiny" onClick={() => onChange(items.filter((_, j) => j !== i))}>×</button>
        </div>
      ))}
      <button className="btn tiny" onClick={() => onChange([...items, ''])}>+ Ajouter</button>
    </div>
  )
}

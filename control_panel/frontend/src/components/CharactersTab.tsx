import { useEffect, useRef, useState } from 'react'
import { api } from '../api/client'
import type { CharacterAsset } from '../api/schemas'

export function CharactersTab() {
  const [assets, setAssets] = useState<CharacterAsset[]>([])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const fileRef = useRef<HTMLInputElement>(null)

  const load = () => api.library().then(setAssets).catch((e) => setError(String(e)))
  useEffect(() => { load() }, [])

  async function onUpload(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0]
    if (!file) return
    setBusy(true); setError(null)
    try {
      await api.uploadCharacter(file)
      await load()
    } catch (err) {
      setError(String(err))
    } finally {
      setBusy(false)
      if (fileRef.current) fileRef.current.value = ''
    }
  }

  return (
    <div className="stack">
      <section className="card">
        <h2>Uploader un personnage</h2>
        <p className="muted">L'image part sur ton GCS (bucket public) sous <code>avatars/</code>. L'URL retournée
          est utilisable comme image d'un personnage dans un channel.</p>
        <label className="btn primary">
          {busy ? 'Upload…' : 'Choisir une image'}
          <input ref={fileRef} type="file" accept="image/*" hidden onChange={onUpload} disabled={busy} />
        </label>
        {error && <p className="error">{error}</p>}
      </section>

      <section className="card">
        <h2>Bibliothèque <span className="muted">({assets.length})</span></h2>
        <div className="grid">
          {assets.map((a) => (
            <figure key={a.blob} className="asset">
              <img src={a.url} alt={a.name} loading="lazy" />
              <figcaption>
                <span className="name">{a.name}</span>
                <button className="btn tiny" onClick={() => navigator.clipboard.writeText(a.url)}>Copier l'URL</button>
              </figcaption>
            </figure>
          ))}
          {assets.length === 0 && <p className="muted">Aucun personnage uploadé pour l'instant.</p>}
        </div>
      </section>
    </div>
  )
}

import { useEffect, useState } from 'react'
import { api } from '../api/client'
import type { VideoItem } from '../api/schemas'

export function GalleryTab() {
  const [videos, setVideos] = useState<VideoItem[]>([])
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)

  const load = () => {
    setLoading(true)
    api.gallery().then(setVideos).catch((e) => setError(String(e))).finally(() => setLoading(false))
  }
  useEffect(() => { load() }, [])

  return (
    <div className="stack">
      <section className="card">
        <div className="row between">
          <h2>Vidéos produites <span className="muted">({videos.length})</span></h2>
          <button className="btn" onClick={load} disabled={loading}>{loading ? '…' : 'Rafraîchir'}</button>
        </div>
        {error && <p className="error">{error}</p>}
        <div className="grid videos">
          {videos.map((v) => (
            <figure key={v.blob} className="video">
              <video src={v.url} controls preload="metadata" />
              <figcaption>
                <span className="name">{v.channel}</span>
                <span className="muted">{v.date} · {v.name}</span>
              </figcaption>
            </figure>
          ))}
          {!loading && videos.length === 0 && <p className="muted">Aucune vidéo sur le bucket.</p>}
        </div>
      </section>
    </div>
  )
}

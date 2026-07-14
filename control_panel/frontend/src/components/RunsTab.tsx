import { useEffect, useRef, useState } from 'react'
import { api } from '../api/client'
import type { Channel, RunInfo } from '../api/schemas'

const STATUS_LABEL: Record<string, string> = {
  queued: '⏳ en file', running: '⚙️ en cours', done: '✅ terminé', error: '❌ erreur',
}

export function RunsTab() {
  const [channels, setChannels] = useState<Channel[]>([])
  const [runs, setRuns] = useState<RunInfo[]>([])
  const [selected, setSelected] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const logRef = useRef<HTMLPreElement>(null)

  useEffect(() => { api.listChannels().then(setChannels).catch((e) => setError(String(e))) }, [])

  // Polling tant qu'un run est actif.
  useEffect(() => {
    const active = runs.some((r) => r.status === 'running' || r.status === 'queued')
    if (!active && runs.length > 0) return
    const t = setInterval(() => { api.listRuns().then(setRuns).catch(() => {}) }, 2000)
    api.listRuns().then(setRuns).catch(() => {})
    return () => clearInterval(t)
  }, [runs])

  useEffect(() => {
    if (logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight
  }, [runs, selected])

  async function launch(name: string) {
    setError(null)
    try {
      const info = await api.launchRun(name)
      setSelected(info.id)
      setRuns((prev) => [...prev, info])
    } catch (e) {
      setError(String(e))
    }
  }

  const current = runs.find((r) => r.id === selected) ?? null

  return (
    <div className="stack">
      <section className="card">
        <h2>Lancer un channel</h2>
        <p className="muted">Rend la vidéo via la pipeline (plusieurs minutes). Les logs s'affichent en direct.</p>
        <div className="chips">
          {channels.map((c) => (
            <button key={c.name} className="btn primary" onClick={() => launch(c.name)}>▶ {c.name}</button>
          ))}
        </div>
        {error && <p className="error">{error}</p>}
      </section>

      <section className="card">
        <h2>Runs</h2>
        <div className="runlist">
          {runs.slice().reverse().map((r) => (
            <button
              key={r.id}
              className={r.id === selected ? 'runitem active' : 'runitem'}
              onClick={() => setSelected(r.id)}
            >
              <span className="name">{r.channel}</span>
              <span className="muted">{STATUS_LABEL[r.status] ?? r.status}</span>
              <span className="muted small">{r.started_at}</span>
            </button>
          ))}
          {runs.length === 0 && <p className="muted">Aucun run lancé.</p>}
        </div>
      </section>

      {current && (
        <section className="card">
          <div className="row between">
            <h2>{current.channel} <span className="muted">{STATUS_LABEL[current.status] ?? current.status}</span></h2>
            {current.gcs_url && <a className="btn" href={current.gcs_url} target="_blank">Ouvrir la vidéo</a>}
          </div>
          {current.title && <p className="muted">Titre : {current.title}</p>}
          {current.error && <p className="error">{current.error}</p>}
          <pre ref={logRef} className="logs">{current.logs || '(pas encore de logs)'}</pre>
        </section>
      )}
    </div>
  )
}

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

  // Ref qui suit toujours le dernier état des runs, pour que l'effet de polling
  // ne dépende pas de `runs` (sinon chaque setRuns relance l'effet → boucle de fetch).
  const runsRef = useRef<RunInfo[]>([])
  useEffect(() => { runsRef.current = runs }, [runs])

  // Polling tant qu'un run est actif. L'effet ne tourne qu'une fois.
  useEffect(() => {
    const poll = () => { api.listRuns().then(setRuns).catch(() => {}) }
    poll()
    const t = setInterval(() => {
      const prev = runsRef.current
      const active = prev.some((r) => r.status === 'running' || r.status === 'queued')
      if (active || prev.length === 0) poll()
    }, 2000)
    return () => clearInterval(t)
  }, [])

  useEffect(() => {
    if (logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight
  }, [runs, selected])

  // Channel dont on prépare le lancement (formulaire de paramètres) + valeurs saisies.
  const [launchFor, setLaunchFor] = useState<Channel | null>(null)
  const [paramValues, setParamValues] = useState<Record<string, string>>({})

  // Clic sur un channel : s'il a des paramètres, on ouvre le formulaire (pré-rempli avec les
  // défauts) ; sinon on lance directement.
  function onChannelClick(c: Channel) {
    const params = c.context?.parameters ?? []
    if (params.length === 0) { launch(c.name); return }
    setError(null)
    setLaunchFor(c)
    setParamValues(Object.fromEntries(params.map((p) => [p.name, p.value])))
  }

  async function launch(name: string, parameters: Record<string, string> = {}) {
    setError(null)
    try {
      const info = await api.launchRun(name, parameters)
      setSelected(info.id)
      setRuns((prev) => [...prev, info])
      setLaunchFor(null)
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
            <button key={c.name} className="btn primary" onClick={() => onChannelClick(c)}>▶ {c.name}</button>
          ))}
        </div>
        {error && <p className="error">{error}</p>}

        {launchFor && (
          <div className="card" style={{ marginTop: 12 }}>
            <div className="row between">
              <h3 style={{ margin: 0 }}>Paramètres du run — {launchFor.name}</h3>
              <button className="btn ghost tiny" onClick={() => setLaunchFor(null)}>Annuler</button>
            </div>
            <p className="muted">Pré-remplis avec les valeurs par défaut du channel. Surcharge pour ce run uniquement.</p>
            {(launchFor.context?.parameters ?? []).map((p) => (
              <label className="field" key={p.name}>
                <span>{p.name} <small className="muted">({p.type})</small></span>
                {p.type === 'boolean' ? (
                  <select value={paramValues[p.name] ?? 'false'}
                    onChange={(e) => setParamValues((s) => ({ ...s, [p.name]: e.target.value }))}>
                    <option value="true">true</option>
                    <option value="false">false</option>
                  </select>
                ) : p.type === 'text' ? (
                  <textarea rows={2} value={paramValues[p.name] ?? ''}
                    onChange={(e) => setParamValues((s) => ({ ...s, [p.name]: e.target.value }))} />
                ) : (
                  <input value={paramValues[p.name] ?? ''} type={p.type === 'number' ? 'number' : 'text'}
                    onChange={(e) => setParamValues((s) => ({ ...s, [p.name]: e.target.value }))} />
                )}
                {p.description && <small className="muted">{p.description}</small>}
              </label>
            ))}
            <button className="btn primary" style={{ marginTop: 8 }}
              onClick={() => launch(launchFor.name, paramValues)}>▶ Lancer avec ces paramètres</button>
          </div>
        )}
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

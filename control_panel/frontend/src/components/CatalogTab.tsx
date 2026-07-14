import { useCatalog } from '../catalog'

export function CatalogTab() {
  const { skills, providers, voices, models } = useCatalog()
  return (
    <div className="stack">
      <section className="card">
        <h2>Skills</h2>
        <ul className="reflist">
          {skills.map((s) => (
            <li key={s.name}><code>{s.name}</code><span>{s.description}</span></li>
          ))}
        </ul>
      </section>

      <section className="card">
        <h2>Providers</h2>
        <ul className="reflist">
          {providers.map((p) => (
            <li key={p.id}>
              <code>{p.id}</code>
              <span>{p.base_url} — token {p.token_set ? '✅ présent' : '⚠️ absent (.env)'}</span>
            </li>
          ))}
        </ul>
      </section>

      <section className="card">
        <h2>Modèles par défaut</h2>
        <ul className="reflist">
          {models.roles.map((r) => (
            <li key={r}>
              <code>{r}</code>
              <span>{models.defaults[r].model_name} <em>({models.defaults[r].provider_id})</em></span>
            </li>
          ))}
        </ul>
      </section>

      <section className="card">
        <h2>Voix</h2>
        <p className="muted">Chirp3-HD (template <code>{voices.chirp3_template}</code>) :</p>
        <div className="chips">
          {[...voices.chirp3.male, ...voices.chirp3.female].map((v) => <span key={v} className="chip">{v}</span>)}
        </div>
        <p className="muted">Langues : {voices.languages.join(', ')}</p>
        <p className="muted">Gemini (expressif) : <code>{voices.gemini_voice_model}</code> — {voices.gemini_note}</p>
      </section>
    </div>
  )
}

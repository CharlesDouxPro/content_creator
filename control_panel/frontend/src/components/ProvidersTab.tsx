import { useEffect, useState } from 'react'
import { api } from '../api/client'
import type { ProviderConfig } from '../api/schemas'

// Providers d'inférence NOMMÉS : on les définit ici (nom + base_url + api_key), une
// seule fois, stockés durablement (GCS). Les channels les référencent par nom.
// La clé n'est jamais réaffichée (laisser vide pour conserver la clé existante).
export function ProvidersTab() {
  const [providers, setProviders] = useState<ProviderConfig[]>([])
  const [drafts, setDrafts] = useState<Record<string, { base_url: string; api_key: string }>>({})
  const [neo, setNeo] = useState({ name: '', base_url: '', api_key: '' })
  const [status, setStatus] = useState('')

  async function refresh() {
    const list = await api.providersConfig()
    setProviders(list)
    setDrafts(Object.fromEntries(list.map((p) => [p.name, { base_url: p.base_url, api_key: '' }])))
  }

  useEffect(() => { refresh().catch((e) => setStatus(String(e))) }, [])

  async function save(name: string, body: { base_url: string; api_key: string }) {
    setStatus('')
    try {
      await api.saveProvider(name, body)
      await refresh()
      setStatus(`✅ ${name} enregistré`)
    } catch (e) {
      setStatus(String(e))
    }
  }

  async function addNew() {
    const name = neo.name.trim()
    if (!name) { setStatus('⚠️ donne un nom au provider'); return }
    await save(name, { base_url: neo.base_url, api_key: neo.api_key })
    setNeo({ name: '', base_url: '', api_key: '' })
  }

  async function remove(name: string) {
    setStatus('')
    try {
      await api.deleteProvider(name)
      await refresh()
      setStatus(`🗑️ ${name} supprimé`)
    } catch (e) {
      setStatus(String(e))
    }
  }

  return (
    <div className="stack">
      <p className="muted">
        Définis tes providers (nom + base_url + api_key), une seule fois. Stockés durablement
        et partagés par tous les channels (référencés par nom). La clé n'est jamais réaffichée —
        laisser vide pour conserver la clé existante.
      </p>

      {providers.map((p) => (
        <section className="card" key={p.name}>
          <h2><code>{p.name}</code> {p.api_key_set ? '· 🔑 clé définie' : '· ⚠️ clé absente'}</h2>
          <label className="field">
            <span>base_url</span>
            <input
              value={drafts[p.name]?.base_url ?? ''}
              placeholder="https://api.deepinfra.com/v1/openai"
              onChange={(e) => setDrafts((d) => ({ ...d, [p.name]: { ...d[p.name], base_url: e.target.value } }))}
            />
          </label>
          <label className="field">
            <span>api_key</span>
            <input
              type="password"
              value={drafts[p.name]?.api_key ?? ''}
              placeholder={p.api_key_set ? '•••••••• (inchangée)' : 'clé API'}
              onChange={(e) => setDrafts((d) => ({ ...d, [p.name]: { ...d[p.name], api_key: e.target.value } }))}
            />
          </label>
          <div className="row">
            <button className="btn" onClick={() => save(p.name, drafts[p.name])}>Enregistrer</button>
            <button className="btn ghost" onClick={() => remove(p.name)}>Supprimer</button>
          </div>
        </section>
      ))}

      <section className="card">
        <h2>➕ Nouveau provider</h2>
        <label className="field">
          <span>nom</span>
          <input value={neo.name} placeholder="ex. sglang_gpu, openai, deepinfra"
            onChange={(e) => setNeo((n) => ({ ...n, name: e.target.value }))} />
        </label>
        <label className="field">
          <span>base_url</span>
          <input value={neo.base_url} placeholder="https://…/v1"
            onChange={(e) => setNeo((n) => ({ ...n, base_url: e.target.value }))} />
        </label>
        <label className="field">
          <span>api_key</span>
          <input type="password" value={neo.api_key} placeholder="clé API"
            onChange={(e) => setNeo((n) => ({ ...n, api_key: e.target.value }))} />
        </label>
        <button className="btn" onClick={addNew}>Créer</button>
      </section>

      {status && <p className="muted">{status}</p>}
    </div>
  )
}

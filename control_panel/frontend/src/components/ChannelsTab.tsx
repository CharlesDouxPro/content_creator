import { useEffect, useState } from 'react'
import { api } from '../api/client'
import { useCatalog } from '../catalog'
import type { Channel } from '../api/schemas'
import { blankChannel, clone } from '../lib'
import { ChannelEditor } from './ChannelEditor'

export function ChannelsTab() {
  const catalog = useCatalog()
  const [channels, setChannels] = useState<Channel[]>([])
  const [draft, setDraft] = useState<Channel | null>(null)
  const [originalName, setOriginalName] = useState<string | null>(null) // null => création
  const [msg, setMsg] = useState<{ kind: 'ok' | 'err'; text: string } | null>(null)
  const [busy, setBusy] = useState(false)

  const reload = () => api.listChannels().then(setChannels).catch((e) => flash('err', String(e)))
  useEffect(() => { reload() }, [])

  function flash(kind: 'ok' | 'err', text: string) {
    setMsg({ kind, text })
    if (kind === 'ok') setTimeout(() => setMsg(null), 2500)
  }

  function edit(c: Channel) {
    setDraft(clone(c))
    setOriginalName(c.name)
    setMsg(null)
  }

  function create() {
    setDraft(blankChannel(catalog.skills[0]?.name ?? '', catalog.models))
    setOriginalName(null)
    setMsg(null)
  }

  async function save() {
    if (!draft) return
    setBusy(true); setMsg(null)
    try {
      if (originalName === null) {
        await api.createChannel(draft)
      } else {
        await api.updateChannel(originalName, draft)
      }
      await reload()
      setOriginalName(draft.name)
      flash('ok', 'Channel enregistré.')
    } catch (e) {
      flash('err', String(e))
    } finally {
      setBusy(false)
    }
  }

  async function remove() {
    if (originalName === null) { setDraft(null); return }
    if (!confirm(`Supprimer le channel "${originalName}" ?`)) return
    setBusy(true)
    try {
      await api.deleteChannel(originalName)
      await reload()
      setDraft(null); setOriginalName(null)
      flash('ok', 'Channel supprimé.')
    } catch (e) {
      flash('err', String(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="cols">
      <aside className="sidebar">
        <div className="row between">
          <h2>Channels</h2>
          <button className="btn primary tiny" onClick={create}>+ Nouveau</button>
        </div>
        <ul className="channel-list">
          {channels.map((c) => (
            <li key={c.name}>
              <button className={originalName === c.name ? 'row-item active' : 'row-item'} onClick={() => edit(c)}>
                <span className="name">{c.name}</span>
                <span className="muted small">{c.skill}</span>
              </button>
            </li>
          ))}
        </ul>
      </aside>

      <div className="editor-pane">
        {msg && <div className={msg.kind === 'ok' ? 'banner ok' : 'banner err'}>{msg.text}</div>}
        {draft ? (
          <>
            <div className="row between sticky-actions">
              <h2>{originalName === null ? 'Nouveau channel' : `Éditer · ${originalName}`}</h2>
              <div className="row gap">
                <button className="btn danger" onClick={remove} disabled={busy}>
                  {originalName === null ? 'Annuler' : 'Supprimer'}
                </button>
                <button className="btn primary" onClick={save} disabled={busy}>
                  {busy ? 'Enregistrement…' : 'Enregistrer'}
                </button>
              </div>
            </div>
            <ChannelEditor value={draft} onChange={setDraft} />
          </>
        ) : (
          <div className="empty">Sélectionne un channel ou crée-en un nouveau.</div>
        )}
      </div>
    </div>
  )
}

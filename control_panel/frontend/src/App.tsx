import { useState } from 'react'
import './App.css'
import { CatalogProvider } from './catalog'
import { ChannelsTab } from './components/ChannelsTab'
import { CharactersTab } from './components/CharactersTab'
import { CatalogTab } from './components/CatalogTab'
import { RunsTab } from './components/RunsTab'
import { GalleryTab } from './components/GalleryTab'

const TABS = [
  { id: 'channels', label: 'Channels', el: <ChannelsTab /> },
  { id: 'characters', label: 'Personnages', el: <CharactersTab /> },
  { id: 'runs', label: 'Runs', el: <RunsTab /> },
  { id: 'gallery', label: 'Galerie', el: <GalleryTab /> },
  { id: 'catalog', label: 'Catalogue', el: <CatalogTab /> },
] as const

export default function App() {
  const [tab, setTab] = useState<string>('channels')
  return (
    <CatalogProvider>
      <header className="topbar">
        <h1>content_creator <span className="muted">· panneau de contrôle</span></h1>
        <nav className="tabs">
          {TABS.map((t) => (
            <button key={t.id} className={tab === t.id ? 'tab active' : 'tab'} onClick={() => setTab(t.id)}>
              {t.label}
            </button>
          ))}
        </nav>
      </header>
      <main className="content">
        {TABS.map((t) => (
          <div key={t.id} hidden={tab !== t.id}>{t.el}</div>
        ))}
      </main>
    </CatalogProvider>
  )
}

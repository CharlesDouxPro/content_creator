// Contexte "Catalogue" : skills / voix / providers / models chargés une fois, partagés.
import { createContext, useContext, useEffect, useState } from 'react'
import type { ReactNode } from 'react'
import { api } from './api/client'
import type { SkillInfo, ProviderInfo, VoicesInfo, ModelsInfo } from './api/schemas'

export interface Catalog {
  skills: SkillInfo[]
  providers: ProviderInfo[]
  voices: VoicesInfo
  models: ModelsInfo
}

const Ctx = createContext<Catalog | null>(null)

export function CatalogProvider({ children }: { children: ReactNode }) {
  const [catalog, setCatalog] = useState<Catalog | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    Promise.all([api.skills(), api.providers(), api.voices(), api.models()])
      .then(([skills, providers, voices, models]) => setCatalog({ skills, providers, voices, models }))
      .catch((e) => setError(String(e)))
  }, [])

  if (error) return <div className="fatal">Backend injoignable : {error}<br />Démarre l'API (port 8080).</div>
  if (!catalog) return <div className="loading">Chargement du catalogue…</div>
  return <Ctx.Provider value={catalog}>{children}</Ctx.Provider>
}

export function useCatalog(): Catalog {
  const c = useContext(Ctx)
  if (!c) throw new Error('useCatalog hors CatalogProvider')
  return c
}

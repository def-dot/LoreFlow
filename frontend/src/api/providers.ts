import { api } from './request'

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface ProviderItem {
  id: number
  name: string
  base_url: string
  api_key: string
  created_at: string | null
  updated_at: string | null
}

export interface ProviderIn {
  name: string
  base_url: string
  api_key?: string
}

export interface ModelItem {
  id: number
  provider_id: number
  provider?: ProviderItem
  name: string
  model_key: string
  model_type: 'chat' | 'embedding' | 'rerank'
  is_enabled: boolean
  created_at: string | null
  updated_at: string | null
}

export interface ModelIn {
  provider_id: number
  name: string
  model_type: 'chat' | 'embedding' | 'rerank'
  is_enabled?: boolean
}

export interface ModelSettings {
  default_chat_model: string | null      // "openai/gpt-4o"
  default_embedding_model: string | null
  default_rerank_model: string | null
}

// ---------------------------------------------------------------------------
// Provider CRUD
// ---------------------------------------------------------------------------

export const listProviders = () => api.get<ProviderItem[]>('/providers')

export const getProvider = (id: number) => api.get<ProviderItem>(`/providers/${id}`)

export const createProvider = (data: ProviderIn) =>
  api.post<ProviderItem>('/providers', data)

export const updateProvider = (id: number, data: ProviderIn) =>
  api.put<ProviderItem>(`/providers/${id}`, data)

export const deleteProvider = (id: number) => api.delete(`/providers/${id}`)

export const syncModels = (providerId: number) =>
  api.post<{ synced: boolean }>(`/providers/${providerId}/sync-models`)

// ---------------------------------------------------------------------------
// Model CRUD
// ---------------------------------------------------------------------------

export const listModels = (params?: { provider_id?: number; model_type?: string }) =>
  api.get<ModelItem[]>('/models', { params })

export const createModel = (data: ModelIn) =>
  api.post<ModelItem>('/models', data)

export const updateModel = (id: number, data: ModelIn) =>
  api.put<ModelItem>(`/models/${id}`, data)

export const toggleModel = (id: number) =>
  api.patch<ModelItem>(`/models/${id}/toggle`)

export const updateModelType = (id: number, modelType: string) =>
  api.patch<ModelItem>(`/models/${id}/type`, { model_type: modelType })

export const deleteModel = (id: number) => api.delete(`/models/${id}`)

// ---------------------------------------------------------------------------
// Settings（用途分配）
// ---------------------------------------------------------------------------

export const getModelSettings = () => api.get<ModelSettings>('/settings/models')

export const updateModelSettings = (data: ModelSettings) =>
  api.put<ModelSettings>('/settings/models', data)
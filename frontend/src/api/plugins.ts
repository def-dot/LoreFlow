import { api } from './request'

export interface PluginInfo {
  filename: string
  module: string
  node_names: string[]
  loaded_at: string
  error: string | null
}

export function listPlugins(): Promise<{ plugins: PluginInfo[] }> {
  return api.get('/plugins')
}

export function uploadPlugin(file: File): Promise<PluginInfo> {
  const form = new FormData()
  form.append('file', file)
  return api.post('/plugins', form)
}

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

export function reloadPlugins(): Promise<{ plugins: PluginInfo[] }> {
  return api.post('/plugins/reload')
}

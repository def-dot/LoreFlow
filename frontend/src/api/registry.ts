import { api } from './request'

export interface RegistryItem {
  name: string
  description: string
  type: string  // "tool" | "workflow"
}

export function listSkills(): Promise<{ items: RegistryItem[] }> {
  return api.get('/skills')
}

export function listTools(): Promise<{ items: RegistryItem[] }> {
  return api.get('/tools')
}

export function listModels(): Promise<Record<string, string[]>> {
  return api.get('/models')
}

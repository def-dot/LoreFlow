import { api } from './request'

export interface RegistryItem {
  name: string
  description: string
}

export function listSkills(): Promise<{ items: RegistryItem[] }> {
  return api.get('/skills')
}

export function listTools(): Promise<{ items: RegistryItem[] }> {
  return api.get('/tools')
}

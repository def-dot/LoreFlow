import { api } from './request'

export interface RegistryItem {
  name: string
  description: string
}

export interface ToolOut {
  name: string
  label: string
  description: string
  group: string
}

export function listSkills(): Promise<RegistryItem[]> {
  return api.get('/skills')
}

export function listTools(): Promise<ToolOut[]> {
  return api.get('/tools')
}

export function listModels(): Promise<Record<string, string[]>> {
  return api.get('/models')
}

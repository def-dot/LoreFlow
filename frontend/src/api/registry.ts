import { api } from './request'
import type { FuncInfo } from './nodeTypes'

export type { FuncInfo as ToolOut }

export interface SkillOut {
  name: string
  description: string
  body: string
  location: string
  base_dir: string
  allowed_tools: string
  license: string
  compatibility: string
}

export function listSkills(): Promise<SkillOut[]> {
  return api.get('/skills')
}

export function rescanSkills(): Promise<{ count: number }> {
  return api.post('/skills/rescan')
}

export function listTools(): Promise<FuncInfo[]> {
  return api.get('/tools')
}

export function listModels(): Promise<Record<string, string[]>> {
  return api.get('/models')
}
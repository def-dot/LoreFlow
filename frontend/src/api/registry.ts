import { api } from './request'
import type { FuncInfo } from './nodeTypes'

export type { FuncInfo as ToolOut }

export interface SkillOut {
  name: string
  description: string
  content: string
  base_dir: string
  files: string[]
}

export function listSkills(): Promise<SkillOut[]> {
  return api.get('/skills')
}

export function rescanSkills(): Promise<{ count: number }> {
  return api.post('/skills/rescan')
}

export interface SkillCreateIn {
  name: string
  description?: string
  body?: string
  allowed_tools?: string
}

export function createSkill(data: SkillCreateIn): Promise<SkillOut> {
  return api.post('/skills', data)
}

export function updateSkill(name: string, data: SkillCreateIn): Promise<SkillOut> {
  return api.put(`/skills/${name}`, data)
}

export function deleteSkill(name: string): Promise<void> {
  return api.delete(`/skills/${name}`)
}

export function uploadSkillZip(file: File): Promise<{ count: number }> {
  const form = new FormData()
  form.append('file', file)
  return api.post('/skills/upload', form)
}

export function readSkillFile(name: string, path: string): Promise<string> {
  return api.get(`/skills/${name}/file`, { params: { path } })
}

export function listTools(): Promise<FuncInfo[]> {
  return api.get('/tools')
}

export function listModels(): Promise<Record<string, string[]>> {
  return api.get('/models')
}
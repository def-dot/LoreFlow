import { api } from './request'
import type { FuncInfo } from './nodeTypes'

export type { FuncInfo as ToolOut }

export interface SkillDef {
  name: string
  description: string
  content: string
  files: string[]
}

export function listSkills(): Promise<SkillDef[]> {
  return api.get('/skills')
}

export interface SkillCreateIn {
  content: string
}

export function createSkill(data: SkillCreateIn): Promise<SkillDef> {
  return api.post('/skills', data)
}

export function updateSkill(name: string, data: SkillCreateIn): Promise<SkillDef> {
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

export async function downloadSkill(name: string): Promise<Blob> {
  const resp = await api.get(`/skills/${name}/download`, { responseType: 'blob' }) as unknown as { data: Blob }
  return resp.data
}

export function listTools(): Promise<FuncInfo[]> {
  return api.get('/tools')
}

export function listModels(): Promise<Record<string, string[]>> {
  return api.get('/models')
}
import { api } from './request'
import type { JsonSchema, SourceInfo } from './nodeTypes'

export interface RegistryItem {
  name: string
  description: string
}

export interface ToolOut {
  name: string
  label: string
  description: string
  group: string
  input_schema?: JsonSchema | null
  output_schema?: JsonSchema | null
  /** 消费方：node = 工作流节点，tool = Agent 工具 */
  roles?: string[]
  source?: SourceInfo
}

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

export function listTools(): Promise<ToolOut[]> {
  return api.get('/tools')
}

export function listModels(): Promise<Record<string, string[]>> {
  return api.get('/models')
}

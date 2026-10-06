import { api } from './request'
import type { JsonSchema } from './nodeTypes'

export interface PipelineNodeInfo {
  name: string
  label: string | null
  type: string | null
  type_label: string | null
  description: string | null
  type_description: string | null
  input_schema: JsonSchema | null
  output_schema: JsonSchema | null
  depends_on: string[]
  inputs: Record<string, unknown> | null
  retry: string | null
  condition: string | null
}

/** UI 渲染控件类型（从 JSON Schema ui:widget 派生） */
export type WidgetType = 'text' | 'textarea' | 'number' | 'select' | 'checkbox' | 'file' | 'file_list'

/** 一个运行时输入参数的声明（JSON Schema property + ui 扩展） */
export interface ParamSpec {
  name: string            // ctx 里的键（从 dict key 派生）
  type?: string           // JSON Schema type: string/number/array/boolean
  title?: string          // 展示名（JSON Schema title）
  description?: string
  default?: unknown
  required?: boolean      // 从顶层 required 数组派生
  enum?: string[]         // JSON Schema enum（select 选项）
  items?: { type: string; enum?: string[] }  // array items（checkbox 选项）
  ui?: string             // UI 控件：textarea/select/checkbox/file/file_list
}

export interface PipelineListItem {
  id: number
  name: string
  description: string
}

export interface PipelineDetail {
  name: string
  description: string
  params: Record<string, unknown> | null
  required?: string[] | null
  mermaid: string
  source: string
  nodes: PipelineNodeInfo[]
}

/** 将 JSON Schema properties 转为 ParamSpec 数组，标记 required */
export function toParamSpecs(
  params: Record<string, unknown>,
  required?: string[] | null,
): ParamSpec[] {
  const reqSet = new Set(required ?? [])
  return Object.entries(params).map(([name, spec]: [string, any]) => ({
    name,
    required: reqSet.has(name),
    ...spec,
  }))
}

/** 解析参数的 UI 控件类型：优先 ui，退化到 JSON Schema type */
export function resolveParamType(spec: ParamSpec): WidgetType {
  if (spec.ui) return spec.ui as WidgetType
  if (spec.type === 'number') return 'number'
  if (spec.type === 'array') return 'checkbox'
  return 'text'
}

/** 获取 select/checkbox 的选项列表 */
export function resolveOptions(spec: ParamSpec): string[] {
  if (spec.enum) return spec.enum
  if (spec.items?.enum) return spec.items.enum
  return []
}

export function listPipelines(q?: string): Promise<PipelineListItem[]> {
  const params: Record<string, string> = {}
  if (q) params.q = q
  return api.get('/pipelines', { params })
}

export function getPipeline(id: number): Promise<PipelineDetail> {
  return api.get(`/pipelines/${id}`)
}

/** 创建用户自定义 pipeline（name 从 YAML 自动生成） */
export function createPipeline(data: {
  definition: string
}): Promise<PipelineListItem> {
  return api.post('/pipelines', data)
}

/** 更新用户自定义 pipeline */
export function updatePipeline(
  id: number,
  data: { definition: string },
): Promise<PipelineListItem> {
  return api.put(`/pipelines/${id}`, data)
}

/** 删除用户自定义 pipeline */
export function deletePipeline(id: number): Promise<{ deleted: number }> {
  return api.delete(`/pipelines/${id}`)
}

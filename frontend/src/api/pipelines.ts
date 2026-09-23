import { api } from './request'
import type { JsonSchema } from './nodeTypes'

export interface PipelineNodeInfo {
  name: string
  label: string | null
  type: string | null
  type_label: string | null
  description: string | null
  type_description: string | null
  type_input_schema: JsonSchema | null
  type_output_schema: JsonSchema | null
  depends_on: string[]
  inputs: Record<string, unknown> | null
  retry: string | null
  condition: string | null
}

/** 参数类型枚举（与后端 ParamType 对齐） */
export type ParamType = 'text' | 'paragraph' | 'number' | 'select' | 'checkbox' | 'file' | 'file_list'

/** 一个运行时输入参数的声明（后端直接返回 YAML 原始结构） */
export interface ParamSpec {
  name: string            // ctx 里的键（从 dict key 派生）
  label?: string          // 展示名（未声明时退化为键名）
  description?: string
  default?: unknown
  required?: boolean
  type?: ParamType        // 参数类型（未声明时退化为 text）
  options?: string[]      // type=select 时的选项列表
  multiline?: boolean     // 多行文本（渲染 textarea，如文章正文）— 旧版兼容
  file?: boolean          // 文件上传（渲染上传控件）— 旧版兼容
}

export interface PipelineListItem {
  name: string
  description: string
}

export interface PipelineDetail extends PipelineListItem {
  params: Record<string, unknown> | null
  mermaid: string
  source: string
  nodes: PipelineNodeInfo[]
}

/** 将 YAML inputs 原始结构转为 ParamSpec 数组（name 从 key 派生） */
export function toParamSpecs(params: Record<string, Omit<ParamSpec, 'name'>>): ParamSpec[] {
  return Object.entries(params).map(([name, spec]) => ({ name, ...spec }))
}

/** 解析参数的实际类型：优先 type 字段，兼容旧版 multiline/file 布尔标记 */
export function resolveParamType(spec: ParamSpec): ParamType {
  if (spec.type) return spec.type
  if (spec.file) return 'file'
  if (spec.multiline) return 'paragraph'
  return 'text'
}

export function listPipelines(): Promise<PipelineListItem[]> {
  return api.get('/pipelines')
}

export function getPipeline(name: string): Promise<PipelineDetail> {
  return api.get(`/pipelines/${encodeURIComponent(name)}`)
}

/** 创建用户自定义 pipeline（name 从 YAML 自动生成） */
export function createPipeline(data: {
  definition: string
}): Promise<PipelineListItem> {
  return api.post('/pipelines', data)
}

/** 更新用户自定义 pipeline（name 变了会自动重命名，返回最终 name） */
export function updatePipeline(
  name: string,
  data: { definition: string },
): Promise<PipelineListItem> {
  return api.put(`/pipelines/${encodeURIComponent(name)}`, data)
}

/** 删除用户自定义 pipeline */
export function deletePipeline(name: string): Promise<{ deleted: string }> {
  return api.delete(`/pipelines/${encodeURIComponent(name)}`)
}

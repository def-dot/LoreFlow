import { api } from './request'

export interface PipelineNodeInfo {
  name: string
  label: string | null
  type: string | null
  type_label: string | null
  description: string | null
  type_description: string | null
  depends_on: string[]
  inputs: Record<string, unknown> | null
  retry: string | null
  condition: string | null
}

/** 一个运行时输入参数的声明（后端直接返回 YAML 原始结构） */
export interface ParamSpec {
  name: string            // ctx 里的键（从 dict key 派生）
  label?: string          // 展示名（未声明时退化为键名）
  description?: string
  default?: unknown
  required?: boolean
  multiline?: boolean     // 多行文本（渲染 textarea，如文章正文）
  file?: boolean          // 文件上传（渲染上传控件，提交上传接口返回的 {id, filename} 引用）
}

export interface PipelineListItem {
  filename: string
  name: string
  description: string
  node_count: number
  params: Record<string, Omit<ParamSpec, 'name'>>  // YAML inputs 原始结构
}

export interface PipelineDetail extends PipelineListItem {
  mermaid: string
  source: string
  nodes: PipelineNodeInfo[]
}

/** 将 YAML inputs 原始结构转为 ParamSpec 数组（name 从 key 派生） */
export function toParamSpecs(params: Record<string, Omit<ParamSpec, 'name'>>): ParamSpec[] {
  return Object.entries(params).map(([name, spec]) => ({ name, ...spec }))
}

export function listPipelines(): Promise<{ pipelines: PipelineListItem[] }> {
  return api.get('/pipelines')
}

export function getPipeline(filename: string): Promise<PipelineDetail> {
  return api.get(`/pipelines/${encodeURIComponent(filename)}`)
}

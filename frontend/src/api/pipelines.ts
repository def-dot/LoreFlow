import { api } from './request'

export interface PipelineNodeInfo {
  name: string
  kind: 'node' | 'human' | 'loop'
  type: string | null
  type_label: string | null
  type_description: string | null
  depends_on: string[]
  retry: string | null
  condition: string | null
  condition_label: string | null
}

/** 一个运行时输入参数的声明行（后端把 params 富声明/inputs 简式归一化后输出） */
export interface ParamSpec {
  name: string            // ctx 里的键
  label: string           // 展示名（简式声明退化为键名）
  description: string | null
  default: unknown
  has_default: boolean    // 区分「声明了 default: null」与「未声明默认值」
  required: boolean
}

export interface PipelineListItem {
  filename: string
  name: string
  description: string
  node_count: number
  inputs: Record<string, unknown>  // YAML 声明的默认输入
  required_inputs: string[]  // 必须由运行时提供的输入键
  params: ParamSpec[]  // 归一化参数行（驱动参数表单）
}

export interface PipelineDetail extends PipelineListItem {
  mermaid: string
  source: string
  nodes: PipelineNodeInfo[]
}

export function listPipelines(): Promise<{ pipelines: PipelineListItem[] }> {
  return api.get('/pipelines')
}

export function getPipeline(filename: string): Promise<PipelineDetail> {
  return api.get(`/pipelines/${encodeURIComponent(filename)}`)
}

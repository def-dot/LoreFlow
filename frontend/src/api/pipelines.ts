import { api } from './request'

export interface PipelineNodeInfo {
  name: string
  type: string | null
  type_label: string | null
  type_description: string | null
  depends_on: string[]
  retry: string | null
  condition: string | null
  condition_label: string | null
  review: Record<string, string> | null  // (human) 审核视图声明原文 {key: 标签文本}；null=全量上下文
}

/** 一个运行时输入参数的声明行（后端从 YAML params 声明归一化输出） */
export interface ParamSpec {
  name: string            // ctx 里的键
  label: string           // 展示名（未声明 label 时退化为键名）
  description: string | null
  default: unknown
  has_default: boolean    // 区分「声明了 default: null」与「未声明默认值」
  required: boolean
  multiline: boolean      // 多行文本（渲染 textarea，如文章正文）
  file: boolean           // 文件上传（渲染上传控件，提交上传接口返回的 {id, filename} 引用）
}

export interface PipelineListItem {
  filename: string
  name: string
  description: string
  node_count: number
  params: ParamSpec[]  // 参数声明行（必填/默认值由行内字段判断，驱动参数表单）
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

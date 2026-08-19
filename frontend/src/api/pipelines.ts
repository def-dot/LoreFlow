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
  prompt: string | null
  body_summary: string | null
}

export interface PipelineListItem {
  filename: string
  name: string
  description: string
  node_count: number
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

import { api } from './request'

export interface RunListItem {
  id: number
  name: string
  created_at: string | null
  finished_at: string | null
  status: string
  error: string | null
}

export interface NodeSnapshot {
  status: string
  output: unknown
  error: string | null
  attempts: number
  duration_ms: number
}

export interface RunDetail extends RunListItem {
  config_file: string
  mermaid: string
  nodes: Record<string, NodeSnapshot>
  reviewing: string[]
}

export interface RunListPage {
  items: RunListItem[]
  total: number
  offset: number
  limit: number
}

export function listRuns(offset = 0, limit = 50): Promise<RunListPage> {
  return api.get('/runs', { params: { offset, limit } })
}

export function getRun(runId: number): Promise<RunDetail> {
  return api.get(`/runs/${runId}`)
}

export function startRun(configFile: string): Promise<{ run_id: number }> {
  return api.post('/runs', { config_file: configFile })
}

export function approve(
  runId: number,
  node: string,
  ok: boolean,
  reason: string | null,
): Promise<{ status: string; run_id: number; node: string; approve: boolean }> {
  return api.post(`/runs/${runId}/approve/${node}`, { approve: ok, reason })
}

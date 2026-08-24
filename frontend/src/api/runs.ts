import { api } from './request'

export interface RunListItem {
  id: number
  name: string
  created_at: string | null
  finished_at: string | null
  status: string
  error: string | null
  config_file: string
}

export interface NodeSnapshot {
  status: string
  output: unknown
  error: string | null
  attempts: number
  duration_ms: number
  payload?: unknown
}

export interface RunDetail extends RunListItem {
  config_file: string
  mermaid: string
  nodes: Record<string, NodeSnapshot>
  inputs: Record<string, unknown>
}

export interface RunListSummary {
  /** 全局计数（不受筛选影响）：running 驱动轮询，active（含待审核）驱动电流 */
  running: number
  active: number
}

export interface RunListPage {
  items: RunListItem[]
  /** 筛选后总数 */
  total: number
  offset: number
  limit: number
  summary: RunListSummary
}

/** 列表筛选条件；空字符串表示不筛（api 层按 truthy 判断） */
export interface RunFilters {
  status: string
  configFile: string
}

export function listRuns(offset = 0, limit = 50, filters?: RunFilters): Promise<RunListPage> {
  const params: Record<string, string> = { offset: String(offset), limit: String(limit) }
  if (filters?.status) params.status = filters.status
  if (filters?.configFile) params.config_file = filters.configFile
  return api.get('/runs', { params })
}

export function getRun(runId: number): Promise<RunDetail> {
  return api.get(`/runs/${runId}`)
}

/** 删除终态 run（后端拒绝非终态：运行中/待审核不可删） */
export function deleteRun(runId: number): Promise<{ deleted: number }> {
  return api.delete(`/runs/${runId}`)
}

export function startRun(
  configFile: string,
  inputs?: Record<string, unknown>,
): Promise<{ run_id: number }> {
  // inputs 非空才带上：不传时后端行为与旧版一致（只用 YAML 默认 inputs）
  const body = inputs && Object.keys(inputs).length ? { config_file: configFile, inputs } : { config_file: configFile }
  return api.post('/runs', body)
}

export function approve(
  runId: number,
  node: string,
  ok: boolean,
  reason: string | null,
  edits?: Record<string, string>,
): Promise<{ status: string; run_id: number; node: string; approve: boolean }> {
  // edits 为空对象时不带（后端视为无修订）
  const hasEdits = edits !== undefined && Object.keys(edits).length > 0
  return api.post(`/runs/${runId}/approve/${node}`, { approve: ok, reason, edits: hasEdits ? edits : undefined })
}

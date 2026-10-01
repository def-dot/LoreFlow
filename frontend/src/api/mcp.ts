import { api } from './request'

export type McpStatus = 'connecting' | 'connected' | 'failed'

export interface McpServer {
  name: string
  transport: string
  status: McpStatus
  error: string | null
  tool_names: string[]
  connected_at: string | null
}

export interface McpServerConfig {
  name: string
  command?: string | null
  args?: string[]
  url?: string | null
  env: Record<string, string>
}

export interface McpServersPayload {
  mcpServers: Record<string, Record<string, any>>
}

export function listMcpServers(): Promise<McpServer[]> {
  return api.get('/mcp/servers')
}

export async function getMcpServerConfig(name: string): Promise<McpServerConfig> {
  const res = await api.get<{ mcpServers: Record<string, McpServerConfig> }>(
    `/mcp/servers/${encodeURIComponent(name)}`,
  )
  return { name, ...res.mcpServers[name] }
}

export function createMcpServer(cfg: McpServersPayload): Promise<McpServer> {
  return api.post('/mcp/servers', cfg)
}

export function updateMcpServer(name: string, cfg: McpServersPayload): Promise<McpServer> {
  return api.put(`/mcp/servers/${encodeURIComponent(name)}`, cfg)
}

export function deleteMcpServer(name: string): Promise<{ deleted: boolean }> {
  return api.delete(`/mcp/servers/${encodeURIComponent(name)}`)
}

export function reconnectMcpServer(name: string): Promise<McpServer> {
  return api.post(`/mcp/servers/${encodeURIComponent(name)}/reconnect`)
}

export function reconnectAllMcpServers(): Promise<McpServer[]> {
  return api.post('/mcp/servers/reconnect-all')
}

import { api } from './request'

export type McpStatus = 'disconnected' | 'connecting' | 'connected' | 'failed' | 'disabled'

export interface McpServer {
  name: string
  transport: string
  status: McpStatus
  enabled: boolean
  error: string | null
  tool_names: string[]
  connected_at: string | null
  endpoint: string
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

export function listMcpServers(): Promise<{ servers: McpServer[] }> {
  return api.get('/mcp/servers')
}

export function getMcpServerConfig(name: string): Promise<McpServerConfig> {
  return api.get(`/mcp/servers/${encodeURIComponent(name)}/config`)
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

export function reconnectAllMcpServers(): Promise<{ servers: McpServer[] }> {
  return api.post('/mcp/servers/reconnect-all')
}

export function setMcpServerEnabled(name: string, enabled: boolean): Promise<McpServer> {
  return api.post(`/mcp/servers/${encodeURIComponent(name)}/enable`, { enabled })
}

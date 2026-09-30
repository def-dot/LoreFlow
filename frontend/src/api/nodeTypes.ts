import { api } from './request'

export interface JsonSchema {
  type?: string
  title?: string
  description?: string
  properties?: Record<string, JsonSchema>
  required?: string[]
  items?: JsonSchema
  anyOf?: JsonSchema[]
  $ref?: string
  $defs?: Record<string, JsonSchema>
  default?: unknown
  additionalProperties?: boolean | JsonSchema
}

export interface SourceInfo {
  kind: 'builtin' | 'plugin' | 'mcp'
  name: string
}

export interface NodeTypeInfo {
  name: string
  kind?: 'function' | 'condition'
  label: string
  description: string
  metadata?: Record<string, any>
  input_schema?: JsonSchema | null
  output_schema?: JsonSchema | null
  /** 消费方：node = 工作流节点，tool = Agent 工具 */
  roles?: string[]
  source?: SourceInfo
}

export function listNodeTypes(): Promise<NodeTypeInfo[]> {
  return api.get('/node-types')
}

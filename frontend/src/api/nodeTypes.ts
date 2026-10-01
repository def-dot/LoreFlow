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

export interface FuncInfo {
  name: string
  label: string
  description: string
  metadata?: Record<string, any>
  input_schema?: JsonSchema | null
  output_schema?: JsonSchema | null
}

/** @deprecated 用 FuncInfo */
export type NodeTypeInfo = FuncInfo

export function listNodeTypes(): Promise<FuncInfo[]> {
  return api.get('/node-types')
}
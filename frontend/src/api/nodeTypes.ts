import { api } from './request'

export interface SchemaField {
  type: string
  description?: string
  required?: boolean
  fields?: Record<string, SchemaField>
  item?: SchemaField
}

export interface NodeTypeInfo {
  name: string
  kind: 'function' | 'condition'
  label: string
  description: string
  group?: string | null
  input_schema?: Record<string, SchemaField> | null
  output_schema?: SchemaField | null
}

export function listNodeTypes(): Promise<{ node_types: NodeTypeInfo[] }> {
  return api.get('/node-types')
}

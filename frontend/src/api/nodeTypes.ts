import { api } from './request'

export interface NodeTypeInfo {
  name: string
  kind: 'function' | 'condition'
  label: string
  description: string
}

export function listNodeTypes(): Promise<{ node_types: NodeTypeInfo[] }> {
  return api.get('/node-types')
}

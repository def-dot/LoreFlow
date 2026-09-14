import { api } from './request'

export interface KnowledgeBase {
  id: number
  name: string
  description: string
}

export interface DocumentItem {
  id: number
  filename: string
  status: 'processing' | 'ready' | 'error'
  chunk_count: number
  error: string | null
  created_at: string | null
  kb_id: number
  kb_name: string
}

// 文档（扁平接口）
export const listAllDocuments = () =>
  api.get<DocumentItem[]>('/documents')

export const deleteDocument = (docId: number) =>
  api.delete(`/documents/${docId}`)

// KnowledgeBase CRUD
export const listKnowledgeBases = () =>
  api.get<KnowledgeBase[]>('/knowledge-bases')

export const createKnowledgeBase = (data: { name: string; description?: string }) =>
  api.post<KnowledgeBase>('/knowledge-bases', data)

export const deleteKnowledgeBase = (id: number) =>
  api.delete(`/knowledge-bases/${id}`)

// 上传文档到指定 KB
export const uploadDocument = (kbId: number, file: File) => {
  const formData = new FormData()
  formData.append('file', file)
  return api.post<{ doc_id: number; filename: string; chunk_count: number }>(
    `/knowledge-bases/${kbId}/documents/upload`,
    formData,
  )
}

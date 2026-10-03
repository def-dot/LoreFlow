import { api } from './request'

export interface KnowledgeBase {
  id: number
  name: string
  description: string
}

export interface DocumentItem {
  id: number
  filename: string
  status: 'pending' | 'processing' | 'completed' | 'failed' | 'cancelled'
  chunk_count: number
  error: string | null
  parse_duration_ms: number | null
  created_at: string | null
  kb_id: number
  kb_name: string
}

export interface DocumentListResponse {
  items: DocumentItem[]
  total: number
}

export interface ChunkItem {
  id: number
  document_id: number
  file_name: string | null
  page_numbers: number[]
  heading_context: string
  raw_content: string
}

export interface ChunkListResponse {
  items: ChunkItem[]
  total: number
}

export interface StatusCounts {
  pending: number
  processing: number
  completed: number
  failed: number
  cancelled: number
}

// 文档（扁平接口）
export const listAllDocuments = (params?: { limit?: number; offset?: number; status?: string }) =>
  api.get<DocumentListResponse>('/documents', { params })

export const getDocument = (docId: number) =>
  api.get<DocumentItem>(`/documents/${docId}`)

export const getDocumentStatusCounts = () =>
  api.get<StatusCounts>('/documents/status')

export const getDocumentChunks = (docId: number, params?: { page?: number; page_size?: number; q?: string }) =>
  api.get<ChunkListResponse>(`/documents/${docId}/chunks`, { params })

export const deleteDocument = (docId: number) =>
  api.delete(`/documents/${docId}`)

export const cancelDocument = (docId: number) =>
  api.post(`/documents/${docId}/cancel`)

export const retryDocument = (docId: number) =>
  api.post(`/documents/${docId}/retry`)

// KnowledgeBase CRUD
export const listKnowledgeBases = () =>
  api.get<KnowledgeBase[]>('/knowledge-bases')

export const createKnowledgeBase = (data: { name: string; description?: string }) =>
  api.post<KnowledgeBase>('/knowledge-bases', data)

export const deleteKnowledgeBase = (id: number) =>
  api.delete(`/knowledge-bases/${id}`)

// 上传文档到指定 KB（保留旧接口）
export const uploadDocument = (kbId: number, file: File) => {
  const formData = new FormData()
  formData.append('file', file)
  return api.post<{ doc_id: number; filename: string; status: string }>(
    `/knowledge-bases/${kbId}/documents/upload`,
    formData,
  )
}

// 直接上传文档（kbId 可选，留空归入默认知识库）
export const uploadDocumentDirect = (file: File, kbId?: number) => {
  const formData = new FormData()
  formData.append('file', file)
  if (kbId != null) formData.append('kb_id', String(kbId))
  return api.post<{ doc_id: number; filename: string; status: string }>(
    '/documents/upload',
    formData,
  )
}

// 解析结果对照
export interface PageData {
  page_no: number
  total: number
  markdown: string
  table_count: number
  picture_count: number
}

export const getDocumentPage = (docId: number, pageNo: number) =>
  api.get<PageData>(`/documents/${docId}/pages/${pageNo}`)
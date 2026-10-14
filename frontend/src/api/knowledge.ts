import { api } from './request'

export interface DocumentItem {
  id: number
  filename: string
  status: 'pending' | 'processing' | 'completed' | 'failed' | 'cancelled'
  chunk_count: number
  error: string | null
  parse_duration_ms: number | null
  file_size: number | null
  created_at: string | null
  tags: string[]
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
export const listAllDocuments = (params?: {
  limit?: number
  offset?: number
  status?: string
  q?: string
  tags?: string[]
}) => api.get<DocumentListResponse>('/documents', {
  params: { ...params, tags: params?.tags?.length ? params.tags.join(',') : undefined },
})

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

export const updateDocumentTags = (docId: number, tags: string[]) =>
  api.put(`/documents/${docId}/tags`, { tags })

// 直接上传文档（批量，逐文件返回入库结果）
export interface UploadResult {
  filename: string
  doc_id: number | null
  status: string | null
  error: string | null
  error_code: 'duplicate_name' | null
}

// 推断标签
export interface InferTagsItem {
  stored_name: string
  suggested_tags: string[]
}

export const inferTags = (storedNames: string[]) =>
  api.post<{ items: InferTagsItem[] }>('/documents/infer-tags', { stored_names: storedNames })

// 确认入库（文件已在盘上，传 stored_name + 标签名）
export const uploadDocuments = (items: { stored_name: string; filename: string; tags: string[] }[]) =>
  api.post<{ items: UploadResult[] }>('/documents/upload', { items })

// 标签 CRUD（以标签名为键）
export const listTags = () =>
  api.get<string[]>('/tags')

export const createTag = (name: string) =>
  api.post<{ name: string }>('/tags', { name })

export const updateTag = (oldName: string, newName: string) =>
  api.put<{ name: string }>(`/tags/${encodeURIComponent(oldName)}`, { name: newName })

export const deleteTag = (name: string) =>
  api.delete(`/tags/${encodeURIComponent(name)}`)

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

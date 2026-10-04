import { api } from './request'

export interface TagInfo {
  id: number
  name: string
}

export interface DocumentItem {
  id: number
  filename: string
  status: 'pending' | 'processing' | 'completed' | 'failed' | 'cancelled'
  chunk_count: number
  error: string | null
  parse_duration_ms: number | null
  file_size: number | null
  created_at: string | null
  tags: TagInfo[]
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
  tag_ids?: number[]
}) => api.get<DocumentListResponse>('/documents', {
  params: { ...params, tag_ids: params?.tag_ids?.length ? params.tag_ids.join(',') : undefined },
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

export const updateDocumentTags = (docId: number, tagIds: number[]) =>
  api.put(`/documents/${docId}/tags`, { tag_ids: tagIds })

// 直接上传文档
export const uploadDocumentDirect = (file: File, tagIds?: number[]) => {
  const formData = new FormData()
  formData.append('file', file)
  if (tagIds && tagIds.length) formData.append('tag_ids', tagIds.join(','))
  return api.post<{ doc_id: number; filename: string; status: string }>(
    '/documents/upload',
    formData,
  )
}

// 标签 CRUD
export const listTags = () =>
  api.get<TagInfo[]>('/tags')

export const createTag = (name: string) =>
  api.post<TagInfo>('/tags', { name })

export const updateTag = (id: number, name: string) =>
  api.put<TagInfo>(`/tags/${id}`, { name })

export const deleteTag = (id: number) =>
  api.delete(`/tags/${id}`)

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
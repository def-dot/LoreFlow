import { api } from './request'

export interface UploadRecord {
  id: number
  /** UUID 存储文件名（如 "a1b2c3d4.txt"）：run 时按它读盘 */
  stored_name: string
  /** 原始文件名（展示 / doc_id / title 来源） */
  filename: string
  size: number
  created_at: string
}

// file 参数（file: true）：选文件即上传到服务端，返回引用供 run 参数使用
export async function uploadFile(file: File): Promise<UploadRecord> {
  const form = new FormData()
  form.append('file', file)
  return api.post<UploadRecord>('/uploads', form)
}
import { api } from './request'

export interface UploadOut {
  /** 存储文件名（uuid+扩展名）：run 时按它读盘 */
  id: string
  /** 原始文件名（展示 / doc_id / title 来源） */
  filename: string
  size: number
}

// file 参数（file: true）：选文件即上传到服务端，返回 {id, filename} 引用供 run 参数使用
export async function uploadFile(file: File): Promise<UploadOut> {
  const form = new FormData()
  form.append('file', file)
  return api.post<UploadOut>('/uploads', form)
}

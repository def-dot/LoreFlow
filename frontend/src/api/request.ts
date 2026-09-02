import axios, { type AxiosRequestConfig } from 'axios'
import { ElMessage } from 'element-plus'

/**
 * 统一信封 {code, msg, data} 解包：
 * - 成功（2xx）：拦截器剥掉信封直接返回 data；非信封响应（如 /health）原样返回
 * - 失败：ElMessage 弹出 msg，reject
 */
const request = axios.create({
  baseURL: '/api/v1',
  timeout: 30000,
})

request.interceptors.response.use(
  (response) => {
    const body = response.data
    if (body && typeof body === 'object' && 'code' in body && 'data' in body) {
      return (body as { data: unknown }).data as unknown as typeof response
    }
    return response
  },
  (error) => {
    const msg: string = error.response?.data?.msg || error.message || '请求失败'
    ElMessage.error(msg)
    return Promise.reject(error)
  },
)

// 类型化请求助手：T 为信封 data 的类型
export const api = {
  get: <T>(url: string, config?: AxiosRequestConfig): Promise<T> =>
    request.get(url, config) as unknown as Promise<T>,
  post: <T>(url: string, data?: unknown): Promise<T> => request.post(url, data) as unknown as Promise<T>,
  put: <T>(url: string, data?: unknown): Promise<T> => request.put(url, data) as unknown as Promise<T>,
  delete: <T>(url: string, config?: AxiosRequestConfig): Promise<T> =>
    request.delete(url, config) as unknown as Promise<T>,
}

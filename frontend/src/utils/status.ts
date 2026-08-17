// 节点/run 状态 → element-plus tag 类型
const STATUS_TAG_TYPE: Record<string, 'success' | 'warning' | 'danger' | 'info' | 'primary'> = {
  completed: 'success',
  running: 'primary',
  reviewing: 'warning',
  retrying: 'warning',
  failed: 'danger',
  skipped: 'info',
  cancelled: 'info',
  pending: 'info',
}

export function statusTagType(status: string): 'success' | 'warning' | 'danger' | 'info' | 'primary' {
  return STATUS_TAG_TYPE[status] ?? 'info'
}

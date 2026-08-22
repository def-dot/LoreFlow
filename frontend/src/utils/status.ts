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

// tag 类型 → 状态点颜色（element-plus 色系，primary 沿用页面的选中蓝）
const TAG_COLOR: Record<string, string> = {
  success: '#67c23a',
  primary: '#3b82f6',
  warning: '#e6a23c',
  danger: '#f56c6c',
  info: '#909399',
}

// 列表项用小圆点代替 tag：颜色含义与详情页 tag 保持一致
export function statusColor(status: string): string {
  return TAG_COLOR[statusTagType(status)]
}

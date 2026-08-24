// 节点/run 状态 → element-plus tag 类型
const STATUS_TAG_TYPE: Record<string, 'success' | 'warning' | 'danger' | 'info' | 'primary'> = {
  completed: 'success',
  running: 'primary',
  reviewing: 'warning',
  retrying: 'warning',
  failed: 'danger',
  upstream_failed: 'danger', // 失败的爆炸半径：归失败色系
  skipped: 'info',
  cancelled: 'info',
  pending: 'info',
}

export function statusTagType(status: string): 'success' | 'warning' | 'danger' | 'info' | 'primary' {
  return STATUS_TAG_TYPE[status] ?? 'info'
}

// 状态值 → 中文标签（后端枚举保持英文，仅展示层翻译；未知值原样显示）
const STATUS_LABEL: Record<string, string> = {
  completed: '已完成',
  running: '运行中',
  reviewing: '待审核',
  retrying: '重试中',
  failed: '失败',
  upstream_failed: '上游失败',
  skipped: '已跳过',
  cancelled: '已取消',
  pending: '待执行',
}

export function statusLabel(status: string): string {
  return STATUS_LABEL[status] ?? status
}

// tag 类型 → 状态点颜色（与页面语义色一致：primary=青 流转，warning=琥珀 人工）
const TAG_COLOR: Record<string, string> = {
  success: '#57c88a',
  primary: '#4dc4b2',
  warning: '#f0c24b',
  danger: '#ef7370',
  info: '#8790b0',
}

// 列表项用小圆点代替 tag：颜色含义与详情页 tag 保持一致
export function statusColor(status: string): string {
  return TAG_COLOR[statusTagType(status)]
}

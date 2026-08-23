<script setup lang="ts">
import { computed } from 'vue'
import type { NodeSnapshot, RunDetail } from '@/api/runs'
import { statusLabel, statusTagType } from '@/utils/status'

const props = defineProps<{ detail: RunDetail }>()

interface Row extends NodeSnapshot {
  name: string
}

const rows = computed<Row[]>(() =>
  Object.entries(props.detail.nodes).map(([name, node]) => ({ name, ...node })),
)

// 跳过原因：两种跳过对用户含义不同，级联跳过说明上游出了问题
const SKIP_REASONS: Record<string, string> = {
  upstream_failed: '上游失败，级联跳过',
  condition_not_met: '条件不满足，分支未执行',
}

// 与旧 UI 一致：completed 显示输出，否则显示 error / 跳过原因
function cellText(row: Row): string {
  if (row.status === 'completed') {
    return row.output === null || row.output === undefined ? '' : JSON.stringify(row.output, null, 1)
  }
  if (row.status === 'skipped') {
    return row.skip_reason ? (SKIP_REASONS[row.skip_reason] ?? row.skip_reason) : '已跳过'
  }
  return row.error ?? ''
}

// 输出/错误共格互斥：错误格子染红自证身份，不用回看状态标签
function isCellError(row: Row): boolean {
  return row.status !== 'completed' && !!row.error
}
</script>

<template>
  <el-table :data="rows" size="small" max-height="420">
    <el-table-column prop="name" label="节点" min-width="120" />
    <el-table-column label="状态" width="110">
      <template #default="{ row }">
        <el-tag :type="statusTagType(row.status)" size="small" disable-transitions>{{ statusLabel(row.status) }}</el-tag>
      </template>
    </el-table-column>
    <el-table-column prop="attempts" label="尝试次数" width="90" />
    <el-table-column prop="duration_ms" label="耗时(ms)" width="90" />
    <el-table-column label="输出 / 错误" min-width="200">
      <template #default="{ row }">
        <pre class="cell-pre" :class="{ err: isCellError(row) }">{{ cellText(row) }}</pre>
      </template>
    </el-table-column>
  </el-table>
</template>

<style scoped>
/* 输出/错误单元格：等宽控制台读出样式 */
.cell-pre {
  margin: 0;
  max-width: 400px;
  max-height: 90px;
  overflow: auto;
  padding: 6px 8px;
  border-radius: 6px;
  background: rgba(10, 14, 27, 0.65);
  color: var(--ink-2);
  font-family: var(--font-mono);
  font-size: 11.5px;
  line-height: 1.5;
  white-space: pre-wrap;
}
/* 错误格：只染文字色（#ff8f8a，深底可读、与参数面板一致），
 * 底色与其余格保持一致 */
.cell-pre.err {
  color: #ff8f8a;
}
/* pending 等无内容行：不渲染空盒子 */
.cell-pre:empty {
  display: none;
}
</style>

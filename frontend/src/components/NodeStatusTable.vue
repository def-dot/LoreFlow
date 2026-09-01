<script setup lang="ts">
import { computed } from 'vue'
import type { NodeSnapshot, RunDetail } from '@/api/runs'
import { statusLabel, statusTagType } from '@/utils/status'

const props = defineProps<{ detail: RunDetail }>()

interface Row extends NodeSnapshot {
  name: string
  displayLabel: string
}

const rows = computed<Row[]>(() =>
  Object.entries(props.detail.nodes).map(([name, node]) => ({
    name,
    ...node,
    displayLabel: node.label || name,
  })),
)

// 与旧 UI 一致：completed 显示输出，否则显示 error / 跳过说明
// skipped 只剩一种含义（条件不满足）；级联跳过/级联失败是独立状态
// upstream_skipped / upstream_failed
function cellText(row: Row): string {
  if (row.status === 'completed') {
    return row.output === null || row.output === undefined ? '' : JSON.stringify(row.output, null, 1)
  }
  if (row.status === 'skipped') {
    return '条件不满足，分支未执行'
  }
  if (row.status === 'upstream_skipped') {
    return '上游路径全部跳过，未执行'
  }
  if (row.status === 'upstream_failed') {
    return '上游失败，未执行'
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
    <el-table-column prop="displayLabel" label="节点" min-width="120" />
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
        <!-- 重试历史：成功/失败都显示，说明「为什么尝试了 N 次」 -->
        <div v-if="row.attempts_log?.length" class="retry-log">
          <div v-for="a in row.attempts_log" :key="`${a.attempt}-${a.at}`">
            <span class="retry-no">#{{ a.attempt }}</span>{{ a.error }}
            <span class="retry-at">{{ a.at }}</span>
          </div>
        </div>
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
/* 重试历史：小字灰底，与输出格同列——#序号 错误 时间 */
.retry-log {
  margin-top: 2px;
  font-size: 11px;
  line-height: 1.6;
  color: var(--ink-3);
}
.retry-no {
  margin-right: 4px;
  font-family: var(--font-mono);
  color: #d9a05b;
}
.retry-at {
  margin-left: 6px;
  font-family: var(--font-mono);
  color: var(--ink-3);
}
/* pending 等无内容行：不渲染空盒子 */
.cell-pre:empty {
  display: none;
}
</style>

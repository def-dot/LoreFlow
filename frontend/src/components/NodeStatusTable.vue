<script setup lang="ts">
import { computed } from 'vue'
import type { NodeSnapshot, RunDetail } from '@/api/runs'
import { statusTagType } from '@/utils/status'

const props = defineProps<{ detail: RunDetail }>()

interface Row extends NodeSnapshot {
  name: string
}

const rows = computed<Row[]>(() =>
  Object.entries(props.detail.nodes).map(([name, node]) => ({ name, ...node })),
)

// 与旧 UI 一致：completed 显示输出，否则显示 error
function cellText(row: Row): string {
  if (row.status === 'completed') {
    return row.output === null || row.output === undefined ? '' : JSON.stringify(row.output, null, 1)
  }
  return row.error ?? ''
}
</script>

<template>
  <el-table :data="rows" size="small" max-height="420">
    <el-table-column prop="name" label="Node" min-width="120" />
    <el-table-column label="Status" width="110">
      <template #default="{ row }">
        <el-tag :type="statusTagType(row.status)" size="small" disable-transitions>{{ row.status }}</el-tag>
      </template>
    </el-table-column>
    <el-table-column prop="attempts" label="Attempts" width="90" />
    <el-table-column prop="duration_ms" label="ms" width="70" />
    <el-table-column label="Output / Error" min-width="200">
      <template #default="{ row }">
        <pre class="cell-pre">{{ cellText(row) }}</pre>
      </template>
    </el-table-column>
  </el-table>
</template>

<style scoped>
.cell-pre {
  margin: 0;
  max-width: 400px;
  max-height: 90px;
  overflow: auto;
  color: #9aa4b2;
  font-size: 12px;
  white-space: pre-wrap;
}
</style>

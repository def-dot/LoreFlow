<script setup lang="ts">
import type { PipelineDetail } from '@/api/pipelines'
import MermaidDiagram from './MermaidDiagram.vue'

defineProps<{ detail: PipelineDetail }>()

function kindTagType(kind: string) {
  return kind === 'human' ? 'warning' : kind === 'loop' ? 'info' : 'primary'
}
</script>

<template>
  <div>
    <div class="detail-head">
      <span class="muted">{{ detail.name }}</span>
      <span class="muted">{{ detail.filename }}</span>
      <el-tag size="small" disable-transitions>{{ detail.node_count }} nodes</el-tag>
    </div>
    <div class="panels">
      <section class="panel">
        <h2>Pipeline</h2>
        <MermaidDiagram :source="detail.mermaid" />
        <p v-if="detail.description" class="muted desc">{{ detail.description }}</p>
      </section>
      <section class="panel">
        <h2>Nodes</h2>
        <el-table :data="detail.nodes" size="small" max-height="420">
          <el-table-column prop="name" label="节点" width="90" />
          <el-table-column label="种类" width="80">
            <template #default="{ row }">
              <el-tag :type="kindTagType(row.kind)" size="small" disable-transitions>
                {{ row.kind }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="功能" min-width="130">
            <template #default="{ row }">
              <div>{{ row.type_label ?? '—' }}</div>
              <div v-if="row.type" class="muted">{{ row.type }}</div>
            </template>
          </el-table-column>
          <el-table-column label="依赖" min-width="90">
            <template #default="{ row }">
              {{ row.depends_on.length ? row.depends_on.join(', ') : '—' }}
            </template>
          </el-table-column>
          <el-table-column label="重试" min-width="110">
            <template #default="{ row }">{{ row.retry ?? '—' }}</template>
          </el-table-column>
          <el-table-column label="条件" min-width="100">
            <template #default="{ row }">{{ row.condition_label ?? '—' }}</template>
          </el-table-column>
          <el-table-column label="说明" min-width="130">
            <template #default="{ row }">{{ row.type_description ?? '—' }}</template>
          </el-table-column>
        </el-table>
      </section>
    </div>
    <section class="panel source-panel">
      <h2>Source</h2>
      <pre class="source">{{ detail.source }}</pre>
    </section>
  </div>
</template>

<style scoped>
.detail-head {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 12px;
  flex-wrap: wrap;
}
/* 自适应：drawer 里单列，宽容器里双列 */
.panels {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
  gap: 18px;
}
.panel {
  background: #1a1d23;
  border: 1px solid #2a2f38;
  border-radius: 10px;
  padding: 14px;
}
.desc {
  margin: 10px 0 0;
}
.source-panel {
  margin-top: 18px;
}
.source {
  margin: 0;
  white-space: pre-wrap;
  word-break: break-all;
  font-size: 12px;
  line-height: 1.5;
  color: #9aa4b2;
  max-height: 420px;
  overflow: auto;
}
</style>

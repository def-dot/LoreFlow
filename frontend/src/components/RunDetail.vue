<script setup lang="ts">
import { computed } from 'vue'
import type { RunDetail } from '@/api/runs'
import { statusTagType } from '@/utils/status'
import MermaidDiagram from './MermaidDiagram.vue'
import NodeStatusTable from './NodeStatusTable.vue'
import ReviewCards from './ReviewCards.vue'

const props = defineProps<{ detail: RunDetail; deciding: boolean }>()

const emit = defineEmits<{ decide: [node: string, approve: boolean, reason: string | null] }>()

// 节点名 → 状态，供 MermaidDiagram 按状态给图里的节点上色
const nodeStatuses = computed(() =>
  Object.fromEntries(Object.entries(props.detail.nodes).map(([name, node]) => [name, node.status])),
)

// 待审批节点：从节点快照筛出 status == "reviewing" 的，payload 供审核卡片展示
const reviewing = computed(() =>
  Object.entries(props.detail.nodes)
    .filter(([, node]) => node.status === 'reviewing')
    .map(([name, node]) => ({ name, payload: node.payload }))
    .sort((a, b) => a.name.localeCompare(b.name)),
)
</script>

<template>
  <div class="detail">
    <div class="detail-head">
      <span class="muted">{{ detail.name }}</span>
      <span class="muted">#{{ detail.id }}<template v-if="detail.created_at"> · {{ detail.created_at }}</template></span>
      <el-tag :type="statusTagType(detail.status)" size="small" disable-transitions>
        {{ detail.status === 'running' ? 'running…' : detail.status }}
      </el-tag>
      <div v-if="detail.error" class="run-error">{{ detail.error }}</div>
    </div>
    <div class="panels">
      <section class="panel">
        <h2>Pipeline</h2>
        <MermaidDiagram :source="detail.mermaid" :statuses="nodeStatuses" />
      </section>
      <section class="panel">
        <h2>Nodes</h2>
        <NodeStatusTable :detail="detail" />
        <template v-if="reviewing.length">
          <h2 class="review-title">Human review</h2>
          <ReviewCards
            :reviewing="reviewing"
            :deciding="deciding"
            @decide="(node, ok, reason) => emit('decide', node, ok, reason)"
          />
        </template>
      </section>
    </div>
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
.run-error {
  color: #f87171;
  white-space: pre-wrap;
  flex-basis: 100%;
}
.panels {
  display: grid;
  grid-template-columns: 1fr 1.2fr;
  gap: 18px;
}
.panel {
  background: #1a1d23;
  border: 1px solid #2a2f38;
  border-radius: 10px;
  padding: 14px;
}
.review-title {
  margin-top: 14px;
}
@media (max-width: 900px) {
  .panels {
    grid-template-columns: 1fr;
  }
}
</style>

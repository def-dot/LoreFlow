<script setup lang="ts">
import { computed, ref } from 'vue'
import type { RunDetail } from '@/api/runs'
import { getRunConfig } from '@/api/runs'
import type { ParamSpec, PipelineDetail } from '@/api/pipelines'
import { statusLabel, statusTagType } from '@/utils/status'
import MermaidDiagram from './MermaidDiagram.vue'
import NodeStatusTable from './NodeStatusTable.vue'
import ReviewCards from './ReviewCards.vue'
import FieldValues, { type FieldValue } from './FieldValues.vue'
import PipelineDetailPanel from './PipelineDetailPanel.vue'

const props = defineProps<{ detail: RunDetail; deciding: boolean; params?: ParamSpec[] }>()

const emit = defineEmits<{
  decide: [node: string, approve: boolean, reason: string | null, values: Record<string, string> | null]
}>()

// 节点名 → 状态，供 MermaidDiagram 按状态给图里的节点上色
const nodeStatuses = computed(() =>
  Object.fromEntries(Object.entries(props.detail.nodes).map(([name, node]) => [name, node.status])),
)

// 待审批节点：run 状态为 reviewing 时，从节点快照筛出 status == "reviewing"
// 的，payload（挂在 output 下）供审核卡片展示。状态门槛与后端 approve 路由
// 对齐——挂起落库窗口期（节点已 reviewing、run 仍 running）不出可点卡片
const reviewing = computed(() => {
  if (props.detail.status !== 'reviewing') return []
  return Object.entries(props.detail.nodes)
    .filter(([, node]) => node.status === 'reviewing')
    .map(([name, node]) => ({
      name,
      label: node.label || name,
      description: node.description ?? null,
      payload: (node.output as { payload?: unknown } | null)?.payload,
    }))
    .sort((a, b) => a.name.localeCompare(b.name))
})

// 创建时的运行时输入快照：数量以 chip 展示，点击弹层逐字段查看
// （后端旧版本无 inputs 字段，?? {} 兜底）
const inputCount = computed(() => Object.keys(props.detail.inputs ?? {}).length)

// 弹层字段：按该 run 流水线的参数声明排序/标注（label 优先），
// 未声明的键（JSON 模式塞的任意键）追加在后、label=键名
const inputFields = computed<FieldValue[]>(() => {
  const inputs = props.detail.inputs ?? {}
  const declared = props.params ?? []
  const fields = declared
    .filter((spec) => spec.name in inputs)
    .map((spec) => ({ key: spec.name, label: spec.label || spec.name, value: inputs[spec.name], required: spec.required }))
  const declaredNames = new Set(declared.map((spec) => spec.name))
  for (const [key, value] of Object.entries(inputs)) {
    if (!declaredNames.has(key)) fields.push({ key, label: key, value })
  }
  return fields
})

// 工作流定义弹窗（点击 pipeline 名称加载配置详情）
const definitionVisible = ref(false)
const definitionLoading = ref(false)
const definitionDetail = ref<PipelineDetail | null>(null)
const definitionError = ref<string | null>(null)

async function openDefinition() {
  definitionVisible.value = true
  if (definitionDetail.value) return // 已加载过，直接复用
  definitionLoading.value = true
  definitionError.value = null
  try {
    definitionDetail.value = await getRunConfig(props.detail.id)
  } catch {
    definitionError.value = '加载工作流配置失败'
  } finally {
    definitionLoading.value = false
  }
}
</script>

<template>
  <div class="detail">
    <div class="detail-head">
      <span class="run-name">{{ detail.name }}</span>
      <span class="run-meta">#{{ detail.id }}<template v-if="detail.created_at"> · {{ detail.created_at }}</template></span>
      <el-tag :type="statusTagType(detail.status)" size="small" disable-transitions>
        {{ detail.status === 'running' ? '运行中…' : statusLabel(detail.status) }}
      </el-tag>
      <span class="run-workflow" @click="openDefinition">⚙️ 查看流水线配置</span>
      <el-popover v-if="inputCount" placement="bottom-start" :width="360" trigger="click">
        <template #reference>
          <span class="run-inputs">⚙ 参数 × {{ inputCount }}</span>
        </template>
        <FieldValues :fields="inputFields" />
      </el-popover>
      <div v-if="detail.error" class="run-error">{{ detail.error }}</div>
    </div>
    <div class="panels">
      <section class="panel">
        <h2>流水线（{{ detail.pipeline }}）</h2>
        <MermaidDiagram :source="detail.mermaid" :statuses="nodeStatuses" />
      </section>
      <section class="panel">
        <h2>节点</h2>
        <NodeStatusTable :detail="detail" />
        <template v-if="reviewing.length">
          <h2 class="review-title">人工审核</h2>
          <ReviewCards
            :reviewing="reviewing"
            :deciding="deciding"
            @decide="(node, ok, reason, values) => emit('decide', node, ok, reason, values)"
          />
        </template>
      </section>
    </div>

    <!-- 工作流定义 drawer -->
    <el-drawer v-model="definitionVisible" size="min(920px, 94vw)">
      <template #title>
        <div class="drawer-title">
          <span class="name">{{ definitionDetail?.name ?? detail.pipeline }}</span>
          <el-tag size="small" type="info">运行 #{{ detail.id }} · 配置快照</el-tag>
        </div>
      </template>
      <div v-loading="definitionLoading" class="definition-body">
        <div v-if="definitionError" class="muted">{{ definitionError }}</div>
        <PipelineDetailPanel v-else-if="definitionDetail" :detail="definitionDetail" />
      </div>
    </el-drawer>
  </div>
</template>

<style scoped>
.detail-head {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 14px;
  flex-wrap: wrap;
}
.run-name {
  font-size: 15px;
  font-weight: 600;
  color: var(--ink);
}
.run-meta {
  font-family: var(--font-mono);
  font-size: 12px;
  color: var(--ink-3);
}
/* 工作流名称（可点击） */
.run-workflow {
  font-family: var(--font-mono);
  font-size: 12px;
  color: var(--ink-3);
  border: 1px solid var(--line);
  border-radius: 6px;
  padding: 2px 8px;
  max-width: 200px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  cursor: pointer;
  transition: border-color 0.2s, color 0.2s;
}
.run-workflow:hover {
  border-color: var(--ink-3);
  color: var(--ink);
}
/* 运行时输入快照 chip：与 run-meta 同级弱化展示，点击弹层逐字段查看 */
.run-inputs {
  font-family: var(--font-mono);
  font-size: 12px;
  color: var(--ink-3);
  border: 1px solid var(--line);
  border-radius: 6px;
  padding: 2px 8px;
  cursor: default;
}
.run-error {
  color: #ffc9c7;
  background: rgba(239, 115, 112, 0.08);
  border: 1px solid rgba(239, 115, 112, 0.25);
  border-radius: 8px;
  padding: 8px 10px;
  font-size: 12px;
  white-space: pre-wrap;
  flex-basis: 100%;
}
.panels {
  display: grid;
  grid-template-columns: 1fr 1.2fr;
  gap: 18px;
}
.panel {
  background: rgba(16, 21, 42, 0.72);
  border: 1px solid var(--line);
  border-radius: 12px;
  padding: 16px;
}
/* 人工审核节标题用琥珀刻度条（与审核卡同语义） */
h2.review-title::before {
  background: var(--amber);
}
.review-title {
  margin-top: 16px;
}
/* 工作流定义 drawer */
:deep(.el-drawer__header) {
  margin-bottom: 0;
}
.drawer-title {
  display: flex;
  align-items: center;
  gap: 10px;
  min-width: 0;
  font-size: 15px;
}
.drawer-title .name {
  font-weight: 600;
  color: var(--ink);
}
.definition-body {
  padding: 20px;
  min-height: 160px;
}
@media (max-width: 900px) {
  .panels {
    grid-template-columns: 1fr;
  }
}
</style>

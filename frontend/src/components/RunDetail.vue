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

// 工作流最终输出：数量以 chip 展示，点击弹层逐字段查看
// 当输出键与节点名一致时，优先显示节点 label
const outputCount = computed(() => Object.keys(props.detail.output ?? {}).length)
const outputFields = computed<FieldValue[]>(() => {
  const nodes = props.detail.nodes ?? {}
  return Object.entries(props.detail.output ?? {}).map(([key, value]) => ({
    key,
    label: nodes[key]?.label || key,
    value,
  }))
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
      <span class="run-workflow" @click="openDefinition">⚙️ 工作流配置</span>
      <div v-if="detail.error" class="run-error">{{ detail.error }}</div>
    </div>
    <div class="panels">
      <section v-if="inputCount" class="panel">
        <h2>输入参数</h2>
        <FieldValues :fields="inputFields" />
      </section>
      <section class="panel">
        <h2>输出</h2>
        <FieldValues v-if="outputCount" :fields="outputFields" />
        <span v-else class="muted">暂无输出</span>
      </section>
      <section class="panel">
        <h2>工作流（{{ detail.pipeline_name }}）</h2>
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

    <!-- 运行配置 YAML -->
    <section v-if="detail.definition" class="yaml-section">
      <h2>运行配置</h2>
      <pre class="yaml-block">{{ detail.definition }}</pre>
    </section>

    <!-- 工作流定义 drawer -->
    <el-drawer v-model="definitionVisible" size="min(920px, 94vw)">
      <template #header>
        <div class="drawer-title">
          <span class="name">{{ definitionDetail?.name ?? detail.pipeline_name }}</span>
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
  max-height: 450px;
  overflow-y: auto;
}
/* 人工审核节标题用琥珀刻度条（与审核卡同语义） */
h2.review-title::before {
  background: var(--amber);
}
.review-title {
  margin-top: 16px;
}
/* 运行配置 YAML */
.yaml-section {
  margin-top: 18px;
}
.yaml-block {
  margin: 0;
  padding: 16px;
  background: rgba(10, 14, 27, 0.65);
  border: 1px solid var(--line);
  border-radius: 12px;
  color: var(--ink-2);
  font-family: var(--font-mono);
  font-size: 12px;
  line-height: 1.7;
  overflow-x: auto;
  max-height: 400px;
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

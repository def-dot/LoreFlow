<script setup lang="ts">
import { computed, ref } from 'vue'
import { ElMessage } from 'element-plus'
import type { PipelineDetail } from '@/api/pipelines'
import type { JsonSchema } from '@/api/nodeTypes'
import MermaidDiagram from './MermaidDiagram.vue'
import SchemaFields from './SchemaFields.vue'

const props = defineProps<{ detail: PipelineDetail; mermaidScale?: number }>()

const tableRef = ref<any>(null)
function handleRowClick(row: any) {
  tableRef.value?.toggleRowExpansion(row)
}

const paramsRows = computed(() => {
  const p = props.detail.params
  if (!p) return []
  return Object.entries(p).map(([name, spec]: [string, any]) => ({
    name,
    title: spec?.title ?? name,
    type: spec?.type ?? 'string',
    description: spec?.description ?? '',
    default: spec?.default,
    required: spec?.required ?? true,
  }))
})

const outputRows = computed(() => {
  const endNode = props.detail.nodes?.find((n: any) => n.type === 'end')
  const o = endNode?.output_schema
  if (!o?.properties) return []
  const rows: { name: string; type: string; description: string; depth: number }[] = []
  function walk(props: Record<string, any>, depth: number) {
    for (const [name, spec] of Object.entries(props)) {
      const type = spec?.type ?? 'string'
      rows.push({ name, type, description: spec?.description ?? '', depth })
      if (type === 'object' && spec?.properties) {
        walk(spec.properties, depth + 1)
      }
    }
  }
  walk(o.properties, 0)
  return rows
})

function typeTagType(type: string | null) {
  return type === 'human' ? 'warning' : type === 'loop' ? 'info' : type === 'end' ? 'success' : 'primary'
}

// 节点 name → label 映射
const nodeLabelMap = computed(() =>
  Object.fromEntries(props.detail.nodes.map((n) => [n.name, n.label ?? n.name])),
)
function dependLabels(names: string[]): string {
  return names.map((n) => nodeLabelMap.value[n] ?? n).join(', ')
}

// schema 格式化
function unwrap(schema: JsonSchema): JsonSchema {
  if (schema.anyOf) {
    const nonNull = schema.anyOf.filter(s => s.type !== 'null')
    if (nonNull.length === 1) return nonNull[0]
  }
  return schema
}

function effective(schema: JsonSchema, root?: JsonSchema): JsonSchema {
  const s = unwrap(schema)
  if (s.$ref) return effective(resolveLocalRef(s.$ref, root), root)
  return s
}

function resolveLocalRef(ref: string, root?: JsonSchema): JsonSchema {
  const name = ref.replace(/^#\/\$defs\//, '')
  return root?.$defs?.[name] ?? { $ref: ref }
}

function schemaTypeLabel(field: JsonSchema, root?: JsonSchema): string {
  const f = effective(field, root)
  if (f.type === 'array' && f.items) return `list[${effective(f.items, root).type ?? '?'}]`
  if (f.type === 'object' && f.properties) {
    const keys = Object.keys(f.properties)
    return keys.length ? `{${keys.join(', ')}}` : 'object'
  }
  return f.type ?? '?'
}

interface RetryInfo {
  max: number
  base?: number
  factor?: number
  backoffMax?: number
  jitter?: boolean
  on?: string[]
}

function parseRetry(retry: unknown): RetryInfo | null {
  if (retry == null) return null
  if (typeof retry === 'number') return { max: retry }
  if (typeof retry === 'object' && retry !== null) {
    const r = retry as Record<string, unknown>
    const max = r.max_retries as number ?? 0
    if (!max) return null
    return {
      max,
      base: r.backoff_base as number | undefined,
      factor: r.backoff_factor as number | undefined,
      backoffMax: r.backoff_max as number | undefined,
      jitter: r.jitter as boolean | undefined,
      on: r.retry_on as string[] | undefined,
    }
  }
  return null
}

function retrySummary(retry: unknown): string {
  const info = parseRetry(retry)
  if (!info) return '—'
  let s = `${info.max}次`
  if (info.base || info.factor || info.backoffMax) {
    s += ` ${info.base ?? 1}s×${info.factor ?? 2}^n≤${info.backoffMax ?? 60}s`
  }
  return s
}

function wiringLines(inputs: Record<string, unknown> | null): string[] {
  if (!inputs || !Object.keys(inputs).length) return []
  return Object.entries(inputs).map(([k, v]) => {
    if (typeof v === 'object' && v !== null) return `${k}: ${JSON.stringify(v)}`
    return `${k}: ${v}`
  })
}

async function copySource() {
  await navigator.clipboard.writeText(props.detail.source)
  ElMessage.success('已复制')
}
</script>

<template>
  <div>
    <!-- 工作流基本信息 -->
    <div class="info-header">
      <h2 class="info-name">{{ detail.name }}</h2>
      <span v-if="detail.description" class="info-desc">{{ detail.description }}</span>
    </div>
    <div class="io-panels">
      <section class="panel params-panel">
        <h2>输入参数</h2>
        <el-table v-if="paramsRows.length" :data="paramsRows" size="small" max-height="200">
          <el-table-column prop="name" label="参数名" width="120" />
          <el-table-column prop="title" label="标题" width="120" />
          <el-table-column prop="type" label="类型" width="80" />
          <el-table-column prop="description" label="说明" min-width="160" />
          <el-table-column label="默认值" width="120">
            <template #default="{ row }">{{ row.default ?? '—' }}</template>
          </el-table-column>
          <el-table-column label="必填" width="60">
            <template #default="{ row }">{{ row.required ? '✓' : '—' }}</template>
          </el-table-column>
        </el-table>
        <span v-else class="muted">无输入参数</span>
      </section>
      <section class="panel output-panel">
        <h2>输出参数</h2>
        <el-table v-if="outputRows.length" :data="outputRows" size="small" max-height="200">
          <el-table-column label="字段名" min-width="120">
            <template #default="{ row }">
              <span :style="{ paddingLeft: row.depth * 16 + 'px' }">{{ row.name }}</span>
            </template>
          </el-table-column>
          <el-table-column prop="type" label="类型" width="100" />
          <el-table-column prop="description" label="说明" min-width="160" />
        </el-table>
        <span v-else class="muted">无输出定义</span>
      </section>
    </div>
    <div class="panels">
      <section class="panel">
        <h2>工作流</h2>
        <MermaidDiagram :key="detail.name" :source="detail.mermaid" :scale="mermaidScale" />
      </section>
      <section class="panel">
        <h2>节点</h2>
        <el-table ref="tableRef" :data="detail.nodes" size="small" row-key="name" @row-click="handleRowClick">
          <el-table-column type="expand">
            <template #default="{ row }">
              <div class="schema-expand">
                <div class="schema-col">
                  <h4>输入 Schema</h4>
                  <SchemaFields v-if="row.input_schema?.properties && Object.keys(row.input_schema.properties).length" :schema="row.input_schema" />
                  <span v-else class="schema-empty">无</span>
                </div>
                <div class="schema-col">
                  <h4>输出 Schema</h4>
                  <SchemaFields v-if="row.output_schema?.properties && Object.keys(row.output_schema.properties).length" :schema="row.output_schema" />
                  <span v-else class="schema-empty">无</span>
                </div>
              </div>
            </template>
          </el-table-column>
          <el-table-column label="节点" width="90">
            <template #default="{ row }">{{ row.label ?? row.name }}</template>
          </el-table-column>
          <el-table-column label="类型" width="100">
            <template #default="{ row }">
              <el-tag v-if="row.type" :type="typeTagType(row.type)" size="small" disable-transitions>
                {{ row.type_label ?? row.type }}
              </el-tag>
              <span v-else>—</span>
            </template>
          </el-table-column>
          <el-table-column label="输入" min-width="180">
            <template #default="{ row }">
              <div v-if="wiringLines(row.inputs).length" class="wiring-lines">
                <div v-for="line in wiringLines(row.inputs)" :key="line" class="wiring-line">{{ line }}</div>
              </div>
              <span v-else class="wiring-text">—</span>
            </template>
          </el-table-column>
          <el-table-column label="依赖" width="100">
            <template #default="{ row }">
              <span class="wiring-text">{{ row.depends_on.length ? dependLabels(row.depends_on) : '—' }}</span>
            </template>
          </el-table-column>
          <el-table-column label="重试" width="140">
            <template #default="{ row }">
              <el-tooltip v-if="parseRetry(row.retry)" :content="JSON.stringify(row.retry, null, 2)" placement="top">
                <span class="wiring-text">{{ retrySummary(row.retry) }}</span>
              </el-tooltip>
              <span v-else class="wiring-text">—</span>
            </template>
          </el-table-column>
          <el-table-column label="条件" min-width="120">
            <template #default="{ row }">
              <span v-if="row.condition" class="wiring-text">{{ row.condition }}</span>
              <span v-else class="wiring-text">—</span>
            </template>
          </el-table-column>
          <el-table-column label="说明" min-width="130">
            <template #default="{ row }">
              <el-tooltip v-if="row.description" :content="row.description" placement="top" :show-after="300">
                <span class="desc-text">{{ row.description }}</span>
              </el-tooltip>
              <span v-else class="desc-text">—</span>
            </template>
          </el-table-column>
        </el-table>
      </section>
    </div>
    <section class="panel source-panel">
      <div class="source-head">
        <h2>YAML 源码</h2>
        <el-button size="small" text @click="copySource">复制</el-button>
      </div>
      <pre class="source">{{ detail.source }}</pre>
    </section>
  </div>
</template>

<style scoped>
/* 自适应：drawer 里单列，宽容器里双列 */
.panels {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
  gap: 18px;
}
.panel {
  background: rgba(16, 21, 42, 0.72);
  border: 1px solid var(--line);
  border-radius: 12px;
  padding: 16px;
}
/* 表格滚动条始终可见 */
.panel :deep(.el-table__body-wrapper) {
  scrollbar-color: var(--line-strong) var(--line) !important;
}
.panel :deep(.el-table__body-wrapper)::-webkit-scrollbar-track {
  background: var(--line) !important;
}
.panel :deep(.el-table__body-wrapper)::-webkit-scrollbar-thumb {
  background: var(--line-strong) !important;
}
/* 行可点击 */
.panel :deep(.el-table__row) {
  cursor: pointer;
}
/* 说明列不换行 */
.desc-text {
  display: block;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.io-panels {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
  gap: 18px;
  margin-bottom: 18px;
}
.params-panel,
.output-panel {
  margin-bottom: 0;
}
/* 工作流信息 */
.info-header {
  display: flex;
  align-items: baseline;
  gap: 12px;
  margin-bottom: 18px;
}
.info-name {
  margin: 0;
  font-size: 18px;
  font-weight: 600;
  color: var(--ink);
  line-height: 1.4;
}
.info-name::before {
  display: none;
}
.info-desc {
  margin: 0;
  font-size: 13px;
  color: var(--ink-3);
  line-height: 1.6;
}
/* 接线/输出文本 */
.wiring-text {
  font-family: var(--font-mono);
  font-size: 11.5px;
  color: var(--ink-2);
}
.wiring-lines {
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.wiring-line {
  font-family: var(--font-mono);
  font-size: 11.5px;
  color: var(--ink-2);
  line-height: 1.4;
}
/* 展开行：输入输出 Schema */
.schema-expand {
  display: flex;
  gap: 32px;
  padding: 12px 16px;
  background: rgba(12, 17, 32, 0.4);
}
.schema-col {
  flex: 1;
  min-width: 0;
}
.schema-col h4 {
  margin: 0 0 8px;
  font-size: 11px;
  font-weight: 600;
  color: var(--ink-3);
  letter-spacing: 0.5px;
}
.schema-empty {
  font-size: 11px;
  color: var(--ink-3);
}
.source-panel {
  margin-top: 18px;
}
.source-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.source {
  margin: 0;
  white-space: pre-wrap;
  word-break: break-all;
  font-size: 12px;
  line-height: 1.6;
  color: var(--ink-2);
  font-family: var(--font-mono);
  max-height: 420px;
  overflow: auto;
}
</style>

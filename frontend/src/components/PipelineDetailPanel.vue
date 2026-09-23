<script setup lang="ts">
import { computed } from 'vue'
import type { PipelineDetail } from '@/api/pipelines'
import type { JsonSchema } from '@/api/nodeTypes'
import MermaidDiagram from './MermaidDiagram.vue'

const props = defineProps<{ detail: PipelineDetail }>()

const paramsRows = computed(() => {
  const p = props.detail.params
  if (!p) return []
  return Object.entries(p).map(([name, spec]: [string, any]) => ({
    name,
    label: spec?.label ?? name,
    type: spec?.type ?? 'string',
    description: spec?.description ?? '',
    default: spec?.default,
    required: spec?.required ?? true,
  }))
})

function typeTagType(type: string | null) {
  return type === 'human' ? 'warning' : type === 'loop' ? 'info' : 'primary'
}

// 节点 name → label 映射（依赖列显示 label 用）
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

function inputsSummary(inputs: Record<string, unknown> | null): string {
  if (!inputs || !Object.keys(inputs).length) return '—'
  return Object.entries(inputs)
    .map(([k, v]) => (typeof v === 'string' && v.startsWith('$') ? `${k}: ${v}` : k))
    .join(', ')
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

function retryTooltip(retry: unknown): string {
  if (retry == null || typeof retry !== 'object') return ''
  return JSON.stringify(retry, null, 2)
}

function inputSchemaTooltip(schema: JsonSchema | null): string {
  if (!schema?.properties) return ''
  const reqSet = new Set(schema.required ?? [])
  return Object.entries(schema.properties)
    .map(([k, v]) => {
      const f = effective(v, schema)
      const req = reqSet.has(k) ? ' (必填)' : ''
      const desc = f.description ? ` — ${f.description}` : ''
      return `${k}: ${schemaTypeLabel(f, schema)}${req}${desc}`
    })
    .join('\n')
}
</script>

<template>
  <div>
    <!-- 无自身头部：仅用于 Runs 页 drawer，名称/文件名由 drawer 标题展示 -->
    <p v-if="detail.description" class="desc">{{ detail.description }}</p>
    <section v-if="detail.params && Object.keys(detail.params).length" class="panel params-panel">
      <h2>参数</h2>
      <el-table :data="paramsRows" size="small" max-height="200">
        <el-table-column prop="name" label="参数名" width="120" />
        <el-table-column prop="label" label="标签" width="120" />
        <el-table-column prop="type" label="类型" width="80" />
        <el-table-column prop="description" label="说明" min-width="160" />
        <el-table-column label="默认值" width="120">
          <template #default="{ row }">{{ row.default ?? '—' }}</template>
        </el-table-column>
        <el-table-column label="必填" width="60">
          <template #default="{ row }">{{ row.required ? '✓' : '—' }}</template>
        </el-table-column>
      </el-table>
    </section>
    <div class="panels">
      <section class="panel">
        <h2>流水线</h2>
        <MermaidDiagram :key="detail.name" :source="detail.mermaid" />
      </section>
      <section class="panel">
        <h2>节点</h2>
        <el-table :data="detail.nodes" size="small">
          <el-table-column label="节点" width="90">
            <template #default="{ row }">{{ row.label ?? row.name }}</template>
          </el-table-column>
          <el-table-column label="类型" min-width="130">
            <template #default="{ row }">
              <template v-if="row.type">
                <span class="type-label" :class="`type-${typeTagType(row.type)}`">
                  {{ row.type_label ?? row.type }}
                </span>
                <el-tooltip v-if="row.type_description" :content="row.type_description" placement="top">
                  <span class="type-help">?</span>
                </el-tooltip>
              </template>
              <span v-else>—</span>
            </template>
          </el-table-column>
          <el-table-column label="输入" min-width="160">
            <template #default="{ row }">
              <el-tooltip
                v-if="row.type_input_schema?.properties && Object.keys(row.type_input_schema.properties).length"
                :content="inputSchemaTooltip(row.type_input_schema)"
                placement="top"
                :show-after="300"
              >
                <span class="wiring-text">{{ inputsSummary(row.inputs) }}</span>
              </el-tooltip>
              <span v-else class="wiring-text">{{ inputsSummary(row.inputs) }}</span>
            </template>
          </el-table-column>
          <el-table-column label="输出" min-width="120">
            <template #default="{ row }">
              <span v-if="row.type_output_schema" class="wiring-text">
                {{ schemaTypeLabel(row.type_output_schema, row.type_output_schema) }}
              </span>
              <span v-else class="wiring-text">—</span>
            </template>
          </el-table-column>
          <el-table-column label="依赖" min-width="90">
            <template #default="{ row }">
              {{ row.depends_on.length ? dependLabels(row.depends_on) : '—' }}
            </template>
          </el-table-column>
          <el-table-column label="重试" min-width="200">
            <template #default="{ row }">
              <template v-if="parseRetry(row.retry)">
                <div class="retry-row"><span class="retry-label">次数</span> {{ parseRetry(row.retry)!.max }}</div>
                <div class="retry-row"><span class="retry-label">退避</span> {{ parseRetry(row.retry)!.base ?? 1 }}s × {{ parseRetry(row.retry)!.factor ?? 2 }}<sup>n</sup> ≤ {{ parseRetry(row.retry)!.backoffMax ?? 60 }}s</div>
                <div v-if="parseRetry(row.retry)!.jitter === false" class="retry-row"><span class="retry-label">抖动</span> 关</div>
                <div v-if="parseRetry(row.retry)!.on?.length" class="retry-row"><span class="retry-label">触发</span> {{ parseRetry(row.retry)!.on!.join(', ') }}</div>
              </template>
              <span v-else class="wiring-text">—</span>
            </template>
          </el-table-column>
          <el-table-column label="条件" min-width="100">
            <template #default="{ row }">{{ row.condition ?? '—' }}</template>
          </el-table-column>
          <el-table-column label="说明" min-width="130">
            <template #default="{ row }">{{ row.description ?? '—' }}</template>
          </el-table-column>
        </el-table>
      </section>
    </div>
    <section class="panel source-panel">
      <h2>YAML 源码</h2>
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
/* 导语：正文字号 + 舒展行高，读起来是「说明文」而非界面杂讯 */
.desc {
  margin: 0 0 18px;
  font-size: 13px;
  line-height: 1.7;
  color: var(--ink-2);
}
/* 节点类型描述问号图标 */
/* 节点类型标签：无背景，纯文字 */
.type-label {
  font-size: 12px;
  font-weight: 500;
}
/* 接线/输出文本 */
.wiring-text {
  font-family: var(--font-mono);
  font-size: 11.5px;
  color: var(--ink-2);
}
/* 节点类型描述问号图标 */
.type-help {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 16px;
  height: 16px;
  margin-left: 4px;
  font-size: 11px;
  color: var(--ink-3);
  border: 1px solid var(--line);
  border-radius: 50%;
  cursor: help;
  vertical-align: middle;
}
.retry-row {
  font-size: 11.5px;
  line-height: 1.6;
  color: var(--ink-2);
}
.retry-label {
  display: inline-block;
  width: 28px;
  color: var(--ink-3);
  font-size: 11px;
}
.params-panel {
  margin-bottom: 18px;
}
.source-panel {
  margin-top: 18px;
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

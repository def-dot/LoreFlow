<script setup lang="ts">
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import mermaid from 'mermaid'

const props = defineProps<{
  source: string
  /** 节点名 → 状态（来自 RunDetail.nodes），用于给图里的节点按状态上色。 */
  statuses?: Record<string, string>
}>()

const graphEl = ref<HTMLElement>()

mermaid.initialize({ startOnLoad: false, theme: 'dark', securityLevel: 'loose' })

// 状态 → mermaid classDef 样式（与 element-plus 深色 tag 色系一致）
interface StatusStyle {
  fill: string
  stroke: string
  color: string
  dashed?: boolean
}

const STATUS_STYLES: Record<string, StatusStyle> = {
  completed: { fill: '#1c3a26', stroke: '#67c23a', color: '#c8efc0' },
  running: { fill: '#123352', stroke: '#409eff', color: '#bfe0ff' },
  reviewing: { fill: '#3a2e10', stroke: '#e6a23c', color: '#ffe2ae' },
  retrying: { fill: '#3a2e10', stroke: '#e6a23c', color: '#ffe2ae', dashed: true },
  failed: { fill: '#3a1d1d', stroke: '#f56c6c', color: '#ffc7c7' },
  skipped: { fill: '#23262d', stroke: '#909399', color: '#c6cbd3', dashed: true },
  cancelled: { fill: '#23262d', stroke: '#909399', color: '#c6cbd3' },
  pending: { fill: '#1f232b', stroke: '#5c6675', color: '#9aa4b2', dashed: true },
}

// 与后端 dag.to_mermaid 一致的节点 id 消毒规则：空格/连字符 → 下划线
const sanitizeId = (name: string) => name.replace(/[ -]/g, '_')

const legend = computed(() => {
  if (!props.statuses) return []
  const used = new Set(Object.values(props.statuses))
  // 图里存在但还没任何快照的节点 → 视为 pending
  const statusIds = new Set(Object.keys(props.statuses).map(sanitizeId))
  const ids = [...props.source.matchAll(/^\s{4}([\w]+)\["/gm)].map((m) => m[1])
  if (ids.some((id) => !statusIds.has(id))) used.add('pending')
  return [...used].filter((s) => STATUS_STYLES[s])
})

/** 在 mermaid 源码尾部追加 classDef / class 语句，按节点状态上色。 */
function decorate(source: string, statuses?: Record<string, string>): string {
  if (!statuses) return source
  const lines = [source]
  for (const status of Object.keys(STATUS_STYLES)) {
    const style = STATUS_STYLES[status]
    let def = `    classDef st_${status} fill:${style.fill},stroke:${style.stroke},color:${style.color}`
    if (style.dashed) def += ',stroke-dasharray:5 3'
    lines.push(def)
  }
  const byClass = new Map<string, string[]>()
  for (const [name, status] of Object.entries(statuses)) {
    const key = STATUS_STYLES[status] ? status : 'pending'
    const list = byClass.get(key) ?? []
    list.push(sanitizeId(name))
    byClass.set(key, list)
  }
  for (const [status, ids] of byClass) {
    lines.push(`    class ${ids.join(',')} st_${status}`)
  }
  return lines.join('\n')
}

let renderSeq = 0

async function render() {
  if (!graphEl.value) return
  const source = decorate(props.source || 'graph TD\n  none[No pipeline]', props.statuses)
  renderSeq += 1
  try {
    const { svg } = await mermaid.render(`mermaid-${renderSeq}`, source)
    graphEl.value.innerHTML = svg
  } catch (e) {
    graphEl.value.textContent = String(e)
  }
}

onMounted(render)
watch(
  [() => props.source, () => props.statuses],
  async () => {
    await nextTick()
    await render()
  },
)
</script>

<template>
  <div>
    <div ref="graphEl" class="mermaid-graph"></div>
    <div v-if="legend.length" class="legend">
      <span v-for="status in legend" :key="status" class="legend-item">
        <i class="dot" :style="{ background: STATUS_STYLES[status].stroke }" />
        {{ status }}
      </span>
    </div>
  </div>
</template>

<style scoped>
.mermaid-graph {
  overflow-x: auto;
}
.mermaid-graph :deep(svg) {
  max-width: 100%;
}
.legend {
  display: flex;
  flex-wrap: wrap;
  gap: 6px 14px;
  margin-top: 10px;
  font-size: 12px;
  color: #9aa4b2;
}
.legend-item {
  display: inline-flex;
  align-items: center;
  gap: 5px;
}
.dot {
  width: 9px;
  height: 9px;
  border-radius: 50%;
  display: inline-block;
}
</style>

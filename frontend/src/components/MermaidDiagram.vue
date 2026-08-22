<script lang="ts">
// 模块级渲染计数器：跨组件实例全局唯一。
// mermaid.render 会按 id 从整个 document 移除同名元素（清理上次渲染残留），
// 若每个实例各自从 1 计数，RunDetail 的图与预览 drawer 的图会撞 id，
// 后渲染者把先渲染者的 SVG 从 DOM 删掉 → 图凭空消失。
let renderSeq = 0
</script>

<script setup lang="ts">
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import mermaid from 'mermaid'
import { statusLabel } from '@/utils/status'

const props = defineProps<{
  source: string
  /** 节点名 → 状态（来自 RunDetail.nodes），用于给图里的节点按状态上色。 */
  statuses?: Record<string, string>
}>()

const graphEl = ref<HTMLElement>()

mermaid.initialize({
  startOnLoad: false,
  theme: 'dark',
  securityLevel: 'loose',
  // 融入靛蓝图纸：节点底用面板色、线用次级文字色
  themeVariables: {
    background: 'transparent',
    primaryColor: '#161c36',
    primaryBorderColor: '#2e3860',
    primaryTextColor: '#e9edf9',
    lineColor: '#626b8c',
    edgeLabelBackground: '#10152a',
    clusterBkg: 'transparent',
    fontSize: '13px',
    fontFamily: "ui-monospace, 'Cascadia Code', Consolas, 'SF Mono', monospace",
  },
})

// 状态 → mermaid classDef 样式：青 = 机器流转，琥珀 = 人工介入，
// 虚线 = 尚未/未真正执行（pending / skipped / retrying）
interface StatusStyle {
  fill: string
  stroke: string
  color: string
  dashed?: boolean
}

const STATUS_STYLES: Record<string, StatusStyle> = {
  completed: { fill: '#12281d', stroke: '#57c88a', color: '#c9f2dc' },
  running: { fill: '#0d2927', stroke: '#4dc4b2', color: '#c2efe6' },
  reviewing: { fill: '#2e2410', stroke: '#f0c24b', color: '#ffe9b0' },
  retrying: { fill: '#2e2410', stroke: '#f0c24b', color: '#ffe9b0', dashed: true },
  failed: { fill: '#321a1c', stroke: '#ef7370', color: '#ffd3d0' },
  skipped: { fill: '#171c30', stroke: '#8790b0', color: '#c3c9de', dashed: true },
  cancelled: { fill: '#171c30', stroke: '#8790b0', color: '#c3c9de' },
  pending: { fill: '#131830', stroke: '#4a5478', color: '#9aa3c4', dashed: true },
}

// 与后端 dag.to_mermaid 一致的节点 id 消毒规则：空格/连字符 → 下划线
const sanitizeId = (name: string) => name.replace(/[ -]/g, '_')

const legend = computed(() => {
  if (!props.statuses) return []
  const used = new Set(Object.values(props.statuses))
  // 图里存在但还没任何快照的节点 → 视为 pending。
  // 节点名可以是中文（如 08 的 输入内容），用 \p{L}\p{N}（u 标志）而非 \w
  const statusIds = new Set(Object.keys(props.statuses).map(sanitizeId))
  const ids = [...props.source.matchAll(/^\s{4}([\p{L}\p{N}_]+)\["/gmu)].map((m) => m[1])
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

async function render() {
  if (!graphEl.value) return
  const source = decorate(props.source || 'graph TD\n  none[暂无流水线]', props.statuses)
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
        {{ statusLabel(status) }}
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
/* 节点小字行（<i>：类型键 · label）—— 缩小字号、弱化颜色（与图例一致） */
.mermaid-graph :deep(.nodeLabel i) {
  font-size: 11px;
  color: var(--ink-3);
}
.legend {
  display: flex;
  flex-wrap: wrap;
  gap: 6px 14px;
  margin-top: 10px;
  font-size: 12px;
  color: var(--ink-3);
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

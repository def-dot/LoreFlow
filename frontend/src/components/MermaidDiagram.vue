<script setup lang="ts">
import { nextTick, onMounted, ref, watch } from 'vue'
import mermaid from 'mermaid'

const props = defineProps<{ source: string }>()

const graphEl = ref<HTMLElement>()

mermaid.initialize({ startOnLoad: false, theme: 'dark', securityLevel: 'loose' })

let renderSeq = 0

async function render() {
  if (!graphEl.value) return
  const source = props.source || 'graph TD\n  none[No pipeline]'
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
  () => props.source,
  async () => {
    await nextTick()
    await render()
  },
)
</script>

<template>
  <div ref="graphEl" class="mermaid-graph"></div>
</template>

<style scoped>
.mermaid-graph {
  overflow-x: auto;
}
.mermaid-graph :deep(svg) {
  max-width: 100%;
}
</style>

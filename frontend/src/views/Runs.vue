<script setup lang="ts">
import { onMounted, onUnmounted, ref } from 'vue'
import RunList from '@/components/RunList.vue'
import RunDetail from '@/components/RunDetail.vue'
import { useRunsStore } from '@/stores/runs'
import { usePipelinesStore } from '@/stores/pipelines'

const store = useRunsStore()
const pipelinesStore = usePipelinesStore()

// 新建 run 时运行的流水线配置（下拉来自 /pipelines，默认主演示流水线）
const configFile = ref('pipeline.yaml')
// 防止 New run 按钮重复点击导致并发创建
const creating = ref(false)

// ---------------------------------------------------------------------------
// 轮询（沿用旧 index.html 的两路 1s 轮询：列表有 running 就刷新列表；
// 选中的 run 在 running 状态就刷新详情，到终态即停）
// ---------------------------------------------------------------------------
let runsTimer: ReturnType<typeof setInterval> | undefined
let detailTimer: ReturnType<typeof setInterval> | undefined

function stopRunsPolling() {
  if (runsTimer) clearInterval(runsTimer)
  runsTimer = undefined
}

function stopDetailPolling() {
  if (detailTimer) clearInterval(detailTimer)
  detailTimer = undefined
}

function startRunsPolling() {
  stopRunsPolling()
  runsTimer = setInterval(async () => {
    try {
      await store.fetchRuns()
      if (!store.hasRunning) stopRunsPolling()
    } catch {
      stopRunsPolling()
    }
  }, 1000)
}

function startDetailPolling() {
  stopDetailPolling()
  detailTimer = setInterval(async () => {
    try {
      await store.fetchDetail()
      if (store.detail && store.detail.status !== 'running') stopDetailPolling()
    } catch {
      stopDetailPolling()
    }
  }, 1000)
}

async function select(id: number) {
  await store.select(id)
  if (store.detail?.status === 'running') {
    startDetailPolling()
    startRunsPolling()
  } else {
    stopDetailPolling()
  }
  store.fetchRuns().catch(() => {})
}

async function startNewRun() {
  if (creating.value) return
  creating.value = true
  try {
    await store.startNewRun(configFile.value)
    startDetailPolling()
    startRunsPolling()
  } finally {
    creating.value = false
  }
}

async function decide(node: string, ok: boolean, reason: string | null) {
  await store.decide(node, ok, reason)
  if (store.detail?.status === 'running') startDetailPolling()
}

onMounted(async () => {
  try {
    await store.fetchRuns()
    if (store.runs.length) {
      await select(store.runs[0].id)
    }
    if (store.hasRunning) startRunsPolling()
  } catch {
    // 后端未启动时静默，等用户刷新
  }
  pipelinesStore
    .fetchPipelines()
    .then(() => {
      // 默认项不在目录里时退到列表第一项（后端缺省仍是 pipeline.yaml）
      if (!pipelinesStore.pipelines.some((p) => p.filename === configFile.value)) {
        configFile.value = pipelinesStore.pipelines[0]?.filename ?? configFile.value
      }
    })
    .catch(() => {})
})

onUnmounted(() => {
  stopRunsPolling()
  stopDetailPolling()
})
</script>

<template>
  <div class="page">
    <header class="page-head">
      <el-select
        v-model="configFile"
        :disabled="!pipelinesStore.pipelines.length"
        placeholder="选择流水线"
        style="width: 260px"
      >
        <el-option
          v-for="p in pipelinesStore.pipelines"
          :key="p.filename"
          :value="p.filename"
          :label="`${p.name}（${p.filename}）`"
        />
      </el-select>
      <el-button type="primary" :loading="creating" @click="startNewRun">▶ New run</el-button>
    </header>
    <main class="layout">
      <RunList :runs="store.runs" :total="store.total" :selected-id="store.selectedId" @select="select" />
      <RunDetail
        v-if="store.detail"
        :detail="store.detail"
        :deciding="store.deciding"
        @decide="decide"
      />
      <div v-else class="muted">选择或创建一个 run 查看执行状态。</div>
    </main>
  </div>
</template>

<style scoped>
.page-head {
  padding: 12px 20px;
  border-bottom: 1px solid #2a2f38;
  display: flex;
  align-items: center;
  gap: 14px;
}
.page-head h1 {
  font-size: 18px;
  margin: 0;
}
.layout {
  display: grid;
  grid-template-columns: 300px 1fr;
  gap: 18px;
  padding: 18px 20px;
  align-items: start;
}
@media (max-width: 900px) {
  .layout {
    grid-template-columns: 1fr;
  }
}
</style>

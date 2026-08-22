<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import RunList from '@/components/RunList.vue'
import RunDetail from '@/components/RunDetail.vue'
import PipelineDetailPanel from '@/components/PipelineDetailPanel.vue'
import { useRunsStore } from '@/stores/runs'
import { usePipelinesStore } from '@/stores/pipelines'

const store = useRunsStore()
const pipelinesStore = usePipelinesStore()

// 新建 run 时运行的流水线配置（下拉来自 /pipelines，默认主演示流水线）
const configFile = ref('pipeline.yaml')
// 防止 New run 按钮重复点击导致并发创建
const creating = ref(false)

// ---------------------------------------------------------------------------
// 流水线详情 drawer：顶部「预览」看所选流水线，run 详情里的「查看配置」
// 看该 run 跑的配置 —— 同一个 drawer，标题区分来源。详情按文件名缓存
// 并后台重取（SWR）：重复打开秒显，改过 YAML 也会自动跟上，无需刷新按钮
// ---------------------------------------------------------------------------
// 打开来源：selection = 顶部预览所选流水线；run = 查看 run 实际跑的配置
type PreviewSource = { kind: 'selection' } | { kind: 'run'; runId: number }

const previewOpen = ref(false)
const previewLoading = ref(false)
const previewError = ref<string | null>(null)
const previewFile = ref<string | null>(null)
const previewFrom = ref<PreviewSource>({ kind: 'selection' })

// 标题里的流水线中文名：详情未加载时从列表兜底，打开即可见
const previewItem = computed(() =>
  pipelinesStore.pipelines.find((p) => p.filename === previewFile.value),
)

async function loadPreview() {
  if (!previewFile.value) return
  previewError.value = null
  // 无缓存才转圈；有缓存先秒显旧内容，后台重取静默更新
  previewLoading.value = !pipelinesStore.detailCache[previewFile.value]
  try {
    await pipelinesStore.select(previewFile.value)
  } catch {
    // 首次加载失败才报错；已有内容时后台重取失败不打扰
    if (!pipelinesStore.detail) {
      previewError.value = '加载流水线失败，请检查后端是否可用。'
    }
  } finally {
    previewLoading.value = false
  }
}

async function openPreview(filename: string, from: PreviewSource = { kind: 'selection' }) {
  previewFile.value = filename
  previewFrom.value = from
  previewOpen.value = true
  await loadPreview()
}

// run 详情「查看配置」：看该 run 实际跑的配置文件，而非当前下拉选项
function viewRunConfig(configFileOfRun: string, runId: number) {
  openPreview(configFileOfRun, { kind: 'run', runId })
}

// ---------------------------------------------------------------------------
// 轮询（沿用旧 index.html 的两路 1s 轮询：列表有 running 就刷新列表；
// 选中的 run 在 running 状态就刷新详情，到终态即停）
// ---------------------------------------------------------------------------
let runsTimer: ReturnType<typeof setInterval> | undefined
let detailTimer: ReturnType<typeof setInterval> | undefined

// run 终态：详情轮询观察到结束时，刷新一次左侧列表让状态标签跟上
const TERMINAL_STATUSES = ['completed', 'failed', 'cancelled']

async function refreshRunsIfTerminal() {
  const status = store.detail?.status
  if (status && TERMINAL_STATUSES.includes(status)) {
    await store.fetchRuns()
    if (!store.hasRunning) stopRunsPolling()
  }
}

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
      if (store.detail && store.detail.status !== 'running') {
        stopDetailPolling()
        await refreshRunsIfTerminal()
      }
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
  if (store.detail?.status === 'running') {
    // 审批后 run 回到执行中：恢复两路轮询（列表在挂起期间已停）
    startDetailPolling()
    startRunsPolling()
  } else {
    // 续跑瞬间已到终态（无下一轮审核）：直接刷新列表
    refreshRunsIfTerminal().catch(() => {})
  }
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
          :label="p.name"
        />
      </el-select>
      <el-button plain :disabled="!pipelinesStore.pipelines.length" @click="openPreview(configFile)">
        👁 预览
      </el-button>
      <el-button type="primary" :loading="creating" @click="startNewRun">▶ New run</el-button>
    </header>
    <main class="layout">
      <RunList :runs="store.runs" :total="store.total" :selected-id="store.selectedId" @select="select" />
      <RunDetail
        v-if="store.detail"
        :detail="store.detail"
        :deciding="store.deciding"
        @decide="decide"
        @view-config="viewRunConfig"
      />
      <div v-else class="muted">选择或创建一个 run 查看执行状态。</div>
    </main>
    <el-drawer v-model="previewOpen" size="min(920px, 94vw)">
      <template #title>
        <div class="drawer-title">
          <span class="name">{{ pipelinesStore.detail?.name ?? previewItem?.name ?? '流水线详情' }}</span>
          <span class="muted file">
            {{ previewFile }}
            <template v-if="pipelinesStore.detail?.node_count ?? previewItem?.node_count">
              · {{ pipelinesStore.detail?.node_count ?? previewItem?.node_count }} 节点
            </template>
          </span>
          <el-tag v-if="previewFrom.kind === 'run'" size="small" type="info">Run #{{ previewFrom.runId }}</el-tag>
        </div>
      </template>
      <div v-loading="previewLoading" class="drawer-body">
        <div v-if="previewError" class="preview-error">
          <span class="muted">{{ previewError }}</span>
          <el-button size="small" plain @click="loadPreview">重试</el-button>
        </div>
        <PipelineDetailPanel v-else-if="pipelinesStore.detail" :detail="pipelinesStore.detail" />
      </div>
    </el-drawer>
  </div>
</template>

<style scoped>
/* Element 默认 .el-drawer__header margin-bottom:32px，加上 body 自身 20px
 * padding-top，标题下方空隙 ≈52px，远大于左右 20px 的节奏；归零让 body
 * padding 统一提供 20px 间隔 */
:deep(.el-drawer__header) {
  margin-bottom: 0;
}
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
.drawer-title {
  display: flex;
  align-items: center;
  gap: 10px;
  min-width: 0;
  font-size: 16px;
}
.drawer-title .name {
  font-weight: 600;
}
.drawer-title .file {
  font-size: 12px;
}
.drawer-body {
  min-height: 160px;
}
.preview-error {
  display: flex;
  align-items: center;
  gap: 10px;
}
</style>

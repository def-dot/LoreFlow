<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import RunList from '@/components/RunList.vue'
import RunDetail from '@/components/RunDetail.vue'
import PipelineDetailPanel from '@/components/PipelineDetailPanel.vue'
import type { ParamSpec } from '@/api/pipelines'
import { useRunsStore } from '@/stores/runs'
import { usePipelinesStore } from '@/stores/pipelines'

const store = useRunsStore()
const pipelinesStore = usePipelinesStore()

// 新建 run 时运行的流水线配置（下拉来自 /pipelines，默认主演示流水线）
const configFile = ref('pipeline.yaml')
// 防止「新建运行」按钮重复点击导致并发创建
const creating = ref(false)

// ---------------------------------------------------------------------------
// 运行时输入参数：流水线声明了 params 时按声明渲染表单（label/说明/默认值/
// 必填一目了然）；未声明或切「JSON 模式」时退回原始 JSON 文本。
// 空 → 只用 YAML 默认 inputs；无效 JSON → 拦截提交并提示。
// ---------------------------------------------------------------------------
const inputsText = ref('')
// 表单模式的字段值（字符串输入，提交时解析）
const paramValues = ref<Record<string, string>>({})
// 声明了 params 的流水线默认表单模式；未声明的只有 JSON 文本
const jsonMode = ref(false)

const parsedInputs = computed<{ value?: Record<string, unknown>; error: string | null; count: number }>(() => {
  const text = inputsText.value.trim()
  if (!text) return { error: null, count: 0 }
  try {
    const value = JSON.parse(text)
    if (typeof value !== 'object' || value === null || Array.isArray(value)) {
      return { error: '必须是 JSON 对象，如 {"topic": "..."}', count: 0 }
    }
    return { value, error: null, count: Object.keys(value).length }
  } catch {
    return { error: 'JSON 格式无效', count: 0 }
  }
})

// 所选流水线的输入声明（列表接口已带 params/inputs/required_inputs，无需再取详情）
const selectedPipeline = computed(() =>
  pipelinesStore.pipelines.find((p) => p.filename === configFile.value),
)
const paramSpecs = computed(() => selectedPipeline.value?.params ?? [])
// run 详情「⚙ 参数」弹层的声明标签：按该 run 的 config_file 取
// （与创建下拉的选中无关 —— 看旧 run 时下拉可能停在别的流水线上）
const detailParams = computed(() =>
  pipelinesStore.pipelines.find((p) => p.filename === store.detail?.config_file)?.params ?? [],
)
const requiredInputs = computed(() => selectedPipeline.value?.required_inputs ?? [])
const defaultInputs = computed(() => selectedPipeline.value?.inputs ?? {})
const hasDefaults = computed(() => Object.keys(defaultInputs.value).length > 0)
const defaultsJson = computed(() => JSON.stringify(defaultInputs.value, null, 2))

// 切换流水线时清空参数状态（表单值 + JSON 文本）：不同流水线的参数集不同，
// 旧值没有意义 —— 参数按钮未声明时是隐藏的，残留 JSON 会静默随新建运行提交
watch(configFile, () => {
  paramValues.value = {}
  inputsText.value = ''
})

// 字段文本 → 提交值：空串 = 未提供；数字/布尔/JSON 字面量按 JSON 解析，其余原样字符串。
// multiline 字段声明为文本语义，跳过 JSON 启发式（正文以 { 开头不该变成对象）
function parseFieldValue(raw: string, multiline: boolean): unknown {
  const text = raw.trim()
  if (!multiline && (/^-?\d+(\.\d+)?$/.test(text) || text === 'true' || text === 'false' || /^[{\[]/.test(text))) {
    try {
      return JSON.parse(text)
    } catch {
      // 形似 JSON 但不合法：按原字符串提交
    }
  }
  return raw
}

// 表单模式实际提交的参数：只收非空字段（留空 = 用默认值或不传）
const formInputs = computed(() => {
  const value: Record<string, unknown> = {}
  for (const spec of paramSpecs.value) {
    const raw = (paramValues.value[spec.name] ?? '').trim()
    if (raw !== '') value[spec.name] = parseFieldValue(raw, spec.multiline)
  }
  return value
})

// 当前模式下随「新建运行」提交的参数（表单实时收集 / JSON 文本解析）
const submitInputs = computed<{ value?: Record<string, unknown>; error: string | null; count: number }>(
  () => {
    if (jsonMode.value || !paramSpecs.value.length) return parsedInputs.value
    const value = formInputs.value
    return { value, error: null, count: Object.keys(value).length }
  },
)

// 必填键缺失：JSON 无效时视为全部缺失（无法确认已提供）；提交前拦截 + 后端兜底 400
const missingRequired = computed(() => {
  const provided = submitInputs.value.error ? {} : (submitInputs.value.value ?? {})
  return requiredInputs.value.filter((k) => !(k in provided))
})

// 表单模式 = 声明了参数且未切 JSON：弹层主体按声明渲染字段
const formMode = computed(() => !jsonMode.value && paramSpecs.value.length > 0)

// 参数键 → 展示名（JSON 模式标签用）：label 优先，与键不同才附 (key) 供照抄；
// 简式声明 label=键名，展示不变
function keyLabel(name: string): string {
  const spec = paramSpecs.value.find((p) => p.name === name)
  return spec && spec.label !== name ? `${spec.label}(${name})` : name
}

// 缺参提示文本按模式取形：表单模式只给 label（用户不接触键），
// JSON 模式附 (key)（键就是要敲进 JSON 的内容）
const missingRequiredText = computed(() =>
  missingRequired.value
    .map((name) => {
      const spec = paramSpecs.value.find((p) => p.name === name)
      if (!spec || spec.label === name) return name
      return formMode.value ? spec.label : `${spec.label}(${name})`
    })
    .join('、'),
)

// JSON 模式 placeholder：示例键取自所选流水线实际声明的参数，避免误导
const jsonPlaceholder = computed(() => {
  const keys = [...requiredInputs.value, ...Object.keys(defaultInputs.value)]
  if (keys.length) return `JSON 对象，键取自参数声明，如 {"${keys[0]}": ...}`
  return 'JSON 对象（该流水线未声明参数，一般无需填写）'
})

// 参数字段的 placeholder：只放「默认值预览」——留空的后果是唯一别处没有的信息；
// 必填/可选已在 label 行（*/可选）、说明在字段下方常驻，不再重复进 placeholder
function paramPlaceholder(spec: ParamSpec): string {
  if (!spec.has_default) return ''
  const text = JSON.stringify(spec.default) ?? ''
  return `默认: ${text.length > 32 ? `${text.slice(0, 32)}…` : text}`
}

function fillDefaults() {
  inputsText.value = defaultsJson.value
}

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
  if (submitInputs.value.error) {
    ElMessage.warning(submitInputs.value.error)
    return
  }
  if (missingRequired.value.length) {
    ElMessage.warning(`缺少必填参数: ${missingRequiredText.value}`)
    return
  }
  creating.value = true
  try {
    await store.startNewRun(configFile.value, submitInputs.value.value)
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

/** 删除终态 run：二次确认（删除不可恢复，含审批决策）。失败提示由
 * 请求拦截器统一弹出，这里只在成功时回礼。 */
async function removeRun(id: number) {
  const run = store.runs.find((r) => r.id === id)
  try {
    await ElMessageBox.confirm(
      `删除运行 #${id}${run ? `（${run.name}）` : ''}？记录与审批决策将一并删除，不可恢复。`,
      '删除运行记录',
      { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' },
    )
  } catch {
    return // 用户取消
  }
  await store.removeRun(id)
  ElMessage.success(`已删除运行 #${id}`)
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
      <!-- 参数入口按声明出现：流水线声明了参数（任意形式）才有「参数」可言，
           未声明的流水线不显示空选项（弹层内容只会是「无需填写」） -->
      <el-popover v-if="paramSpecs.length" placement="bottom-start" :width="380" trigger="click">
        <template #reference>
          <el-button plain :disabled="!pipelinesStore.pipelines.length">
            ⚙ 参数<template v-if="submitInputs.count"> · {{ submitInputs.count }}</template>
          </el-button>
        </template>
        <div class="inputs-pop">
          <div class="inputs-pop-head">
            <span class="inputs-pop-title">运行时输入参数</span>
            <el-button
              v-if="paramSpecs.length"
              size="small"
              text
              type="primary"
              @click="jsonMode = !jsonMode"
            >
              {{ jsonMode ? '表单模式' : 'JSON 模式' }}
            </el-button>
          </div>

          <!-- 表单模式：按流水线声明逐参数渲染，label/说明/默认值/必填直接可见 -->
          <template v-if="formMode">
            <div v-for="spec in paramSpecs" :key="spec.name" class="param-field">
              <div class="param-label">
                {{ spec.label }}<span v-if="spec.required" class="param-star">*</span>
                <span v-else class="param-optional">可选</span>
              </div>
              <el-input
                v-model="paramValues[spec.name]"
                :type="spec.multiline ? 'textarea' : 'text'"
                :autosize="spec.multiline ? { minRows: 3, maxRows: 8 } : undefined"
                size="small"
                spellcheck="false"
                :placeholder="paramPlaceholder(spec)"
              />
              <div v-if="spec.description" class="param-desc">{{ spec.description }}</div>
            </div>
            <div v-if="missingRequired.length" class="inputs-error">
              缺少必填参数: {{ missingRequiredText }}，「新建运行」将被拦截
            </div>
            <div v-else-if="submitInputs.count" class="inputs-hint">
              已填 {{ submitInputs.count }} 个参数，随「新建运行」提交；留空字段用默认值或不传。
            </div>
            <div v-else class="inputs-hint">留空字段用 YAML 默认值或不传；未填必填项将被拦截。</div>
          </template>

          <!-- JSON 模式：原始 JSON 文本（声明流水线的高级逃生口） -->
          <template v-else>
            <div v-if="requiredInputs.length" class="inputs-required">
              <span>必填：</span>
              <el-tag
                v-for="k in requiredInputs"
                :key="k"
                size="small"
                :type="missingRequired.includes(k) ? 'danger' : 'success'"
                disable-transitions
              >
                {{ keyLabel(k) }}{{ missingRequired.includes(k) ? ' *' : ' ✓' }}
              </el-tag>
            </div>
            <el-input
              v-model="inputsText"
              type="textarea"
              :rows="5"
              spellcheck="false"
              :placeholder="jsonPlaceholder"
            />
            <div v-if="parsedInputs.error" class="inputs-error">{{ parsedInputs.error }}</div>
            <div v-else-if="missingRequired.length" class="inputs-error">
              缺少必填参数: {{ missingRequiredText }}，「新建运行」将被拦截
            </div>
            <div v-else-if="parsedInputs.count" class="inputs-hint">
              已解析 {{ parsedInputs.count }} 个参数，随「新建运行」提交；同名键优先于 YAML 默认 inputs。
            </div>
            <div v-else class="inputs-hint">留空则只用流水线 YAML 的默认 inputs。</div>
            <template v-if="hasDefaults">
              <div class="inputs-defaults-head">
                <span class="inputs-defaults-title">默认参数（YAML，只读）</span>
                <el-button size="small" text type="primary" @click="fillDefaults">填入默认值</el-button>
              </div>
              <pre class="inputs-defaults">{{ defaultsJson }}</pre>
            </template>
          </template>
        </div>
      </el-popover>
      <el-button type="primary" :loading="creating" @click="startNewRun">▶ 新建运行</el-button>
    </header>
    <main class="layout">
      <RunList
        :runs="store.runs"
        :total="store.total"
        :selected-id="store.selectedId"
        :loading-more="store.loadingMore"
        :status="store.filters.status"
        :config-file="store.filters.configFile"
        :pipeline-options="pipelinesStore.pipelines"
        @set-status="store.setFilters({ status: $event })"
        @set-config="store.setFilters({ configFile: $event })"
        @select="select"
        @delete="removeRun"
        @more="store.loadMoreRuns()"
      />
      <RunDetail
        v-if="store.detail"
        :detail="store.detail"
        :deciding="store.deciding"
        :params="detailParams"
        @decide="decide"
        @view-config="viewRunConfig"
      />
      <div v-else class="muted">选择或创建一条运行查看执行状态。</div>
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
          <el-tag v-if="previewFrom.kind === 'run'" size="small" type="info">运行 #{{ previewFrom.runId }}</el-tag>
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
  padding: 10px 20px;
  border-bottom: 1px solid var(--line);
  display: flex;
  align-items: center;
  gap: 12px;
}
.page-head h1 {
  font-size: 18px;
  margin: 0;
}
/* 参数 popover：等宽字体输入与页面 mono 语言一致（详情 meta、文件名） */
.inputs-pop :deep(textarea) {
  font-family: var(--font-mono);
  font-size: 12px;
}
.inputs-pop-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 8px;
}
.inputs-pop-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--ink);
}
/* 表单模式单参数字段：label + 输入框 + 说明 */
.param-field {
  margin-bottom: 8px;
}
.param-label {
  display: flex;
  align-items: baseline;
  gap: 4px;
  font-size: 12px;
  font-weight: 600;
  color: var(--ink-2);
  margin-bottom: 3px;
}
.param-star {
  color: #ff8f8a;
}
.param-optional {
  font-weight: 400;
  font-size: 11px;
  color: var(--ink-3);
}
.param-desc {
  font-size: 12px;
  color: var(--ink-3);
  line-height: 1.5;
  margin-top: 2px;
}
.inputs-error {
  color: #ff8f8a;
  font-size: 12px;
  margin-top: 6px;
}
.inputs-hint {
  color: var(--ink-3);
  font-size: 12px;
  margin-top: 6px;
  line-height: 1.5;
}
/* 必填键标签行：缺失红色 *、已提供绿色 ✓ */
.inputs-required {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
  font-size: 12px;
  color: var(--ink-2);
  margin-bottom: 8px;
}
.inputs-defaults-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-top: 10px;
}
.inputs-defaults-title {
  font-size: 12px;
  color: var(--ink-3);
}
.inputs-defaults {
  margin: 6px 0 0;
  padding: 8px;
  max-height: 160px;
  overflow: auto;
  font-family: var(--font-mono);
  font-size: 12px;
  line-height: 1.5;
  color: var(--ink-2);
  background: rgba(16, 21, 42, 0.72);
  border: 1px solid var(--line);
  border-radius: 8px;
  white-space: pre-wrap;
  word-break: break-all;
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
  font-size: 15px;
}
.drawer-title .name {
  font-weight: 600;
  color: var(--ink);
}
.drawer-title .file {
  font-family: var(--font-mono);
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

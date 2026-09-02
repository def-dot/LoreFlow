<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import RunList from '@/components/RunList.vue'
import RunDetail from '@/components/RunDetail.vue'
import PipelineDetailPanel from '@/components/PipelineDetailPanel.vue'
import type { ParamSpec, PipelineDetail } from '@/api/pipelines'
import { toParamSpecs } from '@/api/pipelines'
import { uploadFile, type UploadOut } from '@/api/uploads'
import { useRunsStore } from '@/stores/runs'
import { usePipelinesStore } from '@/stores/pipelines'

const store = useRunsStore()
const pipelinesStore = usePipelinesStore()

// 新建 run 时运行的流水线（下拉来自 /pipelines，值为 YAML name）
const configName = ref('')
// 任务名称（可选，不传则用配置文件名）
const runName = ref('')
// 防止「新建运行」按钮重复点击导致并发创建
const creating = ref(false)
// 新建运行 drawer
const createDrawerOpen = ref(false)

// ---------------------------------------------------------------------------
// 运行时输入参数：流水线声明了 params 时按声明渲染表单（label/说明/默认值
// 预填/必填一目了然）；未声明或切「JSON 模式」时退回原始 JSON 文本。
// 空 → 只用 YAML 默认 inputs；无效 JSON → 拦截提交并提示。
// ---------------------------------------------------------------------------
const inputsText = ref('')
// 表单模式的字段值（字符串输入，提交时解析）
const paramValues = ref<Record<string, string>>({})
// file 参数的服务端引用（选文件即上传，提交时用 {id, filename}）与展示用文件名
const uploadRefs = ref<Record<string, UploadOut>>({})
const fileNames = ref<Record<string, string>>({})
// file 参数上传中（防重复选择 + 按钮态）
const uploading = ref<Record<string, boolean>>({})
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

// 所选流水线的参数声明（列表接口已带 params，无需再取详情）
const selectedPipeline = computed(() =>
  pipelinesStore.pipelines.find((p) => p.name === configName.value),
)
const paramSpecs = computed(() => toParamSpecs(selectedPipeline.value?.params ?? {}))
// run 详情「⚙ 参数」弹层的声明标签：按该 run 的 pipeline 取
// （与创建下拉的选中无关 —— 看旧 run 时下拉可能停在别的流水线上）
const detailParams = computed(() =>
  toParamSpecs(pipelinesStore.pipelines.find((p) => p.name === store.detail?.pipeline)?.params ?? {}),
)
// 必填键/默认值从参数行派生（声明行是单一事实源，后端不再单独输出）
const requiredInputs = computed(() => paramSpecs.value.filter((p) => p.required).map((p) => p.name))
const defaultInputs = computed(() =>
  Object.fromEntries(paramSpecs.value.filter((p) => p.default != null).map((p) => [p.name, p.default])),
)
const hasDefaults = computed(() => Object.keys(defaultInputs.value).length > 0)
const defaultsJson = computed(() => JSON.stringify(defaultInputs.value, null, 2))

// 切换流水线时重置参数状态（表单值 + JSON 文本）：不同流水线的参数集不同，
// 旧值没有意义 —— 参数按钮未声明时是隐藏的，残留 JSON 会静默随新建运行提交。
// 监听「解析到的文件名」而非 configFile：列表异步加载使 configFile 首次命中
// 流水线时也会触发；对象级刷新不改变文件名字符串，不会误清用户已填的表单。
// 表单值重置为默认值预填（可改可清空）—— 所见即所传，留空只剩「无默认值
// 且未填」一种含义；multiline 的非字符串默认值与 null 默认值不预填
// （前者提交时不解析 JSON 会把值变字符串，后者预填文本 "null" 同样变味，
// 留空 = 不传 = 后端按声明默认执行）。
watch(
  () => selectedPipeline.value?.name ?? '',
  () => {
    paramValues.value = Object.fromEntries(
      paramSpecs.value
        .filter((s) => s.default != null && (typeof s.default === 'string' || !s.multiline))
        .map((s) => [s.name, defaultToText(s.default)]),
    )
    fileNames.value = {}
    uploadRefs.value = {}
    inputsText.value = ''
  },
)

// 默认值 → 预填文本：字符串原样，数字/布尔/JSON 值序列化后提交时由
// parseFieldValue 解析回原类型（往返一致）
function defaultToText(v: unknown): string {
  return typeof v === 'string' ? v : (JSON.stringify(v) ?? '')
}

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
    // file 字段的值是上传接口返回的 {id, filename} 引用（rag_load 按 id 读盘）；
    // 未选文件 = 不传该参数（用默认值或不传）
    if (spec.file) {
      const ref = uploadRefs.value[spec.name]
      if (!ref) continue
      value[spec.name] = { id: ref.id, filename: ref.filename }
      continue
    }
    const raw = (paramValues.value[spec.name] ?? '').trim()
    if (raw === '') continue
    value[spec.name] = parseFieldValue(raw, spec.multiline)
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
  if (spec.default == null) return ''
  const text = JSON.stringify(spec.default) ?? ''
  return `默认: ${text.length > 32 ? `${text.slice(0, 32)}…` : text}`
}

function fillDefaults() {
  inputsText.value = defaultsJson.value
}

// file 参数选择文件：上传到服务端换 {id, filename} 引用（rag_load 按 id 读盘），
// 不再客户端读正文；上传失败由拦截器提示，保持「未选择」状态
async function onFilePicked(spec: ParamSpec, event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  input.value = '' // 清掉选择，允许再次选同一文件
  if (!file) return
  uploading.value[spec.name] = true
  try {
    const ref = await uploadFile(file)
    uploadRefs.value[spec.name] = ref
    fileNames.value[spec.name] = ref.filename
  } catch {
    // 错误已由拦截器提示；保持未选择状态
  } finally {
    uploading.value[spec.name] = false
  }
}

// 移除已选文件：回到「未选择」= 该参数不随新建运行提交（用默认值或不传）
function clearFile(spec: ParamSpec) {
  delete uploadRefs.value[spec.name]
  delete fileNames.value[spec.name]
}

// ---------------------------------------------------------------------------
// 流水线详情 drawer：顶部「预览」看所选流水线，run 详情里的「查看配置」
// 看该 run 跑的配置 —— 同一个 drawer，标题区分来源。详情按文件名缓存
// 并后台重取（SWR）：重复打开秒显，改过 YAML 也会自动跟上，无需刷新按钮
// ---------------------------------------------------------------------------
const previewOpen = ref(false)
const previewLoading = ref(false)
const previewError = ref<string | null>(null)
const previewName = ref<string | null>(null)

// 标题里的流水线中文名：详情未加载时从列表兜底，打开即可见
const previewItem = computed(() =>
  pipelinesStore.pipelines.find((p) => p.name === previewName.value),
)

// 抽屉实际渲染的详情：走 store 缓存
const previewDetail = computed<PipelineDetail | null>(() =>
  previewName.value ? (pipelinesStore.detailCache[previewName.value] ?? null) : null,
)

async function loadPreview() {
  if (!previewName.value) return
  previewError.value = null
  // 无缓存才转圈；有缓存先秒显旧内容，后台重取静默更新
  previewLoading.value = !pipelinesStore.detailCache[previewName.value]
  try {
    await pipelinesStore.select(previewName.value)
  } catch {
    if (!previewDetail.value) {
      previewError.value = '加载流水线失败，请检查后端是否可用。'
    }
  } finally {
    previewLoading.value = false
  }
}

async function openPreview(name: string) {
  previewName.value = name
  previewOpen.value = true
  await loadPreview()
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
    await store.startNewRun(configName.value, submitInputs.value.value, runName.value || undefined)
    runName.value = '' // 创建成功后清空
    createDrawerOpen.value = false
    startDetailPolling()
    startRunsPolling()
  } finally {
    creating.value = false
  }
}

// drawer 里的新建运行按钮调用
const handleCreateRun = startNewRun

async function decide(node: string, ok: boolean, reason: string | null, values: Record<string, string> | null) {
  await store.decide(node, ok, reason, values)
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

/** 取消运行中或待审核的 run：立即标记为 CANCELLED，后台 pipeline 会检测并退出。
 * 失败提示由请求拦截器统一弹出。 */
async function cancelRun(id: number) {
  await store.cancelRun(id)
  // 取消成功后立即刷新详情（如果选中就是当前 run）和列表，状态变更为 CANCELLED
  if (store.selectedId === id) {
    await store.fetchDetail()
  }
  await store.fetchRuns()
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
      if (!pipelinesStore.pipelines.some((p) => p.name === configName.value)) {
        configName.value = pipelinesStore.pipelines[0]?.name ?? configName.value
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
      <div class="head-info">
        <h1>运行</h1>
        <span class="muted">查看和管理流水线运行</span>
      </div>
      <el-button type="primary" @click="createDrawerOpen = true">▶ 新建运行</el-button>
    </header>
    <main class="layout">
      <RunList
        :runs="store.runs"
        :total="store.total"
        :selected-id="store.selectedId"
        :loading-more="store.loadingMore"
        :status="store.filters.status"
        :pipeline="store.filters.pipeline"
        @set-status="store.setFilters({ status: $event })"
        @set-pipeline="store.setFilters({ pipeline: $event })"
        @select="select"
        @delete="removeRun"
        @cancel="cancelRun"
        @more="store.loadMoreRuns()"
      />
      <RunDetail
        v-if="store.detail"
        :detail="store.detail"
        :deciding="store.deciding"
        :params="detailParams"
        @decide="decide"
      />
      <div v-else class="muted">选择或创建一条运行查看执行状态。</div>
    </main>
    <el-drawer v-model="previewOpen" size="min(920px, 94vw)">
      <template #title>
        <div class="drawer-title">
          <span class="name">{{ previewDetail?.name ?? previewItem?.name ?? '流水线详情' }}</span>
          <span class="muted file">
            {{ previewName }}
            <template v-if="previewDetail?.node_count ?? previewItem?.node_count">
              · {{ previewDetail?.node_count ?? previewItem?.node_count }} 节点
            </template>
          </span>
        </div>
      </template>
      <div v-loading="previewLoading" class="drawer-body">
        <div v-if="previewError" class="preview-error">
          <span class="muted">{{ previewError }}</span>
          <el-button size="small" plain @click="loadPreview">重试</el-button>
        </div>
        <PipelineDetailPanel v-else-if="previewDetail" :detail="previewDetail" />
      </div>
    </el-drawer>

    <!-- 新建运行 drawer -->
    <el-drawer v-model="createDrawerOpen" title="新建运行" size="min(480px, 90vw)">
      <div class="create-form">
        <div class="create-field">
          <label class="create-label">流水线</label>
          <div class="create-select-group">
            <el-select
              v-model="configName"
              :disabled="!pipelinesStore.pipelines.length"
              placeholder="选择流水线"
              style="flex: 1"
            >
              <el-option
                v-for="p in pipelinesStore.pipelines"
                :key="p.name"
                :value="p.name"
                :label="p.name"
              />
            </el-select>
            <el-button plain :disabled="!pipelinesStore.pipelines.length" @click="openPreview(configName)">
              👁 预览
            </el-button>
          </div>
        </div>
        <div class="create-field">
          <label class="create-label">任务名称</label>
          <el-input
            v-model="runName"
            placeholder="可选，不填则用工作流名称"
            clearable
          />
        </div>
        <div v-if="paramSpecs.length" class="create-field">
          <label class="create-label">运行时参数</label>
          <div class="inputs-pop">
            <div class="inputs-pop-head">
              <span class="inputs-pop-title">参数配置</span>
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

            <!-- 表单模式 -->
            <template v-if="formMode">
              <div v-for="spec in paramSpecs" :key="spec.name" class="param-field">
                <div class="param-label">
                  {{ spec.label }}<span v-if="spec.required" class="param-star">*</span>
                  <span v-else class="param-optional">可选</span>
                </div>
                <div v-if="spec.file" class="param-file">
                  <label
                    class="param-file-button"
                    :class="{ disabled: uploading[spec.name] }"
                  >
                    {{ uploading[spec.name] ? '上传中…' : '选择文件' }}
                    <input
                      type="file"
                      accept=".txt,.md,.markdown,text/plain"
                      :disabled="uploading[spec.name]"
                      @change="onFilePicked(spec, $event)"
                    />
                  </label>
                  <template v-if="fileNames[spec.name]">
                    <span class="param-file-name" :title="fileNames[spec.name]">
                      {{ fileNames[spec.name] }}
                    </span>
                    <button class="param-file-clear" title="移除文件" @click="clearFile(spec)">✕</button>
                  </template>
                  <span v-else class="param-file-empty">{{ uploading[spec.name] ? '上传中，请稍候…' : '未选择文件' }}</span>
                </div>
                <el-input
                  v-else
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
                缺少必填参数: {{ missingRequiredText }}
              </div>
              <div v-else-if="submitInputs.count" class="inputs-hint">
                已填 {{ submitInputs.count }} 个参数
              </div>
              <div v-else class="inputs-hint">留空字段用默认值或不传</div>
            </template>

            <!-- JSON 模式 -->
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
                缺少必填参数: {{ missingRequiredText }}
              </div>
              <div v-else-if="parsedInputs.count" class="inputs-hint">
                已解析 {{ parsedInputs.count }} 个参数
              </div>
              <div v-else class="inputs-hint">留空则只用 YAML 默认 inputs</div>
              <template v-if="hasDefaults">
                <div class="inputs-defaults-head">
                  <span class="inputs-defaults-title">默认参数（YAML）</span>
                  <el-button size="small" text type="primary" @click="fillDefaults">填入默认值</el-button>
                </div>
                <pre class="inputs-defaults">{{ defaultsJson }}</pre>
              </template>
            </template>
          </div>
        </div>
        <div class="create-actions">
          <el-button @click="createDrawerOpen = false">取消</el-button>
          <el-button type="primary" :loading="creating" @click="handleCreateRun">▶ 新建运行</el-button>
        </div>
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
  justify-content: space-between;
}
.head-info {
  display: flex;
  align-items: baseline;
  gap: 12px;
  flex-wrap: wrap;
}
.page-head h1 {
  font-size: 18px;
  margin: 0;
}
/* 新建运行 drawer 表单 */
.create-form {
  display: flex;
  flex-direction: column;
  gap: 16px;
}
.create-field {
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.create-label {
  font-size: 12px;
  font-weight: 600;
  color: var(--ink-3);
}
.create-select-group {
  display: flex;
  align-items: center;
  gap: 4px;
}
.create-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  padding-top: 8px;
  border-top: 1px solid var(--line);
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
/* file 参数：选择文件按钮 + 文件名（超长省略）+ 移除 */
.param-file {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}
.param-file-button {
  flex-shrink: 0;
  font-size: 12px;
  line-height: 1;
  padding: 7px 12px;
  border: 1px solid var(--line);
  border-radius: 6px;
  color: var(--ink-2);
  cursor: pointer;
  user-select: none;
}
.param-file-button:hover {
  border-color: var(--ink-3);
  color: var(--ink);
}
.param-file-button.disabled {
  opacity: 0.6;
  cursor: not-allowed;
}
.param-file-button input {
  display: none;
}
.param-file-name {
  font-family: var(--font-mono);
  font-size: 12px;
  color: var(--ink-2);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.param-file-clear {
  flex-shrink: 0;
  border: none;
  background: none;
  color: var(--ink-3);
  font-size: 12px;
  line-height: 1;
  padding: 4px;
  cursor: pointer;
}
.param-file-clear:hover {
  color: #ff8f8a;
}
.param-file-empty {
  font-size: 12px;
  color: var(--ink-3);
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

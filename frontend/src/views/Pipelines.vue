<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import PipelineDetailPanel from '@/components/PipelineDetailPanel.vue'
import YamlEditor from '@/components/YamlEditor.vue'
import { createPipeline, deletePipeline, updatePipeline } from '@/api/pipelines'
import { usePipelinesStore } from '@/stores/pipelines'

const store = usePipelinesStore()

const loading = ref(false)
const loadError = ref(false)

// 编写指南 drawer
const guideOpen = ref(false)

// 搜索（后端筛选）
const filterText = ref('')
let searchTimer: ReturnType<typeof setTimeout> | null = null
function onSearch() {
  if (searchTimer) clearTimeout(searchTimer)
  searchTimer = setTimeout(() => fetchAll(filterText.value.trim()), 300)
}

// 新建/编辑 drawer
const editorOpen = ref(false)
const editingId = ref<number | null>(null)
const editorDefinition = ref('')
const saving = ref(false)
const saveError = ref<string | null>(null)

async function fetchAll(q?: string) {
  loading.value = true
  loadError.value = false
  try {
    await store.fetchPipelines(q)
  } catch {
    loadError.value = true
  } finally {
    loading.value = false
  }
}

// 选中流水线并加载详情
const detailLoading = ref(false)
const detailError = ref<string | null>(null)

async function selectPipeline(id: number) {
  detailError.value = null
  detailLoading.value = true
  try {
    await store.select(id)
  } catch {
    detailError.value = '加载失败'
  } finally {
    detailLoading.value = false
  }
}

// 打开新建 drawer
function openCreate() {
  editingId.value = null
  editorDefinition.value = defaultYaml
  saveError.value = null
  editorOpen.value = true
}

// 打开编辑 drawer
async function openEdit(id?: number) {
  const targetId = id ?? store.selectedId
  if (targetId == null) return
  if (!store.detail || store.selectedId !== targetId) {
    await selectPipeline(targetId)
  }
  if (!store.detail) return
  editingId.value = targetId
  editorDefinition.value = store.detail.source
  saveError.value = null
  editorOpen.value = true
}

// 保存（新建或更新）
async function handleSave() {
  if (!editorDefinition.value.trim()) {
    saveError.value = 'YAML 定义不能为空'
    return
  }
  saving.value = true
  saveError.value = null
  try {
    if (editingId.value != null) {
      await updatePipeline(editingId.value, { definition: editorDefinition.value })
      await fetchAll()
      ElMessage.success('工作流已更新')
      await selectPipeline(editingId.value)
    } else {
      const { id } = await createPipeline({ definition: editorDefinition.value })
      ElMessage.success('工作流已创建')
      await fetchAll()
      await selectPipeline(id)
    }
    editorOpen.value = false
  } catch (err: unknown) {
    const msg = (err as { response?: { data?: { msg?: string } } })?.response?.data?.msg
    saveError.value = msg || '保存失败，请检查 YAML 定义'
  } finally {
    saving.value = false
  }
}

// 删除
async function handleDelete(id?: number) {
  const targetId = id ?? store.selectedId
  if (targetId == null) return
  const item = store.pipelines.find((p) => p.id === targetId)
  try {
    await ElMessageBox.confirm(
      `删除工作流「${item?.name ?? targetId}」？删除后不可恢复。`,
      '删除工作流',
      { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  await deletePipeline(targetId)
  ElMessage.success(`已删除工作流「${item?.name ?? targetId}」`)
  if (store.selectedId === targetId) {
    store.selectedId = null
    store.selectedName = ''
    store.detail = null
  }
  await fetchAll()
}

const defaultYaml = `name: 我的工作流
description: 一句话描述

params:
  prompt:
    required: true
    label: 提示词
    description: 用户输入的问题

nodes:
- name: reply
  type: llm_chat
  label: LLM 回复
  description: 调用 LLM 直接回答
  inputs:
    prompt: $input.prompt
  timeout: 300
- name: end
  type: end
  label: 最终输出
  depends_on:
  - reply
  inputs:
    reply: $reply.content
`

onMounted(async () => {
  await fetchAll()
  if (store.pipelines.length) {
    await selectPipeline(store.pipelines[0].id)
  }
})
</script>

<template>
  <div class="page">
    <header class="page-head">
      <div class="head-info">
        <h1>工作流</h1>
        <span class="muted">管理预置和自定义的 YAML 工作流定义</span>
      </div>
      <span v-if="loadError" class="load-error">加载失败，请检查后端是否可用</span>
      <el-button plain :loading="loading" @click="fetchAll">↻ 刷新</el-button>
      <el-button type="primary" @click="openCreate">＋ 新建</el-button>
      <span class="guide-link" @click="guideOpen = true">编写指南</span>
    </header>

    <main class="layout">
      <!-- 左侧：流水线列表 -->
      <div class="sidebar">
        <div class="sidebar-filter">
          <el-input v-model="filterText" placeholder="搜索工作流…" clearable size="small" @input="onSearch" @clear="fetchAll()" />
        </div>
        <div
          v-for="p in store.pipelines"
          :key="p.id"
          class="sidebar-item"
          :class="{ active: p.id === store.selectedId }"
          @click="selectPipeline(p.id)"
        >
          <div class="sidebar-main">
            <span class="sidebar-name">{{ p.name }}</span>
            <span v-if="p.description" class="sidebar-desc">{{ p.description }}</span>
          </div>
          <div class="sidebar-actions" @click.stop>
            <el-button size="small" text @click="openEdit(p.id)">✎</el-button>
            <el-button size="small" text type="danger" @click="handleDelete(p.id)">✕</el-button>
          </div>
        </div>
        <div v-if="!store.pipelines.length && !loading" class="sidebar-empty muted">
          暂无工作流
        </div>
      </div>

      <!-- 右侧：详情 -->
      <div class="detail">
        <template v-if="store.detail">
          <div class="detail-body">
            <PipelineDetailPanel :key="store.detail.name" :detail="store.detail" />
          </div>
        </template>
        <div v-else-if="detailLoading" v-loading="true" class="detail-empty" />
        <div v-else-if="detailError" class="detail-empty">
          <span class="muted">{{ detailError }}</span>
        </div>
        <div v-else class="detail-empty">
          <span class="muted">选择一条工作流查看详情</span>
        </div>
      </div>
    </main>

    <!-- 新建/编辑 drawer -->
    <el-drawer v-model="editorOpen" :title="editingId != null ? '编辑工作流' : '新建工作流'" size="min(720px, 94vw)" class="editor-drawer">
      <div class="editor-form">
        <div class="editor-field editor-field-grow">
          <label class="editor-label">YAML 定义</label>
          <YamlEditor
            v-model="editorDefinition"
            placeholder="粘贴或编写 YAML 工作流定义"
            class="yaml-editor"
          />
        </div>
        <div v-if="saveError" class="editor-error">{{ saveError }}</div>
        <div class="editor-actions">
          <el-button @click="editorOpen = false">取消</el-button>
          <el-button type="primary" :loading="saving" @click="handleSave">
            {{ editingId != null ? '保存修改' : '创建' }}
          </el-button>
        </div>
      </div>
    </el-drawer>

    <!-- 编写指南 drawer -->
    <el-drawer v-model="guideOpen" title="工作流编写指南" size="min(720px, 94vw)">
      <div class="guide">
        <p class="guide-yaml-note">工作流使用 <strong>YAML</strong> 格式编写，以下为各字段说明。</p>

        <h4>基本结构</h4>
        <pre class="guide-code">name: 我的工作流
description: 一句话描述

params:
  prompt:
    required: true
    label: 提示词
    description: 用户输入的问题

nodes:
- name: reply
  type: llm_chat
  label: LLM 回复
  inputs:
    prompt: $input.prompt
  timeout: 300
- name: end
  type: end
  label: 最终输出
  depends_on:
  - reply
  inputs:
    reply: $reply.content</pre>

        <h4>输入参数 (params)</h4>
        <p class="guide-desc">params 定义工作流的全局输入参数，支持文本、文件上传、下拉选择等类型。运行时由用户填写，任意节点通过 <code>$input.xxx</code> 引用填写的参数值。</p>
        <pre class="guide-code">params:
  prompt:                      # 参数名（必填），即 $input.prompt
    required: true             # [可选] 是否必填，默认 false
    label: 提示词               # [可选] 表单显示名称
    description: 用户输入的问题  # [可选] 表单提示说明
    type: text                 # [可选] 参数类型，默认 text，见下表
    default: 你好               # [可选] 默认值
    options:                   # [可选] type=select 时的可选值
      - 选项1
      - 选项2</pre>
        <span class="type-label">参数类型</span>
        <div class="guide-cond-grid">
          <span class="cond-item"><code>text</code> 单行文本</span>
          <span class="cond-item"><code>paragraph</code> 多行文本</span>
          <span class="cond-item"><code>number</code> 数字</span>
          <span class="cond-item"><code>select</code> 下拉选项</span>
          <span class="cond-item"><code>checkbox</code> 复选框</span>
          <span class="cond-item"><code>file</code> 单文件上传</span>
          <span class="cond-item"><code>file_list</code> 多文件上传</span>
        </div>

        <h4>节点 (nodes)</h4>
        <p class="guide-desc">nodes 定义工作流中的执行步骤，节点按 depends_on 构成 DAG 顺序执行。</p>
        <pre class="guide-code">nodes:
- name: reply               # 节点名（必填），唯一标识
  type: llm_chat             # 节点类型（必填），须从可用类型中选择
  label: LLM 回复             # [可选] 显示名称
  description: 调用LLM回答    # [可选] 节点说明
  depends_on: node_a         # [可选] 依赖上游节点，可写单个字符串或数组
  # depends_on:              # 等价写法
  #   - node_a
  #   - node_b
  inputs:                    # [可选] 定义 type 函数的输入参数，key 须匹配 type 的输入 schema，
                         # 值通过 $ 引用全局参数或上游节点输出，也可以是字符串面量
    prompt: $input.prompt
    system: 你是一个助手
  condition: $classify.intent == chat   # [可选] 布尔值或条件表达式，为 false 时跳过，
                                     # 支持 $ 引用全局参数或上游节点输出，表达式示例见下表
  retry: 3                   # [可选] 简写，固定间隔重试最多 3 次
  # retry:                   # 完整写法，定义退避策略
  #   max_retries: 3         # 最大重试次数
  #   backoff_base: 1.0      # 初始等待秒数
  #   backoff_factor: 2.0    # 每次等待倍数
  #   backoff_max: 60.0      # 最大等待秒数
  #   jitter: true           # 是否随机抖动
  #   retry_on:              # 触发重试的异常类型，默认所有异常
  #     - ValueError
  #     - TimeoutError
  timeout: 300               # [可选] 超时秒数</pre>
        <span class="type-label">condition 表达式示例</span>
        <div class="guide-cond-grid">
          <span class="cond-item">等值 <code>$intent == chat</code></span>
          <span class="cond-item">不等 <code>$intent != chat</code></span>
          <span class="cond-item">比较 <code>$score >= 0.8</code></span>
          <span class="cond-item">成员 <code>$intent in chat,rag</code></span>
          <span class="cond-item">取反 <code>not $flag</code></span>
          <span class="cond-item">或 <code>$a == x or $b == y</code></span>
          <span class="cond-item">且 <code>$a and $b</code></span>
        </div>
        <span class="type-label">常用节点类型</span>
        <div class="guide-cond-grid">
          <span class="cond-item"><code>agent</code> 智能体</span>
          <span class="cond-item"><code>llm_chat</code> LLM 对话</span>
          <span class="cond-item"><code>llm_classify</code> 意图识别</span>
          <span class="cond-item"><code>read_document</code> 读取文档</span>
          <span class="cond-item"><code>rag_retrieve</code> 知识库检索</span>
          <span class="cond-item"><code>code</code> 代码执行</span>
          <span class="cond-item"><code>human</code> 人工审核</span>
          <span class="cond-item"><code>end</code> 最终输出</span>
        </div>
        <p class="guide-link-hint">
          完整列表及详细参数见 <router-link to="/plugins" class="guide-jump">节点类型 →</router-link>
        </p>

        <h4>数据引用语法</h4>
        <ul class="guide-rules">
          <li><code>$input.xxx</code> — 引用用户填写的输入参数</li>
          <li><code>$节点名.字段</code> — 引用上游节点的输出，如 <code>$reply.content</code></li>
        </ul>

        <h4>规则</h4>
        <ul class="guide-rules">
          <li>节点 <code>name</code> 唯一，用作引用标识</li>
          <li><code>depends_on</code> 中的名字必须对应已定义的节点</li>
          <li><code>inputs</code> 中的 <code>$</code> 引用必须指向输入参数或上游节点</li>
          <li><code>end</code> 节点定义工作流的最终输出</li>
          <li>DAG 不能有环（依赖必须是有向无环图）</li>
        </ul>
      </div>
    </el-drawer>
  </div>
</template>

<style scoped>
.page {
  display: flex;
  flex-direction: column;
  height: 100vh;
  overflow: hidden;
}
.page-head {
  padding: 10px 20px;
  border-bottom: 1px solid var(--line);
  display: flex;
  align-items: center;
  gap: 14px;
}
.head-info {
  flex: 1;
  min-width: 0;
  display: flex;
  align-items: baseline;
  gap: 12px;
  flex-wrap: wrap;
}
.page-head h1 {
  font-size: 18px;
  margin: 0;
}
.load-error {
  color: #f87171;
  font-size: 12px;
}
/* 编写指南链接 */
.guide-link {
  font-size: 13px;
  color: var(--ink-3);
  cursor: pointer;
  user-select: none;
  transition: color 0.2s;
}
.guide-link:hover {
  color: var(--accent);
}
/* 主布局：左侧列表 + 右侧详情 */
.layout {
  display: grid;
  grid-template-columns: 260px 1fr;
  gap: 0;
  flex: 1;
  min-height: 0;
}
@media (max-width: 900px) {
  .layout {
    grid-template-columns: 1fr;
  }
}
/* 左侧列表 */
.sidebar {
  border-right: 1px solid var(--line);
  overflow-y: auto;
  display: flex;
  flex-direction: column;
}
.sidebar-filter {
  padding: 10px 12px;
  border-bottom: 1px solid var(--line);
  flex-shrink: 0;
}
.sidebar-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 12px;
  cursor: pointer;
  border-bottom: 1px solid var(--line);
  transition: background 0.15s;
}
.sidebar-item:hover {
  background: rgba(255, 255, 255, 0.03);
}
.sidebar-item.active {
  background: rgba(77, 196, 178, 0.08);
  border-left: 3px solid var(--accent);
  padding-left: 9px;
}
.sidebar-main {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.sidebar-name {
  font-size: 13px;
  font-weight: 500;
  color: var(--ink);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.sidebar-desc {
  font-size: 11px;
  color: var(--ink-3);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.sidebar-actions {
  display: flex;
  gap: 0;
  flex-shrink: 0;
  opacity: 0;
  transition: opacity 0.15s;
}
.sidebar-item:hover .sidebar-actions {
  opacity: 1;
}
.sidebar-meta {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 11px;
}
.sidebar-empty {
  padding: 20px 16px;
  text-align: center;
  font-size: 13px;
}
/* 右侧详情 */
.detail {
  overflow-y: auto;
}
.detail-body {
  padding: 16px 20px;
}
.detail-empty {
  display: flex;
  align-items: center;
  justify-content: center;
  height: 100%;
  min-height: 200px;
}
/* drawer body needs to fill height for flex children */
.editor-drawer :deep(.el-drawer__body) {
  display: flex;
  flex-direction: column;
  overflow: hidden;
}
/* 编辑 drawer */
.editor-form {
  display: flex;
  flex-direction: column;
  gap: 14px;
  height: 100%;
}
.editor-field {
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.editor-field-grow {
  flex: 1;
  min-height: 0;
}
.editor-label {
  font-size: 12px;
  font-weight: 600;
  color: var(--ink-3);
}
.yaml-editor {
  flex: 1;
  min-height: 0;
}
.editor-error {
  color: #ff8f8a;
  font-size: 12px;
  white-space: pre-wrap;
  max-height: 120px;
  overflow: auto;
}
.editor-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  padding-top: 8px;
  border-top: 1px solid var(--line);
}
/* 编写指南 */
.guide h4 {
  font-size: 14px;
  font-weight: 600;
  color: var(--ink);
  margin: 20px 0 10px;
}
.guide h4:first-child {
  margin-top: 0;
}
.guide-yaml-note {
  font-size: 13px;
  color: var(--ink-3);
  margin: 0 0 6px;
}
.guide-desc {
  font-size: 13px;
  color: var(--ink-2);
  margin: 0 0 8px;
  line-height: 1.6;
}
.guide-desc code {
  font-family: var(--font-mono);
  font-size: 11px;
  background: rgba(77, 196, 178, 0.12);
  padding: 1px 4px;
  border-radius: 3px;
  color: var(--accent);
}
.type-label {
  display: block;
  font-size: 12px;
  font-weight: 600;
  color: var(--ink-2);
  margin-bottom: 6px;
}
.guide-cond-grid {
  display: flex;
  flex-wrap: wrap;
  gap: 6px 16px;
  font-size: 12px;
  color: var(--ink-3);
}
.cond-item code {
  font-family: var(--font-mono);
  font-size: 11px;
  color: var(--accent);
  margin-left: 4px;
}
.guide-link-hint {
  margin: 8px 0 0;
  font-size: 12px;
  color: var(--ink-3);
}
.guide-jump {
  color: #5b9dff;
  text-decoration: none;
  border-bottom: 1px dashed #5b9dff;
  padding-bottom: 1px;
}
.guide-jump:hover {
  color: #7db5ff;
  border-bottom-style: solid;
}
.guide-code {
  font-family: var(--font-mono);
  font-size: 12px;
  line-height: 1.6;
  color: var(--ink-2);
  background: rgba(0, 0, 0, 0.2);
  padding: 12px;
  border-radius: 6px;
  overflow-x: auto;
  white-space: pre;
  margin: 0;
}
.guide-rules {
  margin: 0;
  padding: 0 0 0 20px;
  font-size: 13px;
  line-height: 1.8;
  color: var(--ink-2);
}
.guide-rules li {
  margin-bottom: 4px;
}
.guide-rules code {
  font-family: var(--font-mono);
  font-size: 11px;
  background: rgba(77, 196, 178, 0.15);
  padding: 2px 5px;
  border-radius: 3px;
  color: var(--accent);
}
</style>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import PipelineDetailPanel from '@/components/PipelineDetailPanel.vue'
import { createPipeline, deletePipeline, updatePipeline } from '@/api/pipelines'
import { usePipelinesStore } from '@/stores/pipelines'

const store = usePipelinesStore()

const loading = ref(false)
const loadError = ref(false)

// 编写指南 drawer
const guideOpen = ref(false)

// 筛选
const filterText = ref('')
const filteredPipelines = computed(() => {
  const q = filterText.value.trim().toLowerCase()
  if (!q) return store.pipelines
  return store.pipelines.filter(
    (p) =>
      p.name.toLowerCase().includes(q)
      || p.description.toLowerCase().includes(q),
  )
})

// 新建/编辑 drawer
const editorOpen = ref(false)
const editingName = ref<string | null>(null)
const editorDefinition = ref('')
const saving = ref(false)
const saveError = ref<string | null>(null)

async function fetchAll() {
  loading.value = true
  loadError.value = false
  try {
    await store.fetchPipelines()
  } catch {
    loadError.value = true
  } finally {
    loading.value = false
  }
}

// 选中流水线并加载详情
const detailLoading = ref(false)
const detailError = ref<string | null>(null)

async function selectPipeline(name: string) {
  detailError.value = null
  detailLoading.value = true
  try {
    await store.select(name)
  } catch {
    detailError.value = '加载失败'
  } finally {
    detailLoading.value = false
  }
}

const selectedItem = computed(() =>
  store.pipelines.find((p) => p.name === store.selectedName),
)

// 打开新建 drawer
function openCreate() {
  editingName.value = null
  editorDefinition.value = defaultYaml
  saveError.value = null
  editorOpen.value = true
}

// 打开编辑 drawer
async function openEdit() {
  if (!store.selectedName || !store.detail) return
  editingName.value = store.selectedName
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
    if (editingName.value) {
      const { name: newName } = await updatePipeline(editingName.value, { definition: editorDefinition.value })
      await fetchAll()
      ElMessage.success('流水线已更新')
      await selectPipeline(newName)
    } else {
      const { name } = await createPipeline({ definition: editorDefinition.value })
      ElMessage.success(`流水线已创建: ${name}`)
      await fetchAll()
      await selectPipeline(name)
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
async function handleDelete() {
  if (!store.selectedName || !selectedItem.value) return
  try {
    await ElMessageBox.confirm(
      `删除流水线「${selectedItem.value.name}」？删除后不可恢复。`,
      '删除流水线',
      { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  await deletePipeline(store.selectedName)
  ElMessage.success(`已删除流水线「${selectedItem.value.name}」`)
  store.selectedName = ''
  store.detail = null
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
    await selectPipeline(store.pipelines[0].name)
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
          <el-input v-model="filterText" placeholder="搜索工作流…" clearable size="small" />
        </div>
        <div
          v-for="p in filteredPipelines"
          :key="p.name"
          class="sidebar-item"
          :class="{ active: p.name === store.selectedName }"
          @click="selectPipeline(p.name)"
        >
          <span class="sidebar-name">{{ p.name }}</span>
        </div>
        <div v-if="!filteredPipelines.length && !loading" class="sidebar-empty muted">
          暂无工作流
        </div>
      </div>

      <!-- 右侧：详情 -->
      <div class="detail">
        <template v-if="store.detail">
          <div class="detail-head">
            <div class="detail-title">
              <h2>{{ store.detail.name }}</h2>
            </div>
            <div class="detail-actions">
              <el-button size="small" plain @click="openEdit()">
                ✎ 编辑
              </el-button>
              <el-button size="small" plain type="danger" @click="handleDelete()">
                ✕ 删除
              </el-button>
            </div>
          </div>
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
    <el-drawer v-model="editorOpen" :title="editingName ? '编辑工作流' : '新建工作流'" size="min(720px, 94vw)">
      <div class="editor-form">
        <div class="editor-field editor-field-grow">
          <label class="editor-label">YAML 定义</label>
          <el-input
            v-model="editorDefinition"
            type="textarea"
            :autosize="{ minRows: 16, maxRows: 40 }"
            spellcheck="false"
            placeholder="粘贴或编写 YAML 工作流定义"
            class="yaml-editor"
          />
        </div>
        <div v-if="saveError" class="editor-error">{{ saveError }}</div>
        <div class="editor-actions">
          <el-button @click="editorOpen = false">取消</el-button>
          <el-button type="primary" :loading="saving" @click="handleSave">
            {{ editingName ? '保存修改' : '创建' }}
          </el-button>
        </div>
      </div>
    </el-drawer>

    <!-- 编写指南 drawer -->
    <el-drawer v-model="guideOpen" title="工作流编写指南" size="min(720px, 94vw)">
      <div class="guide">
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

        <h4>参数类型 (params.type)</h4>
        <table class="guide-table">
          <thead><tr><th>类型</th><th>说明</th><th>额外字段</th></tr></thead>
          <tbody>
            <tr><td><code>text</code></td><td>单行文本</td><td>—</td></tr>
            <tr><td><code>paragraph</code></td><td>多行文本</td><td>—</td></tr>
            <tr><td><code>number</code></td><td>数字</td><td>—</td></tr>
            <tr><td><code>select</code></td><td>下拉选项</td><td><code>options: ["选项1", "选项2"]</code></td></tr>
            <tr><td><code>checkbox</code></td><td>复选框</td><td>—</td></tr>
            <tr><td><code>file</code></td><td>单文件上传</td><td>—</td></tr>
            <tr><td><code>file_list</code></td><td>多文件上传</td><td>—</td></tr>
          </tbody>
        </table>

        <h4>常用节点类型 (nodes.type)</h4>
        <table class="guide-table">
          <thead><tr><th>type</th><th>说明</th><th>输入示例</th></tr></thead>
          <tbody>
            <tr><td><code>llm_chat</code></td><td>LLM 对话</td><td><code>prompt</code>, <code>system</code>, <code>context</code></td></tr>
            <tr><td><code>llm_classify</code></td><td>意图识别</td><td><code>prompt</code>, <code>classify_labels</code></td></tr>
            <tr><td><code>rag_load</code></td><td>加载文档</td><td><code>document: {id: $input.file_id}</code></td></tr>
            <tr><td><code>rag_retrieve</code></td><td>知识库检索</td><td><code>prompt</code></td></tr>
            <tr><td><code>human</code></td><td>人工审核</td><td><code>payload: [{key, label, value}]</code></td></tr>
            <tr><td><code>end</code></td><td>结束节点（必须）</td><td>自定义输出字段</td></tr>
          </tbody>
        </table>

        <h4>数据引用语法</h4>
        <ul class="guide-rules">
          <li><code>$input.xxx</code> — 引用运行时输入参数</li>
          <li><code>$节点名.字段</code> — 引用上游节点的输出，如 <code>$reply.content</code></li>
          <li><code>depends_on</code> — 声明节点依赖关系（决定执行顺序）</li>
          <li><code>condition</code> — 条件执行，支持表达式（可选）</li>
        </ul>

        <h4>规则</h4>
        <ul class="guide-rules">
          <li>必须有一个 <code>type: end</code> 的结束节点</li>
          <li>节点 <code>name</code> 唯一，用作引用标识</li>
          <li><code>depends_on</code> 中的名字必须对应已定义的节点</li>
          <li><code>inputs</code> 中的 <code>$</code> 引用必须指向输入参数或上游节点</li>
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
  flex-direction: column;
  gap: 4px;
  padding: 10px 16px;
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
  padding-left: 13px;
}
.sidebar-name {
  font-size: 13px;
  font-weight: 500;
  color: var(--ink);
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
.detail-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 20px;
  border-bottom: 1px solid var(--line);
}
.detail-title {
  display: flex;
  align-items: baseline;
  gap: 10px;
  min-width: 0;
}
.detail-title h2 {
  font-size: 15px;
  margin: 0;
}
.detail-actions {
  display: flex;
  gap: 6px;
  flex-shrink: 0;
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
.yaml-editor :deep(textarea) {
  font-family: var(--font-mono);
  font-size: 12px;
  line-height: 1.6;
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
.guide-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
}
.guide-table th,
.guide-table td {
  padding: 8px 12px;
  text-align: left;
  border-bottom: 1px solid var(--line);
}
.guide-table th {
  font-weight: 600;
  color: var(--ink-2);
  font-size: 12px;
}
.guide-table code {
  font-family: var(--font-mono);
  font-size: 11px;
  background: rgba(77, 196, 178, 0.15);
  padding: 2px 5px;
  border-radius: 3px;
  color: var(--accent);
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

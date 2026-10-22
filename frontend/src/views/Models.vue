<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { EditPen, Refresh, ArrowRight } from '@element-plus/icons-vue'
import {
  listProviders,
  listModels,
  deleteProvider,
  syncModels,
  toggleModel,
  updateModelType,
  type ProviderItem,
  type ModelItem,
} from '@/api/providers'
import ProviderForm from '@/components/ProviderForm.vue'
import ModelForm from '@/components/ModelForm.vue'
import ModelSettings from '@/components/ModelSettings.vue'

// ---- 数据 ----
const providers = ref<ProviderItem[]>([])
const models = ref<ModelItem[]>([])
const loading = ref(false)

// ---- Provider drawer ----
const providerFormOpen = ref(false)
const editingProvider = ref<ProviderItem | null>(null)
// 每次打开弹窗递增 key，强制重建表单，清除上次输入
const providerFormKey = ref(0)

// ---- Model drawer ----
const modelFormOpen = ref(false)
const editingModel = ref<ModelItem | null>(null)
const modelProvider = ref<ProviderItem | null>(null)

// ---- 同步状态 ----
const syncingId = ref<number | null>(null)

// ---- 折叠状态 ----
const expandedProviders = ref<Set<number>>(new Set())

async function fetchAll() {
  loading.value = true
  try {
    const [p, m] = await Promise.all([listProviders(), listModels()])
    providers.value = p ?? []
    models.value = m ?? []
  } catch {
    // 静默失败
  } finally {
    loading.value = false
  }
}

// ---- Provider 操作 ----
function openProviderCreate() {
  editingProvider.value = null
  providerFormKey.value++
  providerFormOpen.value = true
}

function openProviderEdit(p: ProviderItem) {
  editingProvider.value = { ...p }
  providerFormKey.value++
  providerFormOpen.value = true
}

async function onProviderSaved(provider: ProviderItem) {
  providerFormOpen.value = false
  editingProvider.value = null
  await fetchAll()
  ElMessage.success('Provider 已创建，模型已自动导入')
}

async function handleDeleteProvider(p: ProviderItem) {
  try {
    await ElMessageBox.confirm(
      `确定删除 Provider「${p.name}」？其下所有模型将一并删除。`,
      '删除 Provider',
      { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  try {
    await deleteProvider(p.id)
    ElMessage.success(`已删除 ${p.name}`)
    await fetchAll()
  } catch (e: any) {
    ElMessage.error(e?.message || '删除失败')
  }
}

// ---- Model 操作（嵌套在 Provider 卡片内） ----
function openModelCreate(p: ProviderItem) {
  editingModel.value = null
  modelProvider.value = p
  modelFormOpen.value = true
}

function openModelEdit(m: ModelItem) {
  editingModel.value = { ...m }
  modelProvider.value = providers.value.find((p) => p.id === m.provider_id) ?? null
  modelFormOpen.value = true
}

async function onModelSaved() {
  modelFormOpen.value = false
  editingModel.value = null
  modelProvider.value = null
  await fetchAll()
}

async function handleToggleModel(m: ModelItem) {
  try {
    await toggleModel(m.id)
    await fetchAll()
  } catch (e: any) {
    ElMessage.error(e?.message || '操作失败')
  }
}

async function handleTypeChange(m: ModelItem, newType: string) {
  try {
    await updateModelType(m.id, newType)
    await fetchAll()
  } catch (e: any) {
    ElMessage.error(e?.message || '操作失败')
  }
}

function modelsOfProvider(pid: number): ModelItem[] {
  return models.value.filter((m) => m.provider_id === pid)
}

function toggleExpand(pid: number) {
  if (expandedProviders.value.has(pid)) {
    expandedProviders.value.delete(pid)
  } else {
    expandedProviders.value.add(pid)
  }
}

async function handleSyncModels(p: ProviderItem) {
  syncingId.value = p.id
  try {
    await syncModels(p.id)
    await fetchAll()
    ElMessage.success('模型已同步')
  } catch (e: any) {
    ElMessage.error(e?.message || '同步失败')
  } finally {
    syncingId.value = null
  }
}

onMounted(fetchAll)
</script>

<template>
  <div class="page">
    <header class="page-head">
      <div class="head-info">
        <h1>模型管理</h1>
        <span class="muted">管理 Provider 连接及其模型，并分配各场景的默认模型。</span>
      </div>
    </header>

    <div v-loading="loading" class="content">
      <!-- ===== 用途设置 ===== -->
      <section class="section">
        <h2 class="section-title">默认模型设置</h2>
        <ModelSettings :models="models" />
      </section>

      <!-- ===== Provider 列表（含各自模型） ===== -->
      <section class="section">
        <div class="section-head">
          <h2 class="section-title">Provider</h2>
          <el-button type="primary" size="small" @click="openProviderCreate">添加 Provider</el-button>
        </div>

        <div class="provider-list">
          <div v-for="p in providers" :key="p.id" class="provider-card">
            <div class="card-head">
              <span class="provider-name">{{ p.name }}</span>
              <span class="card-actions">
                <el-button class="btn-soft" size="small" :loading="syncingId === p.id" @click="handleSyncModels(p)">
                  <el-icon v-if="syncingId !== p.id"><Refresh /></el-icon>
                  <span>同步</span>
                </el-button>
                <el-button class="btn-soft" size="small" @click="openProviderEdit(p)">编辑</el-button>
                <el-button class="btn-soft btn-soft--danger" size="small" @click="handleDeleteProvider(p)">删除</el-button>
              </span>
            </div>
            <div class="card-body">
              <div class="info-row">
                <span class="info-label">URL</span>
                <span class="info-value mono">{{ p.base_url }}</span>
              </div>
              <div class="info-row" v-if="p.api_key">
                <span class="info-label">Key</span>
                <span class="info-value mono">{{ p.api_key }}</span>
              </div>
            </div>

            <!-- 该 Provider 下的模型 -->
            <div class="model-list">
              <div class="model-toggle" @click="toggleExpand(p.id)">
                <span class="toggle-label">
                  模型 ({{ modelsOfProvider(p.id).filter(m => m.is_enabled).length }}/{{ modelsOfProvider(p.id).length }})
                </span>
                <el-icon class="toggle-icon" :class="{ expanded: expandedProviders.has(p.id) }">
                  <ArrowRight />
                </el-icon>
              </div>
              <template v-if="expandedProviders.has(p.id)">
                <div v-for="m in modelsOfProvider(p.id)" :key="m.id" class="model-row">
                  <span class="model-name mono">{{ m.name }}</span>
                  <el-select
                    v-model="m.model_type"
                    size="small"
                    style="width: 110px"
                    @change="handleTypeChange(m, $event)"
                  >
                    <el-option label="文本生成" value="chat" />
                    <el-option label="Embedding" value="embedding" />
                    <el-option label="Rerank" value="rerank" />
                  </el-select>
                  <el-switch
                    v-model="m.is_enabled"
                    size="small"
                    @change="handleToggleModel(m)"
                  />
                </div>
                <div v-if="!modelsOfProvider(p.id).length" class="empty-models">
                  <span class="muted">暂无模型</span>
                </div>
              </template>
            </div>
          </div>

          <div v-if="!providers.length && !loading" class="empty">
            <p>暂无 Provider</p>
            <p class="muted">点击「添加 Provider」开始配置</p>
          </div>
        </div>
      </section>
    </div>

    <!-- Provider 表单 drawer -->
    <el-drawer
      v-model="providerFormOpen"
      :title="editingProvider ? '编辑 Provider' : '添加 Provider'"
      size="480px"
      :destroy-on-close="true"
    >
      <ProviderForm :key="providerFormKey" :provider="editingProvider" @saved="onProviderSaved" />
    </el-drawer>

    <!-- Model 表单 drawer -->
    <el-drawer
      v-model="modelFormOpen"
      :title="editingModel ? '编辑模型' : '添加模型'"
      size="480px"
      :destroy-on-close="true"
    >
      <ModelForm :model="editingModel" :provider="modelProvider" @saved="onModelSaved" />
    </el-drawer>
  </div>
</template>

<style scoped>
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
.content {
  padding: 16px 20px;
  display: flex;
  flex-direction: column;
  gap: 24px;
}
.section-title {
  font-size: 14px;
  font-weight: 600;
  margin: 0 0 12px;
  color: var(--ink);
}
.section-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 12px;
}
.section-head .section-title {
  margin: 0;
}

/* Provider 卡片 */
.provider-list {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(340px, 1fr));
  gap: 10px;
}
.provider-card {
  background: rgba(16, 21, 42, 0.72);
  border: 1px solid var(--line);
  border-radius: 12px;
  padding: 14px 16px;
  display: flex;
  flex-direction: column;
}
.card-head {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 8px;
}
.provider-name {
  flex: 1;
  font-family: var(--font-mono);
  font-size: 14px;
  font-weight: 600;
  color: var(--ink);
}
.card-actions {
  display: flex;
  gap: 4px;
}
.card-body {
  display: flex;
  flex-direction: column;
  gap: 5px;
}
.info-row {
  display: flex;
  align-items: baseline;
  gap: 10px;
  font-size: 12px;
}
.info-label {
  flex-shrink: 0;
  width: 50px;
  color: var(--ink-3);
  font-size: 11px;
}
.info-value {
  flex: 1;
  min-width: 0;
  color: var(--ink-2);
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
}
.info-value.mono {
  font-family: var(--font-mono);
  font-size: 12px;
}

/* 卡片内的模型列表 */
.model-list {
  margin-top: 10px;
  padding-top: 10px;
  border-top: 1px solid var(--line);
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.model-toggle {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 6px 0;
  cursor: pointer;
  user-select: none;
}
.model-toggle:hover {
  color: var(--ink);
}
.toggle-label {
  font-size: 13px;
  font-weight: 500;
  color: var(--ink-2);
}
.toggle-icon {
  transition: transform 0.2s;
}
.toggle-icon.expanded {
  transform: rotate(90deg);
}
.model-row {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 6px 10px;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: rgba(16, 21, 42, 0.5);
}
.model-row .el-switch {
  margin-left: auto;
}
.model-name {
  font-size: 13px;
  font-weight: 500;
  color: var(--ink);
  min-width: 120px;
}
.act {
  display: inline-flex;
  padding: 5px;
  border: 0;
  background: none;
  color: var(--ink-2);
  cursor: pointer;
  border-radius: 6px;
}
.act:hover {
  color: var(--accent);
}
.act.danger:hover {
  color: var(--danger);
}
.empty-models {
  padding: 4px 2px;
  font-size: 12px;
}
.add-model {
  align-self: flex-start;
  margin-top: auto;
}
.empty {
  grid-column: 1 / -1;
  text-align: center;
  padding: 24px 0;
}
.empty p {
  margin: 0 0 4px;
  font-size: 13px;
}
.muted {
  color: var(--ink-3);
}
.btn-soft {
  --el-button-bg-color: rgba(255, 255, 255, 0.06);
  --el-button-border-color: var(--line);
  --el-button-text-color: var(--ink-2);
  --el-button-hover-bg-color: rgba(255, 255, 255, 0.1);
  --el-button-hover-border-color: rgba(255, 255, 255, 0.18);
  --el-button-hover-text-color: var(--ink);
}
.btn-soft--danger {
  --el-button-text-color: #f87171;
  --el-button-hover-bg-color: rgba(248, 113, 113, 0.12);
  --el-button-hover-border-color: rgba(248, 113, 113, 0.35);
  --el-button-hover-text-color: #fca5a5;
}
</style>

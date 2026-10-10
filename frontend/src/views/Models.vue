<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { EditPen, Delete } from '@element-plus/icons-vue'
import {
  listProviders,
  listModels,
  deleteProvider,
  deleteModel,
  getAvailableModels,
  type ProviderItem,
  type ModelItem,
  type AvailableModel,
} from '@/api/providers'
import ProviderForm from '@/components/ProviderForm.vue'
import ModelForm from '@/components/ModelForm.vue'
import ModelSettings from '@/components/ModelSettings.vue'
import ImportModelsDialog from '@/components/ImportModelsDialog.vue'

// ---- 数据 ----
const providers = ref<ProviderItem[]>([])
const models = ref<ModelItem[]>([])
const loading = ref(false)

// ---- Provider drawer ----
const providerFormOpen = ref(false)
const editingProvider = ref<ProviderItem | null>(null)

// ---- Model drawer ----
const modelFormOpen = ref(false)
const editingModel = ref<ModelItem | null>(null)
const modelProvider = ref<ProviderItem | null>(null)

// ---- Import models dialog ----
const importDialogOpen = ref(false)
const importProviderId = ref<number | null>(null)
const availableModels = ref<AvailableModel[]>([])

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
  providerFormOpen.value = true
}

function openProviderEdit(p: ProviderItem) {
  editingProvider.value = { ...p }
  providerFormOpen.value = true
}

async function onProviderSaved(provider: ProviderItem) {
  providerFormOpen.value = false
  editingProvider.value = null
  await fetchAll()
  // 自动拉取可导入模型
  try {
    const list = await getAvailableModels(provider.id)
    if (list?.length) {
      importProviderId.value = provider.id
      availableModels.value = list
      importDialogOpen.value = true
    }
  } catch {
    // 连不上就跳过，不阻塞
  }
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

async function handleDeleteModel(m: ModelItem) {
  try {
    await ElMessageBox.confirm(
      `确定删除模型「${m.name}」？`,
      '删除模型',
      { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  try {
    await deleteModel(m.id)
    ElMessage.success(`已删除 ${m.name}`)
    await fetchAll()
  } catch (e: any) {
    ElMessage.error(e?.message || '删除失败')
  }
}

function modelsOfProvider(pid: number): ModelItem[] {
  return models.value.filter((m) => m.provider_id === pid)
}

const typeLabel: Record<string, string> = {
  chat: '文本生成',
  embedding: 'Embedding',
  rerank: 'Rerank',
}

const typeTagType: Record<string, 'primary' | 'success' | 'warning'> = {
  chat: 'primary',
  embedding: 'success',
  rerank: 'warning',
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
              <div v-for="m in modelsOfProvider(p.id)" :key="m.id" class="model-row">
                <span class="model-name mono">{{ m.name }}</span>
                <el-tag size="small" :type="typeTagType[m.model_type] ?? 'info'" disable-transitions>
                  {{ typeLabel[m.model_type] ?? m.model_type }}
                </el-tag>
                <el-tag v-if="!m.is_enabled" size="small" type="info" disable-transitions>已禁用</el-tag>
                <span class="row-actions">
                  <button type="button" class="act" title="编辑" @click="openModelEdit(m)">
                    <el-icon><EditPen /></el-icon>
                  </button>
                  <button type="button" class="act danger" title="删除" @click="handleDeleteModel(m)">
                    <el-icon><Delete /></el-icon>
                  </button>
                </span>
              </div>
              <div v-if="!modelsOfProvider(p.id).length" class="empty-models">
                <span class="muted">暂无模型</span>
              </div>
              <el-button class="btn-soft add-model" size="small" @click="openModelCreate(p)">＋ 添加模型</el-button>
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
      <ProviderForm :provider="editingProvider" @saved="onProviderSaved" />
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

    <!-- 导入模型弹窗 -->
    <ImportModelsDialog
      v-model:visible="importDialogOpen"
      :provider-id="importProviderId"
      :models="availableModels"
      @imported="fetchAll"
    />
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
.model-row {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 6px 10px;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: rgba(16, 21, 42, 0.5);
}
.model-name {
  font-size: 13px;
  font-weight: 500;
  color: var(--ink);
  min-width: 120px;
}
.row-actions {
  margin-left: auto;
  display: flex;
  gap: 2px;
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

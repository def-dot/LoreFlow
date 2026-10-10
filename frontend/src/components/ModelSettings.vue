<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import {
  getModelSettings,
  updateModelSettings,
  type ModelItem,
  type ModelSettings as Settings,
} from '@/api/providers'

// 模型清单由父级 Models.vue 统一拉取，这里不重复请求 /models
const props = defineProps<{ models: ModelItem[] }>()

const settings = ref<Settings>({
  default_chat_model: null,
  default_embedding_model: null,
  default_rerank_model: null,
})
const saving = ref(false)

const chatModels = computed(() => props.models.filter((m) => m.model_type === 'chat' && m.is_enabled))
const embeddingModels = computed(() => props.models.filter((m) => m.model_type === 'embedding' && m.is_enabled))
const rerankModels = computed(() => props.models.filter((m) => m.model_type === 'rerank' && m.is_enabled))

// 树形选项：provider 为分组节点（不可选），model 为叶子节点
interface TreeNode {
  id: number | string
  label: string
  disabled?: boolean
  children?: TreeNode[]
}

function toTree(models: ModelItem[]): TreeNode[] {
  const groups = new Map<number, { label: string; children: TreeNode[] }>()
  for (const m of models) {
    const g = groups.get(m.provider_id) ?? {
      label: m.provider?.name ?? `Provider ${m.provider_id}`,
      children: [],
    }
    g.children.push({ id: m.model_key, label: m.name })
    groups.set(m.provider_id, g)
  }
  return [...groups.entries()].map(([pid, g]) => ({
    id: `p-${pid}`,
    label: g.label,
    disabled: true,
    children: g.children,
  }))
}

function filterNode(query: string, data: TreeNode): boolean {
  if (!query) return true
  return data.label.toLowerCase().includes(query.toLowerCase())
}

const fields = computed(() => [
  { key: 'default_chat_model' as const, label: '文本生成 (Chat)', placeholder: '选择默认 LLM', tree: toTree(chatModels.value) },
  { key: 'default_embedding_model' as const, label: 'Embedding', placeholder: '选择默认 Embedding 模型', tree: toTree(embeddingModels.value) },
  { key: 'default_rerank_model' as const, label: 'Rerank', placeholder: '选择默认 Rerank 模型', tree: toTree(rerankModels.value) },
])


async function loadSettings() {
  try {
    const s = await getModelSettings()
    settings.value = s ?? { default_chat_model: null, default_embedding_model: null, default_rerank_model: null }
  } catch {
    // 静默失败
  }
}

async function handleSave() {
  saving.value = true
  try {
    await updateModelSettings(settings.value)
    ElMessage.success('已保存')
  } catch (e: any) {
    ElMessage.error(e?.message || '保存失败')
  } finally {
    saving.value = false
  }
}

onMounted(loadSettings)
</script>

<template>
  <div class="model-settings">
    <p class="desc">选择各场景使用的默认模型。未指定模型时，系统将使用此处配置。</p>

    <div class="settings-row">
      <div class="setting-group" v-for="f in fields" :key="f.key">
        <span class="setting-label">{{ f.label }}</span>
        <el-tree-select
          v-model="settings[f.key]"
          :data="f.tree"
          :props="{ value: 'id', label: 'label', children: 'children', disabled: 'disabled' }"
          node-key="id"
          :filter-node-method="filterNode"
          popper-class="model-tree-popper"
          default-expand-all
          filterable
          clearable
          :placeholder="f.placeholder"
          size="default"
          class="setting-select"
        />
      </div>
      <el-button type="primary" size="default" :loading="saving" @click="handleSave">保存</el-button>
    </div>
  </div>
</template>

<style scoped>
.model-settings {
  padding: 8px 0;
}
.desc {
  font-size: 12px;
  color: var(--ink-3);
  margin: 0 0 10px;
}
.settings-row {
  display: flex;
  align-items: center;
  gap: 24px;
  flex-wrap: wrap;
}
.setting-group {
  display: flex;
  align-items: center;
  gap: 8px;
}
.setting-label {
  font-size: 12px;
  font-weight: 500;
  color: var(--ink-2);
  white-space: nowrap;
}
.setting-select {
  width: 240px;
}
</style>

<style>
/* 下拉面板被 teleport 到 body，scoped 样式够不到 */
.model-tree-popper .el-tree-node.is-disabled > .el-tree-node__content {
  cursor: default;
}
.model-tree-popper .el-tree-node.is-disabled > .el-tree-node__content .el-tree-node__label {
  color: var(--ink-2);
  opacity: 1;
  font-weight: 600;
}
</style>

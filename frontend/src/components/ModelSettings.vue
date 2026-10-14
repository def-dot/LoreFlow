<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import {
  getModelSettings,
  updateModelSettings,
  type ModelItem,
  type ModelSettings as Settings,
} from '@/api/providers'
import ModelTreeSelect from '@/components/ModelTreeSelect.vue'

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

const fields = computed(() => [
  { key: 'default_chat_model' as const, label: '文本生成 (Chat)', placeholder: '选择默认 LLM', models: chatModels.value },
  { key: 'default_embedding_model' as const, label: 'Embedding', placeholder: '选择默认 Embedding 模型', models: embeddingModels.value },
  { key: 'default_rerank_model' as const, label: 'Rerank', placeholder: '选择默认 Rerank 模型', models: rerankModels.value },
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
        <ModelTreeSelect
          v-model="settings[f.key]"
          :models="f.models"
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

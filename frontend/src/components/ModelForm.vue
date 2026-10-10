<script setup lang="ts">
import { ref, watch, computed } from 'vue'
import { ElMessage } from 'element-plus'
import { createModel, updateModel, type ModelItem, type ProviderItem } from '@/api/providers'

const props = defineProps<{
  model?: ModelItem | null
  provider: ProviderItem | null
}>()

const emit = defineEmits<{
  saved: []
}>()

const form = ref({
  name: '',
  model_type: 'chat' as 'chat' | 'embedding' | 'rerank',
  is_enabled: true,
})

const saving = ref(false)
const isEdit = computed(() => !!props.model?.id)

watch(
  () => props.model,
  (m) => {
    form.value = m
      ? { name: m.name, model_type: m.model_type, is_enabled: m.is_enabled }
      : { name: '', model_type: 'chat', is_enabled: true }
  },
  { immediate: true },
)

async function handleSave() {
  const providerId = props.model?.provider_id ?? props.provider?.id
  if (!providerId) {
    ElMessage.warning('缺少 Provider')
    return
  }
  if (!form.value.name.trim()) {
    ElMessage.warning('请输入模型名称')
    return
  }
  saving.value = true
  try {
    if (isEdit.value && props.model) {
      await updateModel(props.model.id, { ...form.value, provider_id: providerId })
      ElMessage.success('已更新')
    } else {
      await createModel({ ...form.value, provider_id: providerId })
      ElMessage.success('已创建')
    }
    emit('saved')
  } catch (e: any) {
    ElMessage.error(e?.message || '保存失败')
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <div class="model-form">
    <el-form label-position="top" size="default">
      <el-form-item label="所属 Provider">
        <el-input :model-value="model?.provider?.name ?? provider?.name ?? ''" disabled />
      </el-form-item>

      <el-form-item label="模型名称" required>
        <el-input v-model="form.name" placeholder="如 gpt-4、bge-m3" maxlength="200" />
      </el-form-item>

      <el-form-item label="模型类型" required>
        <el-radio-group v-model="form.model_type">
          <el-radio-button value="chat">文本生成</el-radio-button>
          <el-radio-button value="embedding">Embedding</el-radio-button>
          <el-radio-button value="rerank">Rerank</el-radio-button>
        </el-radio-group>
      </el-form-item>

      <el-form-item label="启用">
        <el-switch v-model="form.is_enabled" />
      </el-form-item>

      <el-form-item>
        <el-button type="primary" :loading="saving" @click="handleSave">
          {{ isEdit ? '保存修改' : '添加模型' }}
        </el-button>
      </el-form-item>
    </el-form>
  </div>
</template>

<style scoped>
.model-form {
  padding: 16px 0;
}
</style>

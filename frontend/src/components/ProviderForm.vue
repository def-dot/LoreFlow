<script setup lang="ts">
import { ref, watch, computed } from 'vue'
import { ElMessage } from 'element-plus'
import {
  createProvider,
  updateProvider,
  testProvider,
  type ProviderItem,
  type ProviderTestResult,
} from '@/api/providers'

const props = defineProps<{
  provider?: ProviderItem | null
}>()

const emit = defineEmits<{
  saved: [provider: ProviderItem]
}>()

const form = ref({
  name: '',
  base_url: '',
  api_key: '',
})

const saving = ref(false)
const testing = ref(false)
const testResult = ref<ProviderTestResult | null>(null)
const showApiKey = ref(false)

const isEdit = computed(() => !!props.provider?.id)

watch(
  () => props.provider,
  (p) => {
    if (p) {
      form.value = {
        name: p.name,
        base_url: p.base_url,
        api_key: p.api_key,
      }
    } else {
      form.value = { name: '', base_url: '', api_key: '' }
    }
    testResult.value = null
  },
  { immediate: true },
)

async function handleTest() {
  if (!isEdit.value || !props.provider?.id) return
  testing.value = true
  testResult.value = null
  try {
    testResult.value = await testProvider(props.provider.id)
    if (testResult.value.success) {
      ElMessage.success(`连通成功，发现 ${testResult.value.models.length} 个模型`)
    } else {
      ElMessage.error(`连通失败：${testResult.value.error}`)
    }
  } catch (e: any) {
    ElMessage.error(e?.message || '测试失败')
  } finally {
    testing.value = false
  }
}

async function handleSave() {
  if (!form.value.name.trim()) {
    ElMessage.warning('请输入 Provider 名称')
    return
  }
  if (!form.value.base_url.trim()) {
    ElMessage.warning('请输入 Base URL')
    return
  }
  saving.value = true
  try {
    let provider: ProviderItem
    if (isEdit.value && props.provider) {
      provider = await updateProvider(props.provider.id, form.value)
      ElMessage.success('已更新')
    } else {
      provider = await createProvider(form.value)
      ElMessage.success('已创建')
    }
    emit('saved', provider)
  } catch (e: any) {
    ElMessage.error(e?.message || '保存失败')
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <div class="provider-form">
    <el-form label-position="top" size="default">
      <el-form-item label="名称" required>
        <el-input v-model="form.name" placeholder="如 ollama、openai、mimo" maxlength="100" />
      </el-form-item>

      <el-form-item label="Base URL" required>
        <el-input v-model="form.base_url" placeholder="http://localhost:11434/v1" maxlength="500" />
      </el-form-item>

      <el-form-item label="API Key">
        <el-input
          v-model="form.api_key"
          :type="showApiKey ? 'text' : 'password'"
          placeholder="可选，无 Key 则留空"
        >
          <template #append>
            <el-button @click="showApiKey = !showApiKey">
              {{ showApiKey ? '隐藏' : '显示' }}
            </el-button>
          </template>
        </el-input>
      </el-form-item>

      <el-form-item>
        <div class="actions">
          <el-button v-if="isEdit" :loading="testing" @click="handleTest">测试连通性</el-button>
          <el-button type="primary" :loading="saving" @click="handleSave">
            {{ isEdit ? '保存修改' : '创建 Provider' }}
          </el-button>
        </div>
      </el-form-item>
    </el-form>

    <!-- 测试结果 -->
    <div v-if="testResult" class="test-result" :class="{ success: testResult.success, fail: !testResult.success }">
      <div v-if="testResult.success">
        <p>✅ 连通成功，发现 {{ testResult.models.length }} 个模型：</p>
        <div class="test-models">
          <el-tag v-for="m in testResult.models" :key="m" size="small" type="success">{{ m }}</el-tag>
        </div>
      </div>
      <div v-else>
        <p>❌ 连通失败：{{ testResult.error }}</p>
      </div>
    </div>
  </div>
</template>

<style scoped>
.provider-form {
  padding: 16px 0;
}
.actions {
  display: flex;
  gap: 8px;
}
.test-result {
  margin-top: 12px;
  padding: 10px 14px;
  border-radius: 6px;
  font-size: 13px;
  line-height: 1.6;
}
.test-result.success {
  background: rgba(77, 196, 178, 0.1);
  border: 1px solid rgba(77, 196, 178, 0.3);
}
.test-result.fail {
  background: rgba(248, 113, 113, 0.1);
  border: 1px solid rgba(248, 113, 113, 0.3);
}
.test-result p {
  margin: 0 0 6px;
}
.test-models {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
}
</style>
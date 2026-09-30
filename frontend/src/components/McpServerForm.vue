<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import {
  createMcpServer,
  updateMcpServer,
  type McpServerConfig,
} from '@/api/mcp'

const props = defineProps<{
  server?: McpServerConfig | null
}>()

const emit = defineEmits<{
  saved: []
}>()

const configText = ref('')

const saving = ref(false)
const saveError = ref<string | null>(null)

const isEdit = computed(() => !!props.server?.name)

watch(
  () => props.server,
  (s) => {
    if (s) {
      // 编辑时回填为标准格式
      const cfg: Record<string, any> = {}
      if (s.command) cfg.command = s.command
      if (s.args?.length) cfg.args = s.args
      if (s.env && Object.keys(s.env).length) cfg.env = s.env
      if (s.url) cfg.url = s.url
      configText.value = Object.keys(cfg).length
        ? JSON.stringify({ mcpServers: { [s.name]: cfg } }, null, 2)
        : ''
    } else {
      configText.value = ''
    }
    saveError.value = null
  },
  { immediate: true },
)

async function handleSave() {
  const text = configText.value.trim()
  if (!text) {
    ElMessage.warning('请粘贴服务器配置')
    return
  }
  let parsed: any
  try {
    parsed = JSON.parse(text)
  } catch {
    ElMessage.warning('JSON 格式错误，请检查')
    return
  }
  if (!parsed.mcpServers || typeof parsed.mcpServers !== 'object') {
    ElMessage.warning('请粘贴标准格式：{ "mcpServers": { "名称": { ... } } }')
    return
  }

  saving.value = true
  saveError.value = null
  try {
    if (isEdit.value && props.server) {
      await updateMcpServer(props.server.name, parsed)
      ElMessage.success('已保存')
    } else {
      await createMcpServer(parsed)
      ElMessage.success('已创建')
    }
    emit('saved')
  } catch (e: any) {
    saveError.value = e?.response?.data?.msg || e?.message || '保存失败'
  } finally {
    saving.value = false
  }
}

const configPlaceholder = `{
  "mcpServers": {
    "filesystem": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "/path"]
    }
  }
}`
</script>

<template>
  <div class="mcp-form">
    <el-input
      v-model="configText"
      type="textarea"
      :rows="12"
      :placeholder="configPlaceholder"
      class="config-textarea"
    />

    <div v-if="saveError" class="save-error">{{ saveError }}</div>

    <div class="actions">
      <el-button type="primary" :loading="saving" @click="handleSave">
        {{ isEdit ? '保存修改' : '创建' }}
      </el-button>
    </div>
  </div>
</template>

<style scoped>
.mcp-form {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.config-textarea :deep(textarea) {
  font-family: var(--font-mono, ui-monospace, monospace);
  font-size: 13px;
  line-height: 1.6;
}

.save-error {
  color: var(--danger, #f85149);
  font-size: 13px;
  line-height: 1.6;
}

.actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  padding-top: 4px;
}
</style>

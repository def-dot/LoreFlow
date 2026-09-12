<script setup lang="ts">
import { ref, watch, computed } from 'vue'
import { useAgentsStore } from '@/stores/agents'
import { listModels, listTools, listSkills } from '@/api/registry'
import { ElMessage } from 'element-plus'
import type { AgentListItem } from '@/api/agents'

const props = defineProps<{
  agent?: AgentListItem | null
}>()

const emit = defineEmits<{
  saved: []
}>()

const store = useAgentsStore()

const form = ref<AgentListItem>({
  id: 0,
  name: '',
  description: '',
  system_prompt: '',
  model: '',
  tools: [],
  skills: [],
  created_at: null,
  updated_at: null,
})

const saving = ref(false)
const models = ref<Record<string, string[]>>({})
const allTools = ref<{ name: string; description: string }[]>([])
const allSkills = ref<{ name: string; description: string }[]>([])

// 编辑模式：回填
watch(
  () => props.agent,
  (a) => {
    if (a) {
      form.value = {
        name: a.name,
        description: a.description,
        system_prompt: a.system_prompt,
        model: a.model,
        tools: [...a.tools],
        skills: [...a.skills],
      }
    } else {
      form.value = {
        name: '',
        description: '',
        system_prompt: '',
        model: '',
        tools: [],
        skills: [],
      }
    }
  },
  { immediate: true },
)

const isEdit = computed(() => !!props.agent?.id)

// 加载可用模型、工具、技能
async function loadOptions() {
  try {
    const [m, toolsResp, skillsResp] = await Promise.all([
      listModels(),
      listTools(),
      listSkills(),
    ])
    models.value = m
    allTools.value = (toolsResp.items || []).map((t) => ({
      name: t.name,
      description: t.description || '',
    }))
    allSkills.value = (skillsResp.items || []).map((s) => ({
      name: s.name,
      description: s.description || '',
    }))
  } catch {
    // 静默失败
  }
}

loadOptions()

const modelOptions = computed(() => {
  const opts: { label: string; value: string }[] = []
  for (const [provider, mlist] of Object.entries(models.value)) {
    for (const m of mlist as string[]) {
      opts.push({ label: `${provider}:${m}`, value: `${provider}:${m}` })
    }
  }
  return opts
})

async function handleSave() {
  if (!form.value.name.trim()) {
    ElMessage.warning('请输入 Agent 名称')
    return
  }
  saving.value = true
  try {
    if (isEdit.value && props.agent) {
      await store.updateAgent(props.agent.id, form.value)
      ElMessage.success('已更新')
    } else {
      await store.createAgent(form.value)
      ElMessage.success('已创建')
    }
    emit('saved')
  } catch (e: any) {
    ElMessage.error(e.message || '保存失败')
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <div class="agent-form">
    <el-form label-position="top" size="default">
      <el-form-item label="名称" required>
        <el-input v-model="form.name" placeholder="我的助手" maxlength="200" />
      </el-form-item>

      <el-form-item label="描述">
        <el-input
          v-model="form.description"
          type="textarea"
          :rows="2"
          placeholder="这个 Agent 用来做什么..."
        />
      </el-form-item>

      <el-form-item label="模型">
        <el-select
          v-model="form.model"
          placeholder="使用默认模型"
          clearable
          filterable
          style="width: 100%"
        >
          <el-option
            v-for="opt in modelOptions"
            :key="opt.value"
            :label="opt.label"
            :value="opt.value"
          />
        </el-select>
      </el-form-item>

      <el-form-item label="系统提示词">
        <el-input
          v-model="form.system_prompt"
          type="textarea"
          :rows="4"
          placeholder="你是一个专业的助手..."
        />
      </el-form-item>

      <el-form-item label="工具">
        <el-select
          v-model="form.tools"
          multiple
          filterable
          allow-create
          default-first-option
          placeholder="选择工具"
          style="width: 100%"
        >
          <el-option label="* 全部工具" value="*" />
          <el-option
            v-for="t in allTools"
            :key="t.name"
            :label="t.name"
            :value="t.name"
          >
            <span>{{ t.name }}</span>
            <span class="opt-desc">{{ t.description || '暂无描述' }}</span>
          </el-option>
        </el-select>
      </el-form-item>

      <el-form-item label="技能">
        <el-select
          v-model="form.skills"
          multiple
          filterable
          allow-create
          default-first-option
          placeholder="选择技能"
          style="width: 100%"
        >
          <el-option label="* 全部技能" value="*" />
          <el-option
            v-for="s in allSkills"
            :key="s.name"
            :label="s.name"
            :value="s.name"
          >
            <span>{{ s.name }}</span>
            <span v-if="s.description" class="opt-desc">{{ s.description }}</span>
          </el-option>
        </el-select>
      </el-form-item>

      <el-form-item>
        <el-button type="primary" :loading="saving" @click="handleSave">
          {{ isEdit ? '保存修改' : '创建 Agent' }}
        </el-button>
      </el-form-item>
    </el-form>
  </div>
</template>

<style scoped>
.agent-form {
  padding: 16px 0;
}
</style>

<style>
/* el-option 内容在 body 的 teleported popover 里，scoped 无法覆盖 */
.opt-desc {
  margin-left: 8px;
  font-size: 12px;
  color: var(--el-text-color-secondary);
  max-width: 300px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  display: inline-block;
  vertical-align: bottom;
}
</style>

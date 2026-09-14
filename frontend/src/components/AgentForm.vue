<script setup lang="ts">
import { ref, watch, computed, nextTick } from 'vue'
import { useAgentsStore } from '@/stores/agents'
import { listModels, listTools, listSkills, type ToolOut } from '@/api/registry'
import { listKnowledgeBases, type KnowledgeBase } from '@/api/knowledge'
import { ElMessage } from 'element-plus'
import type { AgentListItem } from '@/api/agents'

const props = defineProps<{
  agent?: AgentListItem | null
}>()

const emit = defineEmits<{
  saved: []
}>()

const store = useAgentsStore()

const form = ref({
  name: '',
  description: '',
  system_prompt: '',
  model: '',
  tools: [] as string[],
  skills: [] as string[],
  kb_id: null as number | null,
})

const saving = ref(false)
const models = ref<Record<string, string[]>>({})
const allTools = ref<ToolOut[]>([])
const allSkills = ref<{ name: string; description: string }[]>([])
const knowledgeBases = ref<KnowledgeBase[]>([])
const toolTreeRef = ref<any>(null)

interface TreeNode {
  id: string
  label: string
  description?: string
  children?: TreeNode[]
}

const toolTreeData = computed<TreeNode[]>(() => {
  const grouped = new Map<string, ToolOut[]>()
  const ungrouped: TreeNode[] = []
  for (const t of allTools.value) {
    if (t.group) {
      const arr = grouped.get(t.group) || []
      arr.push(t)
      grouped.set(t.group, arr)
    } else {
      ungrouped.push({ id: t.name, label: t.label, description: t.description })
    }
  }
  const children: TreeNode[] = [...ungrouped]
  for (const [g, tools] of [...grouped.entries()].sort()) {
    children.push({
      id: `__group_${g}__`,
      label: g,
      children: tools.map((t) => ({ id: t.name, label: t.label, description: t.description })),
    })
  }
  return [{ id: '*', label: '全部工具', children }]
})

const toolExpandedKeys = computed(() => {
  const root = toolTreeData.value[0]
  return root ? [root.id] : []
})

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
        kb_id: a.kb_id ?? null,
      }
    } else {
      form.value = {
        name: '',
        description: '',
        system_prompt: '',
        model: '',
        tools: [],
        skills: [],
        kb_id: null,
      }
    }
    // 同步树勾选状态（等 tree 渲染后）
    nextTick(() => syncTreeFromForm())
  },
  { immediate: true },
)

const isEdit = computed(() => !!props.agent?.id)

// 加载可用模型、工具、技能
async function loadOptions() {
  try {
    const [m, toolsResp, skillsResp, kbs] = await Promise.all([
      listModels(),
      listTools(),
      listSkills(),
      listKnowledgeBases(),
    ])
    models.value = m
    allTools.value = toolsResp || []
    allSkills.value = (skillsResp || []).map((s) => ({
      name: s.name,
      description: s.description || '',
    }))
    knowledgeBases.value = kbs
    // options 加载完成后同步树（编辑模式下回填）
    nextTick(() => syncTreeFromForm())
  } catch {
    // 静默失败
  }
}

// 树勾选 → 同步到 form.tools（只存叶子节点工具名）
function syncFormFromTree() {
  const tree = toolTreeRef.value
  if (!tree) return
  form.value.tools = (tree.getCheckedKeys() as string[])
    .filter((k) => k !== '*' && !k.startsWith('__group_'))
}

// form.tools → 同步到树勾选
function syncTreeFromForm() {
  const tree = toolTreeRef.value
  if (!tree) return
  for (const t of form.value.tools) {
    tree.setChecked(t, true, false)
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
        <el-tree
          ref="toolTreeRef"
          :data="toolTreeData"
          show-checkbox
          node-key="id"
          :default-expanded-keys="toolExpandedKeys"
          :props="{ children: 'children', label: 'label' }"
          class="tool-tree"
          @check="syncFormFromTree"
        >
          <template #default="{ node, data }">
            <span class="tree-node">
              <span>{{ data.label }}</span>
              <span v-if="data.description" class="tree-desc">{{ data.description }}</span>
            </span>
          </template>
        </el-tree>
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

      <el-form-item label="知识库">
        <el-select
          v-model="form.kb_id"
          placeholder="不关联知识库"
          clearable
          style="width: 100%"
        >
          <el-option
            v-for="kb in knowledgeBases"
            :key="kb.id"
            :label="kb.name"
            :value="kb.id"
          >
            <span>{{ kb.name }}</span>
            <span v-if="kb.description" class="opt-desc">{{ kb.description }}</span>
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

.tool-tree {
  width: 100%;
  border: 1px solid var(--el-border-color);
  border-radius: 4px;
  max-height: 360px;
  overflow-y: auto;
}

.tree-node {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
}

.tree-desc {
  font-size: 12px;
  color: var(--el-text-color-secondary);
  max-width: 300px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
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

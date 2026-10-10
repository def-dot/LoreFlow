<script setup lang="ts">
import { ref, watch, computed, nextTick } from 'vue'
import { useAgentsStore } from '@/stores/agents'
import { listModels, type ModelItem } from '@/api/providers'
import { listTools, listSkills, type ToolOut } from '@/api/registry'
import { listPlugins, type PluginInfo } from '@/api/plugins'
import { listMcpServers, reconnectMcpServer, type McpServer } from '@/api/mcp'
import { listTags, type TagInfo } from '@/api/knowledge'
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
  tags: [] as string[],
})

const saving = ref(false)
const models = ref<ModelItem[]>([])
const allTools = ref<ToolOut[]>([])
const allSkills = ref<{ name: string; description: string }[]>([])
const allTags = ref<TagInfo[]>([])
const plugins = ref<PluginInfo[]>([])
const mcpServers = ref<McpServer[]>([])
const mcpBusy = ref('')
const collapsedMcp = ref<Set<string>>(new Set())
const searchQuery = ref('')
const toolDialogVisible = ref(false)
const skillDialogVisible = ref(false)
const skillSearchQuery = ref('')

/** name → label 映射，用于 tag 显示 */
const toolNameMap = computed(() => {
  const m = new Map<string, string>()
  for (const t of allTools.value) m.set(t.name, t.label || t.name)
  return m
})

// ---- 来源推算（与 Capabilities.vue 同逻辑）----

interface SourceInfo { kind: 'builtin' | 'plugin' | 'mcp' | 'pipeline'; name: string }

function buildSourceMap(): Map<string, SourceInfo> {
  const srcMap = new Map<string, SourceInfo>()
  for (const p of plugins.value)
    for (const n of p.tool_names) srcMap.set(n, { kind: 'plugin', name: p.filename })
  for (const s of mcpServers.value)
    for (const n of s.tool_names) srcMap.set(n, { kind: 'mcp', name: s.name })
  for (const t of allTools.value)
    if (t.metadata?.source === 'pipeline') srcMap.set(t.name, { kind: 'pipeline', name: t.name })
  return srcMap
}

interface ToolItem {
  name: string
  label: string
  description: string
  source: SourceInfo
}

interface ToolSection {
  key: string
  title: string
  groups: { groupName: string; items: ToolItem[] }[]
  server?: McpServer
}

interface ToolTab {
  key: string
  label: string
  sections: ToolSection[]
}

const activeToolTab = ref('builtin')

const toolTabs = computed<ToolTab[]>(() => {
  const srcMap = buildSourceMap()
  const q = searchQuery.value.trim().toLowerCase()

  const matchTool = (t: ToolOut | ToolItem) => {
    if (!q) return true
    const hay = `${t.name} ${t.label} ${t.description}`.toLowerCase()
    return hay.includes(q)
  }

  // ---- 内置：系统 @tool 注册的工具（排除插件/MCP/工作流来源，隐藏 load_skill）----
  const builtinItems: ToolItem[] = allTools.value
    .filter((t) => !srcMap.has(t.name) && t.name !== 'load_skill' && matchTool(t))
    .map((t) => ({
      name: t.name,
      label: t.label || t.name,
      description: t.description || '',
      source: { kind: 'builtin' as const, name: 'builtin' },
    }))
  const builtinTab: ToolTab = {
    key: 'builtin',
    label: '内置',
    sections: [{
      key: 'builtin',
      title: '内置',
      groups: builtinItems.length ? [{ groupName: '内置', items: builtinItems }] : [],
    }],
  }

  // ---- 工作流 ----
  const workflowItems = allTools.value
    .filter((t) => srcMap.get(t.name)?.kind === 'pipeline' && matchTool(t))
    .map((t) => ({
      name: t.name,
      label: t.label || t.name,
      description: t.description || '',
      source: { kind: 'pipeline' as const, name: t.name },
    }))
  const workflowTab: ToolTab = {
    key: 'workflow',
    label: '工作流',
    sections: [{
      key: 'workflow',
      title: '工作流',
      groups: workflowItems.length ? [{ groupName: '工作流', items: workflowItems }] : [],
    }],
  }

  // ---- 自定义脚本 ----
  const scriptSections: ToolSection[] = plugins.value.map((p) => {
    const items = allTools.value
      .filter((t) => srcMap.get(t.name)?.kind === 'plugin' && srcMap.get(t.name)?.name === p.filename && matchTool(t))
      .map((t) => ({
        name: t.name,
        label: t.label || t.name,
        description: t.description || '',
        source: { kind: 'plugin' as const, name: p.filename },
      }))
    return {
      key: `script::${p.filename}`,
      title: p.filename,
      groups: items.length ? [{ groupName: p.filename, items }] : [],
    }
  })
  const scriptTab: ToolTab = { key: 'script', label: '自定义脚本', sections: scriptSections }

  // ---- MCP 服务器 ----
  const mcpSections: ToolSection[] = mcpServers.value.map((s) => {
    const items = allTools.value
      .filter((t) => srcMap.get(t.name)?.kind === 'mcp' && srcMap.get(t.name)?.name === s.name && matchTool(t))
      .map((t) => ({
        name: t.name,
        label: t.label || t.name,
        description: t.description || '',
        source: { kind: 'mcp' as const, name: s.name },
      }))
    return {
      key: `mcp::${s.name}`,
      title: s.name,
      groups: items.length ? [{ groupName: s.name, items }] : [],
      server: s,
    }
  })
  const mcpTab: ToolTab = { key: 'mcp', label: 'MCP 服务器', sections: mcpSections }

  return [builtinTab, workflowTab, mcpTab, scriptTab]
})

/** 每个 tab 的命中数 */
function tabMatchCount(tab: ToolTab): number {
  return tab.sections.flatMap((s) => s.groups.flatMap((g) => g.items)).length
}

/** 搜索时自动切到第一个有命中的 tab */
watch(searchQuery, (q) => {
  if (!q.trim()) return
  const hit = toolTabs.value.find((t) => tabMatchCount(t) > 0)
  if (hit) activeToolTab.value = hit.key
})

// ---- 选中态管理 ----

const toolSet = computed(() => new Set(form.value.tools))

function isToolSelected(name: string) {
  return toolSet.value.has(name)
}

function toggleTool(name: string) {
  const idx = form.value.tools.indexOf(name)
  if (idx >= 0) form.value.tools.splice(idx, 1)
  else form.value.tools.push(name)
}

function sectionItems(sec: ToolSection): ToolItem[] {
  return sec.groups.flatMap((g) => g.items)
}

function isSectionAllSelected(sec: ToolSection) {
  const items = sectionItems(sec)
  return items.length > 0 && items.every((t) => toolSet.value.has(t.name))
}

function toggleSectionAll(sec: ToolSection) {
  const items = sectionItems(sec)
  const allSelected = isSectionAllSelected(sec)
  for (const t of items) {
    const idx = form.value.tools.indexOf(t.name)
    if (allSelected) {
      if (idx >= 0) form.value.tools.splice(idx, 1)
    } else {
      if (idx < 0) form.value.tools.push(t.name)
    }
  }
}

function sectionSelectedCount(sec: ToolSection) {
  return sectionItems(sec).filter((t) => toolSet.value.has(t.name)).length
}

function toggleMcpCollapse(name: string) {
  if (collapsedMcp.value.has(name)) collapsedMcp.value.delete(name)
  else collapsedMcp.value.add(name)
}

function isMcpCollapsed(name: string) {
  return collapsedMcp.value.has(name)
}

// ---- MCP 重连 ----

async function handleReconnect(s: McpServer) {
  mcpBusy.value = s.name
  try {
    const updated = await reconnectMcpServer(s.name)
    Object.assign(s, updated)
    if (updated.status === 'connected') ElMessage.success(`${s.name} 已连接（${updated.tool_names.length} 个工具）`)
    else ElMessage.warning(`${s.name} 连接失败`)
    // 刷新工具列表
    allTools.value = (await listTools()) || []
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || e?.message || '重连失败')
  } finally {
    mcpBusy.value = ''
  }
}

// ---- 弹窗打开时清空搜索、收起已选 ----

watch(toolDialogVisible, (open) => {
  if (open) searchQuery.value = ''
})

// ---- 搜索过滤后的全局命中数 ----

const totalFilteredTools = computed(() => {
  const seen = new Set<string>()
  for (const tab of toolTabs.value)
    for (const sec of tab.sections)
      for (const item of sectionItems(sec))
        seen.add(item.name)
  return seen.size
})

// ---- 技能选择 ----

const skillSet = computed(() => new Set(form.value.skills))
const selectAllSkills = computed({
  get: () => form.value.skills.includes('*'),
  set: (v: boolean) => {
    if (v) form.value.skills = ['*']
    else form.value.skills = []
  },
})

const filteredSkills = computed(() => {
  const q = skillSearchQuery.value.trim().toLowerCase()
  if (!q) return allSkills.value
  return allSkills.value.filter((s) =>
    `${s.name} ${s.description}`.toLowerCase().includes(q),
  )
})

function isSkillSelected(name: string) {
  return skillSet.value.has('*') || skillSet.value.has(name)
}

function toggleSkill(name: string) {
  if (skillSet.value.has('*')) return // 全选模式下不可单独取消
  const idx = form.value.skills.indexOf(name)
  if (idx >= 0) form.value.skills.splice(idx, 1)
  else form.value.skills.push(name)
}

watch(skillDialogVisible, (open) => {
  if (open) skillSearchQuery.value = ''
})

// ---- 编辑模式：回填 ----

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
        tags: [...(a.tags || [])],
      }
    } else {
      form.value = {
        name: '',
        description: '',
        system_prompt: '',
        model: '',
        tools: [],
        skills: [],
        tags: [],
      }
    }
  },
  { immediate: true },
)

const isEdit = computed(() => !!props.agent?.id)

// ---- 加载选项 ----

async function loadOptions() {
  try {
    const [m, toolsResp, skillsResp, pluginsResp, mcpResp, tagsResp] = await Promise.all([
      listModels(),
      listTools(),
      listSkills(),
      listPlugins(),
      listMcpServers(),
      listTags(),
    ])
    models.value = m
    allTools.value = toolsResp || []
    allSkills.value = (skillsResp || []).map((s) => ({
      name: s.name,
      description: s.description || '',
    }))
    allTags.value = tagsResp || []
    plugins.value = pluginsResp?.plugins || []
    mcpServers.value = mcpResp || []
    // MCP 默认折叠
    collapsedMcp.value = new Set((mcpResp || []).map((s) => s.name))
  } catch {
    // 静默失败
  }
}

loadOptions()

const modelOptions = computed(() => {
  return models.value
    .filter((m) => m.model_type === 'chat' && m.is_enabled)
    .map((m) => ({
      label: `${m.provider?.name}:${m.name}`,
      value: `${m.provider?.name}:${m.name}`,
    }))
})

// ---- 保存 ----

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

// ---- MCP 状态映射 ----

const statusMeta: Record<string, { label: string; type: 'success' | 'danger' | 'info' | 'warning' }> = {
  connected: { label: '已连接', type: 'success' },
  connecting: { label: '连接中', type: 'warning' },
  failed: { label: '连接失败', type: 'danger' },
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

      <!-- ===== 工具选择 ===== -->
      <el-form-item label="工具">
        <div class="tool-field">
          <div class="tool-tags" v-if="form.tools.length">
            <el-tag
              v-for="name in form.tools"
              :key="name"
              size="small"
              closable
              disable-transitions
              @close="toggleTool(name)"
            >{{ toolNameMap.get(name) || name }}</el-tag>
          </div>
          <el-button size="small" @click="toolDialogVisible = true">
            选择工具
            <template v-if="form.tools.length">（{{ form.tools.length }}）</template>
          </el-button>
        </div>
      </el-form-item>

      <!-- ===== 技能选择 ===== -->
      <el-form-item label="技能">
        <div class="tool-field">
          <div class="tool-tags" v-if="form.skills.length">
            <el-tag
              v-for="name in form.skills"
              :key="name"
              size="small"
              closable
              disable-transitions
              @close="form.skills.splice(form.skills.indexOf(name), 1)"
            >{{ name === '*' ? '全部技能' : name }}</el-tag>
          </div>
          <el-button size="small" @click="skillDialogVisible = true">
            选择技能
            <template v-if="form.skills.length">（{{ form.skills.includes('*') ? '全部' : form.skills.length }}）</template>
          </el-button>
        </div>
      </el-form-item>

      <!-- ===== 知识库标签 ===== -->
      <el-form-item label="知识库范围">
        <el-select
          v-model="form.tags"
          multiple
          filterable
          placeholder="选择标签以启用知识库检索"
          style="width: 100%"
        >
          <el-option
            v-for="t in allTags"
            :key="t.id"
            :label="t.name"
            :value="t.name"
          />
        </el-select>
      </el-form-item>

      <el-form-item>
        <el-button type="primary" :loading="saving" @click="handleSave">
          {{ isEdit ? '保存修改' : '创建 Agent' }}
        </el-button>
      </el-form-item>
    </el-form>

    <!-- ===== 工具选择弹窗 ===== -->
    <el-dialog
      v-model="toolDialogVisible"
      title="选择工具"
      width="min(720px, 92vw)"
      :close-on-click-modal="false"
      destroy-on-close
      top="5vh"
    >
      <div class="tool-picker">
        <!-- 搜索 + 已选汇总 -->
        <div class="tool-top-bar">
          <el-input
            v-model="searchQuery"
            placeholder="搜索工具名 / 描述"
            clearable
            size="small"
            class="tool-search-input"
          />
          <span class="tool-summary">已选 {{ form.tools.length }}</span>
        </div>

        <!-- 已选工具 -->
        <div v-if="form.tools.length" class="selected-bar">
          <el-tag
            v-for="name in form.tools"
            :key="name"
            size="small"
            closable
            disable-transitions
            @close="toggleTool(name)"
          >{{ toolNameMap.get(name) || name }}</el-tag>
        </div>

        <!-- 选项卡 -->
        <el-tabs v-model="activeToolTab" class="tool-tabs">
          <el-tab-pane
            v-for="tab in toolTabs"
            :key="tab.key"
            :name="tab.key"
          >
            <template #label>
              <span class="tab-label">
                {{ tab.label }}
                <span v-if="searchQuery.trim()" class="tab-hit">{{ tabMatchCount(tab) }}</span>
              </span>
            </template>

            <div class="tab-body">
              <template v-for="sec in tab.sections" :key="sec.key">
                <!-- MCP 段头：服务器名 + 状态 + 重连 -->
                <div v-if="sec.server" class="mcp-server-head collapsible" @click="toggleMcpCollapse(sec.server.name)">
                  <svg class="chev" :class="{ open: !isMcpCollapsed(sec.server.name) }" width="10" height="10" viewBox="0 0 10 10" aria-hidden="true">
                    <path d="M3 2 L7 5 L3 8" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round" />
                  </svg>
                  <span class="mcp-server-name">{{ sec.title }}</span>
                  <el-tag size="small" :type="statusMeta[sec.server.status]?.type ?? 'info'" disable-transitions>
                    {{ statusMeta[sec.server.status]?.label ?? sec.server.status }}
                  </el-tag>
                  <el-button
                    v-if="sec.server.status !== 'connected'"
                    class="btn-soft"
                    size="small"
                    :loading="mcpBusy === sec.server.name"
                    @click="handleReconnect(sec.server)"
                  >重连</el-button>
                  <span class="section-count">
                    {{ sectionSelectedCount(sec) }}/{{ sectionItems(sec).length }}
                  </span>
                  <el-button
                    v-if="sectionItems(sec).length"
                    class="btn-soft"
                    size="small"
                    @click.stop="toggleSectionAll(sec)"
                  >
                    {{ isSectionAllSelected(sec) ? '取消' : '全选' }}
                  </el-button>
                </div>
                <template v-if="sec.server && !isMcpCollapsed(sec.server.name)">
                  <div v-if="sec.server?.error" class="section-error">{{ sec.server.error }}</div>
                </template>

                <!-- 非 MCP 段：段头含全选（内置段不显示）-->
                <div v-if="!sec.server && sec.key !== 'builtin' && sectionItems(sec).length" class="section-head-inline">
                  <span class="section-count">
                    {{ sectionSelectedCount(sec) }}/{{ sectionItems(sec).length }}
                  </span>
                  <el-button class="btn-soft" size="small" @click="toggleSectionAll(sec)">
                    {{ isSectionAllSelected(sec) ? '取消' : '全选' }}
                  </el-button>
                </div>

                <!-- 工具列表 -->
                <template v-for="group in sec.groups" :key="group.groupName">
                  <div v-if="tab.key === 'script'" class="group-label">{{ group.groupName }}</div>
                  <div v-if="!sec.server || !isMcpCollapsed(sec.server.name)" class="tool-list">
                    <label
                      v-for="t in group.items"
                      :key="t.name"
                      class="tool-row"
                      :class="{ on: isToolSelected(t.name) }"
                    >
                      <input
                        type="checkbox"
                        :checked="isToolSelected(t.name)"
                        @change="toggleTool(t.name)"
                      />
                      <span class="tool-name">
                        {{ t.label }}
                      </span>
                      <span v-if="t.description" class="tool-desc">{{ t.description }}</span>
                    </label>
                  </div>
                </template>

                <div v-if="!sectionItems(sec).length && (!sec.server || !isMcpCollapsed(sec.server.name))" class="section-empty muted">
                  {{ searchQuery.trim() ? '无匹配工具' : (sec.server && sec.server.status !== 'connected' ? '服务器未连接' : '无可用工具') }}
                </div>
              </template>
            </div>
          </el-tab-pane>
        </el-tabs>
      </div>

      <template #footer>
        <el-button @click="toolDialogVisible = false">完成</el-button>
      </template>
    </el-dialog>

    <!-- ===== 技能选择弹窗 ===== -->
    <el-dialog
      v-model="skillDialogVisible"
      title="选择技能"
      width="min(560px, 92vw)"
      :close-on-click-modal="false"
      destroy-on-close
      top="10vh"
    >
      <div class="skill-picker">
        <div class="tool-top-bar">
          <el-input
            v-model="skillSearchQuery"
            placeholder="搜索技能名 / 描述"
            clearable
            size="small"
            class="tool-search-input"
          />
          <span class="tool-summary">
            已选 {{ form.skills.includes('*') ? '全部' : form.skills.length }}
          </span>
        </div>

        <!-- 全部技能开关 -->
        <label class="skill-row skill-row-all" :class="{ on: selectAllSkills }">
          <input type="checkbox" v-model="selectAllSkills" />
          <span class="tool-name">全部技能</span>
          <span class="tool-desc">加载所有可用技能的目录</span>
        </label>

        <div class="skill-divider" />

        <!-- 技能列表 -->
        <div class="skill-list">
          <label
            v-for="s in filteredSkills"
            :key="s.name"
            class="tool-row"
            :class="{ on: isSkillSelected(s.name), disabled: selectAllSkills }"
          >
            <input
              type="checkbox"
              :checked="isSkillSelected(s.name)"
              :disabled="selectAllSkills"
              @change="toggleSkill(s.name)"
            />
            <span class="tool-name">{{ s.name }}</span>
            <span v-if="s.description" class="tool-desc">{{ s.description }}</span>
          </label>
          <div v-if="!filteredSkills.length" class="section-empty muted">
            {{ skillSearchQuery.trim() ? '无匹配技能' : '无可用技能' }}
          </div>
        </div>
      </div>

      <template #footer>
        <el-button @click="skillDialogVisible = false">完成</el-button>
      </template>
    </el-dialog>

  </div>
</template>

<style scoped>
.agent-form {
  padding: 16px 0;
}

/* ---- 工具字段（表单内） ---- */

.tool-field {
  width: 100%;
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
}

.tool-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
}

/* ---- 工具选择器（弹窗内） ---- */

.tool-picker {
  display: flex;
  flex-direction: column;
  gap: 0;
}

.tool-top-bar {
  display: flex;
  align-items: center;
  gap: 10px;
  padding-bottom: 10px;
}

.tool-search-input {
  flex: 1;
}

.tool-summary {
  font-size: 12px;
  color: var(--el-text-color-secondary);
  white-space: nowrap;
}

.selected-bar {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  padding: 8px 10px;
  margin-bottom: 10px;
  background: rgba(255, 255, 255, 0.03);
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 6px;
  max-height: 80px;
  overflow-y: auto;
}

.tool-tabs {
  flex: 1;
  min-height: 0;
}

.tool-tabs :deep(.el-tabs__header) {
  margin: 0;
}

.tool-tabs :deep(.el-tabs__content) {
  padding: 0;
}

.tab-label {
  display: inline-flex;
  align-items: center;
  gap: 4px;
}

.tab-hit {
  font-family: var(--el-font-family);
  font-size: 10px;
  color: var(--el-text-color-secondary);
  background: rgba(255, 255, 255, 0.06);
  padding: 0 5px;
  border-radius: 999px;
  line-height: 1.6;
}

.tab-body {
  max-height: 50vh;
  overflow-y: auto;
  padding: 8px 0;
}

/* ---- MCP 段头 ---- */

.mcp-server-head {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 0;
}
.mcp-server-head.collapsible {
  cursor: pointer;
  user-select: none;
}

.mcp-server-name {
  font-size: 13px;
  font-weight: 600;
  color: var(--el-text-color-primary);
  font-family: var(--el-font-family);
}

/* ---- 非 MCP 段头（全选行） ---- */

.section-head-inline {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 4px 0;
}

.section-count {
  font-family: var(--el-font-family);
  font-size: 11px;
  color: var(--el-text-color-secondary);
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}

.btn-soft {
  --el-button-bg-color: rgba(255, 255, 255, 0.06);
  --el-button-border-color: var(--el-border-color);
  --el-button-text-color: var(--el-text-color-secondary);
  --el-button-hover-bg-color: rgba(255, 255, 255, 0.1);
  --el-button-hover-border-color: rgba(255, 255, 255, 0.18);
  --el-button-hover-text-color: var(--el-text-color-primary);
}

.chev {
  flex: none;
  color: var(--el-text-color-placeholder);
  transition: transform 0.15s;
}
.chev.open {
  transform: rotate(90deg);
}

/* ---- 工具列表 ---- */

.section-body {
  padding: 2px 0;
}

.group-label {
  font-size: 11px;
  color: var(--el-text-color-placeholder);
  margin: 2px 0 0;
  padding: 0 12px 0 32px;
  font-family: var(--el-font-family);
}

.tool-list {
  display: flex;
  flex-direction: column;
}

.tool-row {
  display: flex;
  align-items: baseline;
  gap: 8px;
  padding: 4px 12px 4px 32px;
  cursor: pointer;
  transition: background 0.12s;
  min-height: 30px;
}
.tool-row:hover {
  background: rgba(255, 255, 255, 0.03);
}
.tool-row.on {
  background: rgba(77, 196, 178, 0.06);
}
.tool-row.builtin {
  opacity: 0.85;
  cursor: default;
}

.tool-row input[type="checkbox"] {
  flex-shrink: 0;
  margin: 0;
  accent-color: var(--el-color-primary);
}

.tool-name {
  font-size: 12px;
  font-weight: 500;
  color: var(--el-text-color-primary);
  white-space: nowrap;
  flex-shrink: 0;
}

.tool-desc {
  font-size: 11px;
  color: var(--el-text-color-placeholder);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  min-width: 0;
}

.section-error {
  font-size: 11px;
  color: var(--el-color-danger);
  padding: 4px 12px 4px 32px;
  font-family: var(--el-font-family);
}

.section-empty {
  font-size: 11px;
  padding: 4px 12px 4px 32px;
}

.muted {
  color: var(--el-text-color-placeholder);
}

/* ---- 技能选择器 ---- */

.skill-picker {
  display: flex;
  flex-direction: column;
  gap: 0;
}

.skill-row-all {
  padding: 6px 12px;
}

.skill-divider {
  height: 1px;
  background: var(--el-border-color-lighter);
  margin: 4px 0;
}

.skill-list {
  max-height: 40vh;
  overflow-y: auto;
}

.tool-row.disabled {
  opacity: 0.5;
  cursor: default;
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
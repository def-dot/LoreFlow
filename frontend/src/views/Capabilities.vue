<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { listNodeTypes, type NodeTypeInfo } from '@/api/nodeTypes'
import {
  listTools, listSkills,
  deleteSkill, readSkillFile, downloadSkill,
  type ToolOut, type SkillDef,
} from '@/api/registry'
import { listPlugins, uploadPlugin, deletePlugin, type PluginInfo } from '@/api/plugins'
import {
  listMcpServers, reconnectMcpServer, reconnectAllMcpServers,
  getMcpServerConfig, deleteMcpServer,
  type McpServer, type McpServerConfig,
} from '@/api/mcp'
import NodeTypeCard from '@/components/NodeTypeCard.vue'
import McpServerForm from '@/components/McpServerForm.vue'
import SkillForm from '@/components/SkillForm.vue'

type TabName = 'catalog' | 'skills'
type RoleFilter = 'all' | 'node' | 'tool'

const activeTab = ref<TabName>('catalog')
const nodeTypes = ref<NodeTypeInfo[]>([])
const tools = ref<ToolOut[]>([])
const skills = ref<SkillDef[]>([])
const plugins = ref<PluginInfo[]>([])
const mcpServers = ref<McpServer[]>([])
const loading = ref(false)
const loadError = ref(false)
const uploading = ref(false)
const fileInput = ref<HTMLInputElement | null>(null)
const guideOpen = ref(false)
const mcpBusy = ref('')

const roleFilter = ref<RoleFilter>('all')
const searchQuery = ref('')

/** 名录条目：节点与工具并集（双端注册的 FuncDef 只出现一次） */
interface SourceInfo { kind: 'builtin' | 'plugin' | 'mcp' | 'pipeline'; name: string }

interface CatalogItem {
  name: string
  label: string
  description: string
  metadata?: Record<string, any>
  input_schema?: NodeTypeInfo['input_schema']
  output_schema?: NodeTypeInfo['output_schema']
  roles?: string[]
  source?: SourceInfo
}

function catalogKey(t: { name: string; source?: SourceInfo }): string {
  return `${t.name}::${t.source?.kind ?? 'builtin'}::${t.source?.name ?? ''}`
}

/** 一张名录：REGISTRY 与 TOOL_REGISTRY 的并集，source / roles 由前端推算 */
const catalogItems = computed<CatalogItem[]>(() => {
  // 从 plugins / mcpServers 构建 name → source 索引
  const srcMap = new Map<string, SourceInfo>()
  for (const p of plugins.value)
    for (const n of p.node_names) srcMap.set(n, { kind: 'plugin', name: p.filename })
  for (const p of plugins.value)
    for (const n of p.tool_names) srcMap.set(n, { kind: 'plugin', name: p.filename })
  for (const s of mcpServers.value)
    for (const n of s.tool_names) srcMap.set(n, { kind: 'mcp', name: s.name })
  for (const t of tools.value)
    if (t.metadata?.source === 'pipeline') srcMap.set(t.name, { kind: 'pipeline', name: t.name })

  const toolSet = new Set(tools.value.map((t) => t.name))
  const map = new Map<string, CatalogItem>()
  for (const t of nodeTypes.value) {
    const roles: string[] = toolSet.has(t.name) ? ['node', 'tool'] : ['node']
    map.set(catalogKey(t), { ...t, roles, source: srcMap.get(t.name) })
  }
  for (const t of tools.value) {
    const k = catalogKey(t)
    if (map.has(k)) continue
    map.set(k, { ...t, roles: ['tool'], source: srcMap.get(t.name) })
  }
  return [...map.values()]
})

function hasRole(t: CatalogItem, role: string): boolean {
  return (t.roles ?? []).includes(role)
}

const roleCounts = computed(() => ({
  all: catalogItems.value.length,
  node: catalogItems.value.filter((t) => hasRole(t, 'node')).length,
  tool: catalogItems.value.filter((t) => hasRole(t, 'tool')).length,
}))

const filteredItems = computed(() => {
  const q = searchQuery.value.trim().toLowerCase()
  return catalogItems.value.filter((t) => {
    if (roleFilter.value !== 'all' && !hasRole(t, roleFilter.value)) return false
    if (q) {
      const hay = `${t.name} ${t.label} ${t.description}`.toLowerCase()
      if (!hay.includes(q)) return false
    }
    return true
  })
})

/**
 * 分组：内置跟 metadata.group（域分组）；脚本按文件、MCP 按服务器。
 * 脚本/MCP 是装载单位，管理动作（删除/重连/启停）就挂在组头上。
 */
interface CatalogGroup {
  key: string
  name: string
  items: CatalogItem[]
  kind: 'domain' | 'script' | 'mcp'
  plugin?: PluginInfo
  server?: McpServer
}

function domainGroupOf(t: CatalogItem): string {
  return t.metadata?.group || '其他'
}

const catalogGroups = computed<CatalogGroup[]>(() => {
  const bySource = (kind: string, name: string) =>
    filteredItems.value.filter((t) => t.source?.kind === kind && t.source?.name === name)

  // 内置：域分组
  const domainMap = new Map<string, CatalogItem[]>()
  for (const t of filteredItems.value) {
    if (t.source?.kind === 'plugin' || t.source?.kind === 'mcp' || t.source?.kind === 'pipeline') continue
    const g = domainGroupOf(t)
    if (!domainMap.has(g)) domainMap.set(g, [])
    domainMap.get(g)!.push(t)
  }
  const sortItems = (items: CatalogItem[]) =>
    items.sort((a, b) => {
      const oa = a.metadata?.order ?? 999
      const ob = b.metadata?.order ?? 999
      return oa !== ob ? oa - ob : a.name.localeCompare(b.name)
    })
  const domains: CatalogGroup[] = [...domainMap.entries()].map(([name, items]) => ({
    key: `domain::${name}`,
    name,
    items: sortItems(items),
    kind: 'domain' as const,
  }))

  // 自定义脚本：按文件一组（含 0 条目的，管理入口不能消失）
  const scripts: CatalogGroup[] = plugins.value.map((p) => ({
    key: `script::${p.filename}`,
    name: p.filename,
    items: sortItems(bySource('plugin', p.filename)),
    kind: 'script' as const,
    plugin: p,
  }))

  // MCP：按服务器一组
  const mcp: CatalogGroup[] = mcpServers.value.map((s) => ({
    key: `mcp::${s.name}`,
    name: s.name,
    items: sortItems(bySource('mcp', s.name)),
    kind: 'mcp' as const,
    server: s,
  }))

  // 工作流
  const workflows: CatalogGroup[] = [{
    key: 'workflow::all',
    name: '工作流',
    items: sortItems(filteredItems.value.filter((t) => t.source?.kind === 'pipeline')),
    kind: 'workflow' as const,
  }]

  return [...domains, ...scripts, ...mcp, ...workflows]
})

/** MCP 组默认折叠（Notion 一来就是 24 个工具）；搜索时自动展开命中所在的组 */
const expandedGroups = ref<Set<string>>(new Set())

function isGroupOpen(g: CatalogGroup): boolean {
  if (g.kind === 'domain' || g.kind === 'script') return true
  if (searchQuery.value.trim()) return true
  return expandedGroups.value.has(g.key)
}

function toggleGroup(g: CatalogGroup) {
  if (g.kind !== 'mcp') return
  const next = new Set(expandedGroups.value)
  if (next.has(g.key)) next.delete(g.key)
  else next.add(g.key)
  expandedGroups.value = next
}

const filtersActive = computed(() => roleFilter.value !== 'all' || searchQuery.value.trim() !== '')

/** 四个区默认折叠，展开只影响显示不改数据 */
const collapsedSections = ref<Set<string>>(new Set(['builtin', 'workflows', 'scripts', 'mcp']))

function isSectionCollapsed(key: string): boolean {
  return collapsedSections.value.has(key)
}

function toggleSection(key: string) {
  const next = new Set(collapsedSections.value)
  if (next.has(key)) next.delete(key)
  else next.add(key)
  collapsedSections.value = next
}

const builtinCount = computed(
  () => filteredItems.value.filter((t) => !t.source?.kind || t.source.kind === 'builtin').length,
)
const pluginItemCount = computed(
  () => filteredItems.value.filter((t) => t.source?.kind === 'plugin').length,
)
const mcpItemCount = computed(
  () => filteredItems.value.filter((t) => t.source?.kind === 'mcp').length,
)
const workflowItemCount = computed(
  () => filteredItems.value.filter((t) => t.source?.kind === 'pipeline').length,
)

function resetFilters() {
  roleFilter.value = 'all'
  searchQuery.value = ''
}

/** 技能正文默认折叠 */
const openSkills = ref<Set<string>>(new Set())

/** 选中的文件：skill name → file path */
const selectedFile = ref<Record<string, string>>({})
/** 文件内容缓存：skill name → content */
const fileContent = ref<Record<string, string>>({})

interface FileTreeItem {
  name: string
  path: string
  isDir: boolean
  connector: string  // "├── " / "└── " / "│   ├── " / "    └── "
}

/** 将平铺的文件路径列表转成目录树条目 */
function buildFileTree(files: string[]): FileTreeItem[] {
  if (!files?.length) return []
  interface TreeNode { [key: string]: TreeNode }
  const root: TreeNode = {}
  for (const f of files) {
    const parts = f.split('/')
    let node = root
    for (const p of parts) {
      if (!node[p]) node[p] = {}
      node = node[p]
    }
  }
  const items: FileTreeItem[] = []
  function walk(node: TreeNode, prefix: string, dirPath: string) {
    const keys = Object.keys(node)
    keys.forEach((key, i) => {
      const isLast = i === keys.length - 1
      const connector = prefix + (isLast ? '└── ' : '├── ')
      const childPrefix = prefix + (isLast ? '    ' : '│   ')
      const isDir = Object.keys(node[key]).length > 0
      const fullPath = dirPath ? dirPath + '/' + key : key
      items.push({ name: key, path: fullPath, isDir, connector })
      if (isDir) walk(node[key], childPrefix, fullPath)
    })
  }
  walk(root, '', '')
  return items
}

/** 点击文件：加载内容 */
async function handleFileClick(skillName: string, filePath: string) {
  const cur = selectedFile.value[skillName]
  if (cur === filePath) {
    // 取消选中
    selectedFile.value = { ...selectedFile.value, [skillName]: '' }
    return
  }
  selectedFile.value = { ...selectedFile.value, [skillName]: filePath }
  // 已有缓存则不重复请求
  const cacheKey = `${skillName}::${filePath}`
  if (fileContent.value[cacheKey]) return
  try {
    const content = await readSkillFile(skillName, filePath)
    fileContent.value = { ...fileContent.value, [cacheKey]: content }
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.msg || e?.message || '读取文件失败')
  }
}

/** 树宽度调整 */
const treeWidths = ref<Record<string, number>>({})

function startTreeResize(e: MouseEvent, skillName: string) {
  const startX = e.clientX
  const startW = treeWidths.value[skillName] || 200
  const onMove = (ev: MouseEvent) => {
    const w = Math.max(120, Math.min(400, startW + ev.clientX - startX))
    treeWidths.value = { ...treeWidths.value, [skillName]: w }
  }
  const onUp = () => {
    document.removeEventListener('mousemove', onMove)
    document.removeEventListener('mouseup', onUp)
    document.body.style.cursor = ''
    document.body.style.userSelect = ''
  }
  document.addEventListener('mousemove', onMove)
  document.addEventListener('mouseup', onUp)
  document.body.style.cursor = 'col-resize'
  document.body.style.userSelect = 'none'
}

function toggleSkill(name: string) {
  const next = new Set(openSkills.value)
  if (next.has(name)) next.delete(name)
  else next.add(name)
  openSkills.value = next
}

async function fetchAll() {
  loading.value = true
  loadError.value = false
  try {
    const [n, t, s, p, m] = await Promise.all([
      listNodeTypes(), listTools(), listSkills(), listPlugins(), listMcpServers(),
    ])
    nodeTypes.value = n ?? []
    tools.value = t ?? []
    skills.value = s ?? []
    plugins.value = p.plugins ?? []
    mcpServers.value = m ?? []
  } catch {
    loadError.value = true
  } finally {
    loading.value = false
  }
}

/** 插件增删后只刷新受影响的三项 */
async function refreshAfterPluginChange() {
  const [n, t, p] = await Promise.all([listNodeTypes(), listTools(), listPlugins()])
  nodeTypes.value = n ?? []
  tools.value = t ?? []
  plugins.value = p.plugins ?? []
}

/** MCP 增删改后只刷新 mcpServers 和 tools */
async function refreshAfterMcpChange() {
  const [t, m] = await Promise.all([listTools(), listMcpServers()])
  tools.value = t ?? []
  mcpServers.value = m ?? []
}

async function handleUpload(file: File) {
  uploading.value = true
  try {
    const plugin = await uploadPlugin(file)
    ElMessage.success(`脚本 ${plugin.filename} 上传成功`)
    await refreshAfterPluginChange()
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || e?.message || '上传失败')
  } finally {
    uploading.value = false
  }
}

function onFileChange(e: Event) {
  const input = e.target as HTMLInputElement
  const file = input.files?.[0]
  if (file) handleUpload(file)
  input.value = ''
}

async function handleDeletePlugin(p: PluginInfo) {
  try {
    await ElMessageBox.confirm(`确定删除脚本 ${p.filename}？删除后它注册的节点和工具会一并移除。`, '删除脚本', {
      type: 'warning',
      confirmButtonText: '删除',
      cancelButtonText: '取消',
    })
    await deletePlugin(p.filename)
    ElMessage.success(`脚本 ${p.filename} 已删除`)
    await refreshAfterPluginChange()
  } catch (e: any) {
    if (e !== 'cancel') ElMessage.error(e?.response?.data?.detail || e?.message || '删除失败')
  }
}

async function handleReconnect(s: McpServer) {
  mcpBusy.value = s.name
  try {
    const updated = await reconnectMcpServer(s.name)
    Object.assign(s, updated)
    if (updated.status === 'connected') ElMessage.success(`${s.name} 已连接（${updated.tool_names.length} 个工具）`)
    else ElMessage.warning(`${s.name} 连接失败`)
    await refreshAfterMcpChange()
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || e?.message || '重连失败')
  } finally {
    mcpBusy.value = ''
  }
}


async function handleReconnectAll() {
  mcpBusy.value = '__all__'
  try {
    mcpServers.value = await reconnectAllMcpServers()
    ElMessage.success('已重连全部服务器')
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || e?.message || '重连失败')
  } finally {
    mcpBusy.value = ''
  }
}

// ---- MCP 配置：新建 / 编辑 / 删除（写回 mcp.json）----
const mcpFormOpen = ref(false)
const mcpEditing = ref<McpServerConfig | null>(null)

function openMcpCreate() {
  mcpEditing.value = null
  mcpFormOpen.value = true
}

async function openMcpEdit(s: McpServer) {
  try {
    mcpEditing.value = await getMcpServerConfig(s.name)
    mcpFormOpen.value = true
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.msg || e?.message || '读取配置失败')
  }
}

async function handleDeleteMcp(s: McpServer) {
  try {
    await ElMessageBox.confirm(`确定删除 MCP 服务器「${s.name}」？删除后它提供的工具将不可用。`, '删除 MCP 服务器', {
      type: 'warning',
      confirmButtonText: '删除',
      cancelButtonText: '取消',
    })
  } catch {
    return
  }
  try {
    await deleteMcpServer(s.name)
    ElMessage.success(`已删除 ${s.name}`)
    await refreshAfterMcpChange()
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.msg || e?.message || '删除失败')
  }
}

async function onMcpSaved() {
  mcpFormOpen.value = false
  mcpEditing.value = null
  await refreshAfterMcpChange()
}

// ---- 技能包：新建 / 编辑 / 删除 ----
const skillFormOpen = ref(false)
const skillEditing = ref<SkillDef | null>(null)
const skillGuideOpen = ref(false)

function openSkillCreate() {
  skillEditing.value = null
  skillFormOpen.value = true
}

function openSkillEdit(s: SkillDef) {
  skillEditing.value = s
  skillFormOpen.value = true
}

async function onSkillSaved() {
  skillFormOpen.value = false
  skillEditing.value = null
  skills.value = (await listSkills()) ?? []
}

async function handleDeleteSkill(s: SkillDef) {
  try {
    await ElMessageBox.confirm(`确定删除技能「${s.name}」？`, '删除技能', {
      type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消',
    })
  } catch { return }
  try {
    await deleteSkill(s.name)
    ElMessage.success(`已删除 ${s.name}`)
    skills.value = (await listSkills()) ?? []
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.msg || e?.message || '删除失败')
  }
}

async function handleDownloadSkill(s: SkillDef) {
  try {
    const blob = await downloadSkill(s.name)
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `${s.name}.zip`
    a.click()
    URL.revokeObjectURL(url)
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.msg || e?.message || '下载失败')
  }
}

const statusMeta: Record<string, { label: string; type: 'success' | 'danger' | 'info' | 'warning' }> = {
  connected: { label: '已连接', type: 'success' },
  connecting: { label: '连接中', type: 'warning' },
  failed: { label: '连接失败', type: 'danger' },
}

onMounted(fetchAll)
</script>

<template>
  <div class="page">
    <header class="page-head">
      <div class="head-info">
        <h1>能力目录</h1>
        <span class="muted">工作流可引用的节点、Agent 可调用的工具/技能，以及扩展的装卸状态。</span>
      </div>
      <span v-if="loadError" class="load-error">加载失败，请检查后端是否可用</span>
    </header>

    <el-tabs v-model="activeTab" class="cap-tabs">
      <!-- ==================== 节点与工具 ==================== -->
      <el-tab-pane label="节点与工具" name="catalog">
        <p class="lead">
          节点供工作流 YAML 以 <code>type:</code> 引用，工具供智能体 function calling 调用。同一个函数可以两端都注册。
        </p>

        <!-- 筛选：用途 chip + 搜索 -->
        <div class="filter-bar">
          <div class="chip-group">
            <button class="chip" :class="{ on: roleFilter === 'all' }" @click="roleFilter = 'all'">
              全部 <span class="chip-n">{{ roleCounts.all }}</span>
            </button>
            <button class="chip" :class="{ on: roleFilter === 'node' }" @click="roleFilter = 'node'">
              节点 <span class="chip-n">{{ roleCounts.node }}</span>
            </button>
            <button class="chip" :class="{ on: roleFilter === 'tool' }" @click="roleFilter = 'tool'">
              工具 <span class="chip-n">{{ roleCounts.tool }}</span>
            </button>
          </div>
          <el-input
            v-model="searchQuery"
            class="search"
            placeholder="搜索 名称/描述"
            clearable
            :prefix-icon="() => null"
          />
        </div>

        <!-- 名录 -->
        <div v-loading="loading">
          <!-- 内置：随代码走，不可装卸 -->
          <div class="top-section">
            <div class="source-head">
              <h3 class="source-title" @click="toggleSection('builtin')">
                <svg class="chev" :class="{ open: !isSectionCollapsed('builtin') }" width="10" height="10" viewBox="0 0 10 10" aria-hidden="true">
                  <path d="M3 2 L7 5 L3 8" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round" />
                </svg>
                内置
                <span class="group-count">{{ builtinCount }}</span>
              </h3>
            </div>
            <template v-if="!isSectionCollapsed('builtin')">
              <div v-for="g in catalogGroups.filter((x) => x.kind === 'domain')" :key="g.key" class="group-section">
                <h3 class="group-title">
                  <span class="group-name">{{ g.name }}</span>
                  <span class="group-count">{{ g.items.length }}</span>
                </h3>
                <div class="node-grid">
                  <NodeTypeCard v-for="t in g.items" :key="catalogKey(t)" :node="t" variant="func" />
                </div>
              </div>
              <div v-if="!catalogGroups.some((x) => x.kind === 'domain')" class="group-empty muted">无内置条目</div>
            </template>
          </div>

          <!-- 工作流 -->
          <div class="top-section">
            <div class="section-head">
              <h3 class="source-title" @click="toggleSection('workflows')">
                <svg class="chev" :class="{ open: !isSectionCollapsed('workflows') }" width="10" height="10" viewBox="0 0 10 10" aria-hidden="true">
                  <path d="M3 2 L7 5 L3 8" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round" />
                </svg>
                工作流
                <span class="group-count">{{ workflowItemCount }}</span>
              </h3>
            </div>
            <template v-if="!isSectionCollapsed('workflows')">
              <div v-for="g in catalogGroups.filter((x) => x.kind === 'workflow')" :key="g.key" class="group-section">
                <div v-if="g.items.length" class="node-grid">
                  <NodeTypeCard v-for="t in g.items" :key="catalogKey(t)" :node="t" variant="func" />
                </div>
                <div v-else class="group-empty muted">无工作流工具</div>
              </div>
            </template>
          </div>

          <!-- 自定义脚本：按文件一组，管理挂在组头 -->
          <div class="top-section">
            <div class="source-head">
              <h3 class="source-title" @click="toggleSection('scripts')">
                <svg class="chev" :class="{ open: !isSectionCollapsed('scripts') }" width="10" height="10" viewBox="0 0 10 10" aria-hidden="true">
                  <path d="M3 2 L7 5 L3 8" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round" />
                </svg>
                自定义脚本
                <span class="group-count">{{ pluginItemCount }}</span>
              </h3>
              <input ref="fileInput" type="file" accept=".py" hidden @change="onFileChange" />
              <el-button type="primary" size="small" :loading="uploading" @click="fileInput?.click()">上传脚本</el-button>
              <span class="guide-link" @click="guideOpen = true">编写指南</span>
            </div>
            <template v-if="!isSectionCollapsed('scripts')">
            <p class="source-note">用 <code>@func</code> 写的 <code>.py</code> 文件 · 上传后自动热加载，函数会成为节点和工具</p>

            <div v-for="g in catalogGroups.filter((x) => x.kind === 'script')" :key="g.key" class="group-section">
              <h3 class="group-title with-actions">
                <span class="group-name mono">{{ g.name }}</span>
                <el-tag v-if="g.plugin?.error" type="danger" size="small" disable-transitions>加载失败</el-tag>
                <el-tag v-else type="success" size="small" disable-transitions>正常</el-tag>
                <span class="group-count">节点 {{ g.plugin?.node_names.length ?? 0 }} · 工具 {{ g.plugin?.tool_names?.length ?? 0 }}</span>
                <span class="group-actions">
                  <el-button class="btn-soft btn-soft--danger" size="small" @click.stop="g.plugin && handleDeletePlugin(g.plugin)">删除</el-button>
                </span>
              </h3>
              <div v-if="g.plugin?.error" class="group-error">{{ g.plugin.error }}</div>
              <div v-if="g.items.length" class="node-grid">
                <NodeTypeCard v-for="t in g.items" :key="catalogKey(t)" :node="t" variant="plugin" />
              </div>
              <div v-else-if="!g.plugin?.error" class="group-empty muted">无注册条目</div>
            </div>
            <div v-if="!plugins.length" class="muted source-empty">暂无自定义脚本</div>
            </template>
          </div>

          <!-- MCP 服务器：按服务器一组 -->
          <div class="top-section">
            <div class="source-head">
              <h3 class="source-title" @click="toggleSection('mcp')">
                <svg class="chev" :class="{ open: !isSectionCollapsed('mcp') }" width="10" height="10" viewBox="0 0 10 10" aria-hidden="true">
                  <path d="M3 2 L7 5 L3 8" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round" />
                </svg>
                MCP 服务器
                <span class="group-count">{{ mcpItemCount }}</span>
              </h3>
              <el-button type="primary" size="small" @click="openMcpCreate">新建</el-button>
              <el-button class="btn-soft" size="small" :loading="mcpBusy === '__all__'" @click="handleReconnectAll">全部重连</el-button>
            </div>
            <template v-if="!isSectionCollapsed('mcp')">
            <p class="source-note">接入外部工具服务，供智能体调用 · 新建 / 编辑 / 删除 / 启停即时生效</p>

            <div v-for="g in catalogGroups.filter((x) => x.kind === 'mcp')" :key="g.key" class="group-section">
              <h3 class="group-title with-actions collapsible" @click="toggleGroup(g)">
                <svg class="chev" :class="{ open: isGroupOpen(g) }" width="10" height="10" viewBox="0 0 10 10" aria-hidden="true">
                  <path d="M3 2 L7 5 L3 8" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round" />
                </svg>
                <span class="group-name mono">{{ g.name }}</span>
                <el-tag size="small" class="transport-tag" disable-transitions>{{ g.server?.transport }}</el-tag>
                <el-tag size="small" :type="statusMeta[g.server?.status ?? '']?.type ?? 'info'" disable-transitions>
                  {{ statusMeta[g.server?.status ?? '']?.label ?? g.server?.status }}
                </el-tag>
                <span class="group-count">{{ g.items.length }} 个工具</span>
                <span class="group-actions" @click.stop>
                  <el-button
                    v-if="g.server && g.server.status !== 'connected'"
                    class="btn-soft"
                    size="small"
                    :loading="mcpBusy === g.name"
                    @click="handleReconnect(g.server)"
                  >重连</el-button>
                  <el-button class="btn-soft" size="small" @click="g.server && openMcpEdit(g.server)">编辑</el-button>
                  <el-button class="btn-soft btn-soft--danger" size="small" @click="g.server && handleDeleteMcp(g.server)">删除</el-button>
                </span>
              </h3>
              <div v-if="g.server?.error" class="group-error">{{ g.server.error }}</div>
              <div v-if="isGroupOpen(g) && g.items.length" class="node-grid">
                <NodeTypeCard v-for="t in g.items" :key="catalogKey(t)" :node="t" variant="func" />
              </div>
              <div v-else-if="isGroupOpen(g) && !g.items.length" class="group-empty muted">
                无可用工具
              </div>
            </div>
            <div v-if="!mcpServers.length" class="muted source-empty">未配置 MCP 服务器</div>
            </template>
          </div>

          <div v-if="!filteredItems.length && !loading" class="empty">
            <template v-if="filtersActive">
              没有匹配的条目
              <el-button size="small" text type="primary" @click="resetFilters">清除筛选</el-button>
            </template>
            <template v-else>暂无节点与工具</template>
          </div>
        </div>
      </el-tab-pane>

      <!-- ==================== 技能包 ==================== -->
      <el-tab-pane label="技能包" name="skills">
        <div class="section-head">
          <span class="muted"><code>skills/</code> · 目录含 SKILL.md 即一个技能 · agent 经 <code>load_skill</code> 按需加载</span>
          <span>
            <el-button size="small" type="primary" @click="openSkillCreate">添加技能</el-button>
            <span class="guide-link" @click="skillGuideOpen = true">编写指南</span>
          </span>
        </div>
        <div v-loading="loading" class="plugin-list">
          <div v-for="s in skills" :key="s.name" class="plugin-card clickable" @click="toggleSkill(s.name)">
            <div class="plugin-head">
              <svg class="chev" :class="{ open: openSkills.has(s.name) }" width="10" height="10" viewBox="0 0 10 10" aria-hidden="true">
                <path d="M3 2 L7 5 L3 8" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round" />
              </svg>
              <div class="plugin-info">
                <span class="plugin-filename">{{ s.name }}</span>
              </div>
              <span class="group-actions" @click.stop>
                <el-button class="btn-soft" size="small" @click="openSkillEdit(s)">编辑</el-button>
                <el-button class="btn-soft" size="small" @click="handleDownloadSkill(s)">下载</el-button>
                <el-button class="btn-soft btn-soft--danger" size="small" @click="handleDeleteSkill(s)">删除</el-button>
              </span>
            </div>
            <div class="muted plugin-desc">{{ s.description }}</div>
            <div v-if="openSkills.has(s.name)" class="skill-content" @click.stop>
              <!-- 无附加文件：直接显示 SKILL.md -->
              <template v-if="!s.files || s.files.length <= 1">
                <pre v-if="s.content" class="skill-body skill-body--standalone">{{ s.content }}</pre>
                <span v-else class="muted plugin-desc">（无正文）</span>
              </template>
              <!-- 有附加文件：左树右内容 -->
              <div v-else class="skill-split">
                <div class="skill-tree" :style="{ flex: `0 0 ${treeWidths[s.name] || 200}px` }">
                  <div
                    v-for="item in buildFileTree(s.files)"
                    :key="item.path"
                    class="tree-item"
                    :class="{ dir: item.isDir, file: !item.isDir, active: (selectedFile[s.name] || 'SKILL.md') === item.path }"
                    @click="!item.isDir && handleFileClick(s.name, item.path)"
                  >
                    <span class="tree-connector">{{ item.connector }}</span>
                    <span class="tree-name" :title="item.path">{{ item.name }}{{ item.isDir ? '/' : '' }}</span>
                  </div>
                </div>
                <div class="skill-divider" @mousedown.prevent="startTreeResize($event, s.name)"></div>
                <div class="skill-viewer">
                  <template v-if="(selectedFile[s.name] || 'SKILL.md') === 'SKILL.md'">
                    <pre v-if="s.content" class="skill-body">{{ s.content }}</pre>
                    <span v-else class="muted plugin-desc">（无正文）</span>
                  </template>
                  <template v-else>
                    <pre v-if="fileContent[`${s.name}::${selectedFile[s.name]}`]" class="skill-body">{{ fileContent[`${s.name}::${selectedFile[s.name]}`] }}</pre>
                    <span v-else class="muted plugin-desc">（加载中…）</span>
                  </template>
                </div>
              </div>
            </div>
          </div>
          <span v-if="!skills.length && !loading" class="muted">暂无技能</span>
        </div>
      </el-tab-pane>
    </el-tabs>

    <!-- MCP 服务器配置 drawer -->
    <el-drawer
      v-model="mcpFormOpen"
      :title="mcpEditing ? '编辑 MCP 服务器' : '新建 MCP 服务器'"
      size="480px"
      :destroy-on-close="true"
    >
      <McpServerForm :server="mcpEditing" @saved="onMcpSaved" />
    </el-drawer>

    <!-- 技能包 drawer -->
    <el-drawer
      v-model="skillFormOpen"
      :title="skillEditing ? '编辑技能' : '新建技能'"
      size="min(640px, 90vw)"
      :destroy-on-close="true"
    >
      <SkillForm :skill="skillEditing" @saved="onSkillSaved" />
    </el-drawer>

    <!-- 编写指南 drawer -->
    <el-drawer v-model="guideOpen" title="脚本编写指南" size="min(640px, 90vw)">
      <div class="guide">
        <p>在 <code>custom_plugins/</code> 目录下创建 <code>.py</code> 文件，用 <code>@func</code> 装饰器定义函数即可。文件修改后自动热加载，无需重启。也可以在本页「自定义脚本」区直接上传。</p>

        <h4 class="guide-h4">示例</h4>
        <pre class="guide-code">from pydantic import BaseModel, Field

class NotifyInput(BaseModel):
    message: str = Field(description="消息内容")

class NotifyOutput(BaseModel):
    result: str = Field(description="发送结果")

@func(label="发送通知", description="发送通知消息")
async def send_notify(params: NotifyInput) -> NotifyOutput:
    return NotifyOutput(result=f"已发送: {params.message}")</pre>
        <p>函数名 <code>send_notify</code> 即为条目名：工作流 YAML 用 <code>type: send_notify</code> 引用，Agent 侧以同名 function calling 调用。输入输出参数类型都必须是 <code>BaseModel</code> 子类，框架自动推导 JSON Schema。</p>

        <h4 class="guide-h4">注册选项</h4>
        <table class="guide-table">
          <thead><tr><th>参数</th><th>默认值</th><th>说明</th></tr></thead>
          <tbody>
            <tr><td><code>label</code></td><td>—</td><td>显示名称（建议填写）</td></tr>
            <tr><td><code>description</code></td><td>—</td><td>功能描述；作为工具时是 LLM 读的主要依据</td></tr>
            <tr><td><code>node</code></td><td><code>True</code></td><td>注册为工作流节点</td></tr>
            <tr><td><code>tool</code></td><td><code>True</code></td><td>注册为 Agent 工具</td></tr>
          </tbody>
        </table>
        <p>两个开关独立：默认两端都注册；<code>node=False</code> 得到仅 Agent 可调用的工具，<code>tool=False</code> 得到仅流程使用的节点。卡片右上角的「仅节点 / 仅工具」就来自这两个开关——两端都注册时不标。</p>

        <h4 class="guide-h4">规则</h4>
        <ul class="guide-rules">
          <li>函数必须是 <code>async def</code>，输入输出都用 <code>BaseModel</code></li>
          <li>名字不可与内置或其它脚本冲突——节点名和工具名共用一个命名空间</li>
        </ul>

        <h4 class="guide-h4">错误处理</h4>
        <p>脚本加载失败时会在对应文件分组下显示错误信息，常见原因：</p>
        <ul class="guide-rules">
          <li>Python 语法错误或导入失败</li>
          <li>名字与已有节点/工具冲突</li>
          <li>运行时缺少依赖包</li>
        </ul>
        <p>修正后保存文件即自动重载。也可以删除后重新上传。</p>
      </div>
    </el-drawer>

    <!-- 技能编写指南 drawer -->
    <el-drawer v-model="skillGuideOpen" title="技能编写指南" size="min(640px, 90vw)">
      <div class="guide">
        <p>技能是 agent 可按需加载的指令包。每个技能是一个目录，包含 <code>SKILL.md</code> 文件。</p>

        <h4 class="guide-h4">添加技能</h4>
        <p>两种方式：</p>
        <ul class="guide-rules">
          <li><b>编写</b> — 点击「添加技能」，在编辑器中直接编写 SKILL.md</li>
          <li><b>导入</b> — 点击「添加技能」，切换到「导入 zip 包」选项卡，上传 <code>.zip</code> 文件（目录结构见下方）</li>
        </ul>

        <h4 class="guide-h4">SKILL.md 格式</h4>
        <pre class="guide-code">---
name: my-skill
description: 一句话说明技能用途
---

执行任务时，按以下步骤逐项检查：

## 步骤

1. 第一步做什么
2. 第二步做什么
3. 第三步做什么

## 注意事项

- 要点一
- 要点二</pre>

        <h4 class="guide-h4">头部信息字段</h4>
        <table class="guide-table">
          <thead><tr><th>字段</th><th>必填</th><th>说明</th></tr></thead>
          <tbody>
            <tr><td><code>name</code></td><td>是</td><td>技能标识符，如 <code>code-review</code></td></tr>
            <tr><td><code>description</code></td><td>是</td><td>技能描述，agent 根据此判断何时加载</td></tr>
          </tbody>
        </table>

        <h4 class="guide-h4">使用方式</h4>
        <p>在工作流或 agent 配置中通过 <code>skills</code> 字段引用技能名。agent 运行时会调用 <code>load_skill</code> 工具加载完整指令。</p>
        <ul class="guide-rules">
          <li><code>skills: ["*"]</code> — 加载所有技能</li>
          <li><code>skills: ["code-review", "summarize"]</code> — 加载指定技能</li>
        </ul>

        <h4 class="guide-h4">目录结构</h4>
        <pre class="guide-code">skills/
  code-review/
    SKILL.md          # 技能指令（必须）
    examples.md       # 附加文件（可选）
  summarize/
    SKILL.md</pre>
        <p>目录名建议与 <code>name</code> 一致。除 <code>SKILL.md</code> 外可放任意附加文件，agent 加载时会读取目录下的所有内容。</p>

        <h4 class="guide-h4">zip 导入</h4>
        <p>zip 内直接包含技能文件（<code>SKILL.md</code> 在根目录），例如：</p>
        <pre class="guide-code">my-skill.zip
  ├── SKILL.md
  └── examples.md</pre>
        <p>上传后会解压到 <code>skills/{name}/</code> 目录并注册。<code>name</code> 取自 SKILL.md 中的 frontmatter。</p>
        <p>编辑已有技能时也可上传 zip 替换全部文件，支持通过修改 SKILL.md 的 <code>name</code> 字段重命名。</p>
      </div>
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
.guide-link {
  font-size: 12px;
  color: var(--ink-3);
  cursor: pointer;
  white-space: nowrap;
  flex-shrink: 0;
  margin-left: 10px;
}
.guide-link:hover {
  color: #79bbff;
}
.load-error {
  color: #f87171;
  font-size: 12px;
}
.cap-tabs {
  padding: 0 20px;
}
.lead {
  margin: 0 0 14px;
  font-size: 12px;
  line-height: 1.6;
  color: var(--ink-3);
}
.lead code {
  font-family: var(--font-mono);
  background: rgba(255, 255, 255, 0.06);
  padding: 1px 5px;
  border-radius: 3px;
}

/* 筛选 */
.filter-bar {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 10px 14px;
  margin-bottom: 16px;
}
.chip-group {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}
.chip {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 3px 10px;
  font-size: 12px;
  color: var(--ink-2);
  background: rgba(255, 255, 255, 0.04);
  border: 1px solid var(--line);
  border-radius: 999px;
  cursor: pointer;
  transition: color 0.15s, border-color 0.15s, background 0.15s;
}
.chip:hover {
  color: var(--ink);
  border-color: rgba(255, 255, 255, 0.18);
}
.chip.on {
  color: var(--ink);
  background: rgba(77, 196, 178, 0.14);
  border-color: rgba(77, 196, 178, 0.45);
}
.chip-n {
  font-family: var(--font-mono);
  font-size: 10.5px;
  color: var(--ink-3);
}
.chip.on .chip-n {
  color: var(--accent, #4dc4b2);
}
.search {
  width: min(280px, 100%);
}
.search :deep(.el-input__wrapper) {
  background: rgba(255, 255, 255, 0.04);
  box-shadow: 0 0 0 1px var(--line) inset;
}
.search :deep(.el-input__inner) {
  font-size: 12px;
}

.empty {
  padding: 28px 0;
  text-align: center;
  font-size: 12px;
  color: var(--ink-3);
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 10px;
}

.section-head {
  display: flex;
  align-items: center;
  gap: 12px;
  margin: 4px 0 14px;
}
.section-head .muted {
  flex: 1;
  min-width: 0;
}

/* 树：一级 = 内置 / 自定义脚本 / MCP 服务器；二级 = group-title（域、脚本文件、MCP 服务器实例） */
.group-section {
  margin: 0 0 12px 16px;
  padding-left: 12px;
  border-left: 1px solid var(--line);
}
.group-section:last-child {
  margin-bottom: 0;
}
.group-title {
  margin: 0 0 8px;
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 1px;
  color: var(--ink-3);
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
.group-title.with-actions {
  user-select: none;
}
.group-title.collapsible {
  cursor: pointer;
}
.group-title.collapsible:hover {
  color: var(--ink-2);
}
.group-name.mono {
  font-family: var(--font-mono);
  font-weight: 500;
  letter-spacing: 0;
}
.group-count {
  font-family: var(--font-mono);
  font-size: 10.5px;
  font-weight: 400;
  color: var(--ink-3);
  opacity: 0.85;
}
.group-actions {
  margin-left: auto;
  display: flex;
  gap: 2px;
  flex-shrink: 0;
}
.transport-tag {
  text-transform: uppercase;
  font-size: 10px;
}
.group-error {
  margin: -2px 0 8px;
  font-family: var(--font-mono);
  font-size: 11px;
  color: #f87171;
  line-height: 1.5;
}
.group-empty {
  font-size: 11px;
  padding: 2px 0 0;
}
.chev {
  flex: none;
  color: var(--ink-3);
  transition: transform 0.15s;
}
.chev.open {
  transform: rotate(90deg);
}
.node-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));
  gap: 10px;
  min-height: 26px;
}

/* 一级节点：内置 / 自定义脚本 / MCP 服务器，三者平级 */
.top-section {
  padding: 18px 0 4px;
  border-top: 1px solid var(--line);
}
.top-section:first-child {
  padding-top: 0;
  border-top: none;
}
.source-head {
  display: flex;
  align-items: center;
  gap: 10px;
}
/* 次要按钮：给底色，免得只剩文字和链接分不清 */
.btn-soft {
  --el-button-bg-color: rgba(255, 255, 255, 0.06);
  --el-button-border-color: var(--line);
  --el-button-text-color: var(--ink-2);
  --el-button-hover-bg-color: rgba(255, 255, 255, 0.1);
  --el-button-hover-border-color: rgba(255, 255, 255, 0.18);
  --el-button-hover-text-color: var(--ink);
  --el-button-active-bg-color: rgba(255, 255, 255, 0.12);
  --el-button-disabled-bg-color: rgba(255, 255, 255, 0.04);
  --el-button-disabled-border-color: var(--line);
  --el-button-disabled-text-color: var(--ink-3);
}
.btn-soft--danger {
  --el-button-text-color: #f87171;
  --el-button-hover-bg-color: rgba(248, 113, 113, 0.12);
  --el-button-hover-border-color: rgba(248, 113, 113, 0.35);
  --el-button-hover-text-color: #fca5a5;
  --el-button-active-bg-color: rgba(248, 113, 113, 0.16);
}
.source-title {
  margin: 0;
  flex: 1;
  font-size: 13px;
  font-weight: 600;
  color: var(--ink);
  display: flex;
  align-items: center;
  gap: 8px;
  cursor: pointer;
  user-select: none;
}
.source-title:hover {
  color: var(--ink-2);
}
.source-title .chev {
  color: var(--ink-2);
}
.source-note {
  margin: 4px 0 14px 16px;
  padding-left: 12px;
  font-size: 11.5px;
  line-height: 1.6;
  color: var(--ink-3);
}
.source-note code {
  font-family: var(--font-mono);
  background: rgba(255, 255, 255, 0.06);
  padding: 1px 5px;
  border-radius: 3px;
}
.source-empty {
  margin-left: 16px;
  padding-left: 12px;
  font-size: 12px;
}

/* 技能内容区 */
.skill-content {
  margin-top: 8px;
  user-select: text;
  cursor: auto;
}

/* 左树右内容分栏 */
.skill-split {
  display: flex;
  margin-top: 8px;
  background: rgba(0, 0, 0, 0.2);
  border: 1px solid rgba(255, 255, 255, 0.06);
  border-radius: 6px;
  overflow: hidden;
  max-height: 280px;
}
.skill-tree {
  font-family: var(--font-mono);
  font-size: 11.5px;
  line-height: 1.7;
  color: var(--ink-3);
  padding: 8px 10px;
  overflow-y: auto;
  overflow-x: hidden;
  user-select: none;
}
.skill-divider {
  flex: 0 0 4px;
  cursor: col-resize;
  background: rgba(255, 255, 255, 0.06);
  transition: background 0.15s;
}
.skill-divider:hover {
  background: rgba(255, 255, 255, 0.15);
}
.tree-item {
  display: flex;
}
.tree-item.file {
  cursor: pointer;
  border-radius: 3px;
  padding: 0 2px;
}
.tree-item.file:hover {
  background: rgba(255, 255, 255, 0.06);
  color: var(--ink-2);
}
.tree-item.file.active {
  background: rgba(77, 196, 178, 0.14);
  color: var(--ink);
}
.tree-item.dir {
  color: var(--ink-2);
  font-weight: 500;
}
.tree-connector {
  white-space: pre;
  opacity: 0.5;
  flex-shrink: 0;
}
.tree-name {
  flex: 1;
  min-width: 0;
}
.skill-viewer {
  flex: 1;
  min-width: 0;
  overflow: auto;
}

/* 技能正文 */
.skill-body {
  margin: 0;
  font-family: var(--font-mono);
  font-size: 11.5px;
  line-height: 1.6;
  color: var(--ink-3);
  padding: 10px 12px;
  white-space: pre-wrap;
  user-select: text;
  cursor: auto;
}
/* 独立显示时（无附加文件）加边框背景 */
.skill-body--standalone {
  background: rgba(0, 0, 0, 0.28);
  border: 1px solid var(--line);
  border-radius: 6px;
  max-height: 280px;
  overflow: auto;
}

/* 技能包列表 */
.plugin-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.plugin-card {
  background: rgba(16, 21, 42, 0.72);
  border: 1px solid var(--line);
  border-radius: 12px;
  padding: 14px 16px;
}
.plugin-card.clickable {
  cursor: pointer;
  user-select: none;
}
.plugin-head {
  display: flex;
  align-items: center;
  gap: 12px;
}
.plugin-desc {
  margin-top: 6px;
  margin-left: 18px;
  font-size: 12px;
  line-height: 1.6;
}
.plugin-info {
  flex: 1;
  min-width: 0;
  display: flex;
  align-items: baseline;
  gap: 10px;
  flex-wrap: wrap;
}
.plugin-filename {
  font-family: var(--font-mono);
  font-size: 13px;
  font-weight: 500;
  color: var(--ink);
}
.plugin-module {
  font-family: var(--font-mono);
  font-size: 11.5px;
}

/* 编写指南 */
.guide {
  font-size: 13px;
  line-height: 1.7;
  color: var(--ink-2);
}
.guide p {
  margin: 0 0 14px;
}
.guide code {
  font-family: var(--font-mono);
  font-size: 12px;
  background: rgba(255, 255, 255, 0.06);
  padding: 1px 5px;
  border-radius: 3px;
}
.guide-h4 {
  margin: 16px 0 8px;
  font-size: 13px;
  font-weight: 600;
  color: var(--ink);
}
.guide-code {
  font-family: var(--font-mono);
  font-size: 12px;
  line-height: 1.6;
  background: rgba(0, 0, 0, 0.3);
  border: 1px solid var(--line);
  border-radius: 6px;
  padding: 14px 16px;
  overflow-x: auto;
  margin: 0 0 10px;
  white-space: pre;
}
.guide-rules {
  margin: 0;
  padding-left: 20px;
}
.guide-rules li {
  margin-bottom: 4px;
}
.guide-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 12px;
  margin-top: 4px;
}
.guide-table th,
.guide-table td {
  text-align: left;
  padding: 6px 10px;
  border-bottom: 1px solid var(--line);
}
.guide-table th {
  font-weight: 600;
  color: var(--ink-2);
  font-size: 11px;
}
.guide-table code {
  font-size: 11.5px;
}
</style>

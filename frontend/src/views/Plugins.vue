<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { listNodeTypes, type NodeTypeInfo } from '@/api/nodeTypes'
import { listPlugins, uploadPlugin, type PluginInfo } from '@/api/plugins'
import NodeTypeCard from '@/components/NodeTypeCard.vue'

const plugins = ref<PluginInfo[]>([])
const nodeTypes = ref<NodeTypeInfo[]>([])
const loading = ref(false)
const loadError = ref(false)
const uploading = ref(false)
const fileInput = ref<HTMLInputElement | null>(null)

const nodeTypeMap = computed(() => new Map(nodeTypes.value.map((t) => [t.name, t])))

const builtinNodes = computed(() => {
  const pluginNames = new Set(plugins.value.flatMap((p) => p.node_names))
  return nodeTypes.value.filter((t) => !pluginNames.has(t.name))
})

const builtinGroups = computed(() => {
  const map = new Map<string, NodeTypeInfo[]>()
  for (const t of builtinNodes.value) {
    const g = t.group || '其他'
    if (!map.has(g)) map.set(g, [])
    map.get(g)!.push(t)
  }
  return [...map.entries()].map(([name, items]) => ({ name, items }))
})

async function fetchAll() {
  loading.value = true
  loadError.value = false
  try {
    const [p, n] = await Promise.all([listPlugins(), listNodeTypes()])
    plugins.value = p.plugins
    nodeTypes.value = n.node_types
  } catch {
    loadError.value = true
  } finally {
    loading.value = false
  }
}

function fmtTime(iso: string) {
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString()
}

async function handleUpload(file: File) {
  uploading.value = true
  try {
    const plugin = await uploadPlugin(file)
    ElMessage.success(`插件 ${plugin.filename} 上传成功`)
    await fetchAll()
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

onMounted(fetchAll)
</script>

<template>
  <div class="page">
    <header class="page-head">
      <div class="head-info">
        <h1>节点类型</h1>
        <span class="muted">可在 YAML 流程定义中使用的全部节点类型；插件修改后自动热加载。</span>
      </div>
      <span v-if="loadError" class="load-error">加载失败，请检查后端是否可用</span>
      <input ref="fileInput" type="file" accept=".py" hidden @change="onFileChange" />
      <el-button type="primary" :loading="uploading" @click="fileInput?.click()">上传插件</el-button>
      <el-button plain :loading="loading" @click="fetchAll">↻ 刷新</el-button>
    </header>

    <main class="panel">
      <!-- 内置节点类型 -->
      <div class="section-head">
        <h2>内置节点类型</h2>
        <span class="muted">框架自带，只读 · {{ builtinNodes.length }}</span>
      </div>
      <div v-loading="loading">
        <div v-for="g in builtinGroups" :key="g.name" class="group-section">
          <h3 class="group-title">{{ g.name }}</h3>
          <div class="node-grid">
            <NodeTypeCard v-for="t in g.items" :key="t.name" :node="t" variant="func" />
          </div>
        </div>
        <span v-if="!builtinNodes.length && !loading" class="muted">—</span>
      </div>

      <!-- 插件 -->
      <div class="section-divider"></div>
      <div class="section-head">
        <h2>插件</h2>
        <span class="muted">默认目录 custom_plugins/</span>
      </div>
      <div v-loading="loading" class="plugin-list">
        <div v-for="p in plugins" :key="p.filename" class="plugin-card">
          <div class="plugin-head">
            <div class="plugin-info">
              <span class="plugin-filename">{{ p.filename }}</span>
              <span class="plugin-module muted">{{ p.module }}</span>
              <span class="plugin-time muted">{{ fmtTime(p.loaded_at) }}</span>
            </div>
            <el-tag v-if="p.error" type="danger" size="small" disable-transitions>{{ p.error }}</el-tag>
            <el-tag v-else type="success" size="small" disable-transitions>正常</el-tag>
          </div>
          <div v-if="p.node_names.length" class="plugin-nodes">
            <template v-for="name in p.node_names" :key="name">
              <NodeTypeCard v-if="nodeTypeMap.get(name)" :node="nodeTypeMap.get(name)!" variant="plugin" />
              <div v-else class="node-card-missing">
                <el-tag class="node-tag--plugin" size="small" disable-transitions>{{ name }}</el-tag>
                <span class="muted">未找到类型信息</span>
              </div>
            </template>
          </div>
          <div v-else class="plugin-nodes">
            <span class="muted">无注册节点类型</span>
          </div>
        </div>
        <span v-if="!plugins.length && !loading" class="muted">暂无已加载插件</span>
      </div>
    </main>
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
.load-error {
  color: #f87171;
  font-size: 12px;
}
.panel {
  padding: 18px 20px;
  display: flex;
  flex-direction: column;
  gap: 18px;
}
.section-head {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 14px;
}
.section-head h2 {
  font-size: 13px;
  margin: 0;
}
.section-divider {
  height: 1px;
  background: var(--line);
  margin: 6px 0;
}
.group-section {
  margin-bottom: 14px;
}
.group-section:last-child {
  margin-bottom: 0;
}
.group-title {
  margin: 0 0 8px;
  font-size: 12px;
  font-weight: 600;
  color: var(--ink);
}

/* 节点卡片网格 */
.node-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));
  gap: 12px;
  min-height: 26px;
}

/* 插件卡片 */
.plugin-list {
  display: flex;
  flex-direction: column;
  gap: 14px;
}
.plugin-card {
  background: rgba(16, 21, 42, 0.72);
  border: 1px solid var(--line);
  border-radius: 12px;
  padding: 14px 16px;
}
.plugin-head {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 12px;
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
.plugin-time {
  font-size: 11px;
}
.plugin-nodes {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));
  gap: 10px;
}
.node-card-missing {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 12px;
}
</style>

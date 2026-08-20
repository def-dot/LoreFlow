<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { listNodeTypes, type NodeTypeInfo } from '@/api/nodeTypes'
import { listPlugins, reloadPlugins, type PluginInfo } from '@/api/plugins'

const plugins = ref<PluginInfo[]>([])
const nodeTypes = ref<NodeTypeInfo[]>([])
const loading = ref(false)
const reloading = ref(false)

// 内置节点 = 全量类型目录减去插件注册的名字（框架自带，只读展示）
const builtinNodes = computed(() => {
  const pluginNames = new Set(plugins.value.flatMap((p) => p.node_names))
  return nodeTypes.value.filter((t) => !pluginNames.has(t.name))
})

async function fetchAll() {
  loading.value = true
  try {
    const [p, n] = await Promise.all([listPlugins(), listNodeTypes()])
    plugins.value = p.plugins
    nodeTypes.value = n.node_types
  } catch {
    // 后端未启动时静默，等用户点击重试
  } finally {
    loading.value = false
  }
}

async function reload() {
  if (reloading.value) return
  reloading.value = true
  try {
    // 重载后插件注册名可能变化，类型目录一并刷新
    const [p, n] = await Promise.all([reloadPlugins(), listNodeTypes()])
    plugins.value = p.plugins
    nodeTypes.value = n.node_types
    ElMessage.success('插件已重载')
  } finally {
    reloading.value = false
  }
}

function fmtTime(iso: string) {
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString()
}

onMounted(fetchAll)
</script>

<template>
  <div class="page">
    <header class="page-head">
      <h1>节点目录</h1>
      <span class="muted">YAML 中 type:/condition: 可引用的全部类型；插件放入 custom_plugins/ 后点重载即生效。</span>
    </header>

    <main class="panel">
      <section class="card">
        <div class="card-head">
          <h2>内置节点</h2>
          <span class="muted">框架自带，只读 · {{ builtinNodes.length }}</span>
        </div>
        <div v-loading="loading" class="tag-cloud">
          <el-tooltip
            v-for="t in builtinNodes"
            :key="t.name"
            :content="`${t.label} — ${t.description}`"
            placement="top"
          >
            <el-tag
              :type="t.kind === 'condition' ? 'warning' : 'primary'"
              class="node-tag"
              disable-transitions
            >
              {{ t.name }} · {{ t.label }}
            </el-tag>
          </el-tooltip>
          <span v-if="!builtinNodes.length && !loading" class="muted">—</span>
        </div>
      </section>

      <section class="card">
        <div class="card-head">
          <h2>插件</h2>
          <span class="muted">settings.PLUGINS_DIR（默认 backend/custom_plugins/）</span>
          <div class="spacer" />
          <el-button type="primary" :loading="reloading" @click="reload">⟳ 重载</el-button>
        </div>
        <el-table v-loading="loading" :data="plugins" empty-text="暂无已加载插件">
          <el-table-column prop="filename" label="文件" min-width="160" />
          <el-table-column prop="module" label="模块" min-width="200" />
          <el-table-column label="注册节点" min-width="240">
            <template #default="{ row }">
              <el-tag
                v-for="name in row.node_names"
                :key="name"
                size="small"
                class="node-tag"
                disable-transitions
              >
                {{ name }}
              </el-tag>
              <span v-if="!row.node_names.length" class="muted">—</span>
            </template>
          </el-table-column>
          <el-table-column label="加载时间" min-width="170">
            <template #default="{ row }">{{ fmtTime(row.loaded_at) }}</template>
          </el-table-column>
          <el-table-column label="状态" min-width="220">
            <template #default="{ row }">
              <el-tag v-if="row.error" type="danger" size="small" disable-transitions>{{ row.error }}</el-tag>
              <el-tag v-else type="success" size="small" disable-transitions>正常</el-tag>
            </template>
          </el-table-column>
        </el-table>
      </section>
    </main>
  </div>
</template>

<style scoped>
.page-head {
  padding: 12px 20px;
  border-bottom: 1px solid #2a2f38;
  display: flex;
  align-items: center;
  gap: 14px;
}
.page-head h1 {
  font-size: 18px;
  margin: 0;
}
.spacer {
  flex: 1;
}
.panel {
  padding: 18px 20px;
  display: flex;
  flex-direction: column;
  gap: 18px;
}
.card {
  background: #1a1d23;
  border: 1px solid #2a2f38;
  border-radius: 10px;
  padding: 14px;
}
.card-head {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 12px;
}
.card-head h2 {
  font-size: 15px;
  margin: 0;
}
.tag-cloud {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  min-height: 26px;
}
.node-tag {
  margin: 2px 4px 2px 0;
}
</style>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { listNodeTypes, type NodeTypeInfo } from '@/api/nodeTypes'
import { listPlugins, type PluginInfo } from '@/api/plugins'

const plugins = ref<PluginInfo[]>([])
const nodeTypes = ref<NodeTypeInfo[]>([])
const loading = ref(false)
// 后端未启动时置位，页头给提示，可点「刷新」重试
const loadError = ref(false)

// 内置节点 = 全量类型目录减去插件注册的名字（框架自带，只读展示）
const builtinNodes = computed(() => {
  const pluginNames = new Set(plugins.value.flatMap((p) => p.node_names))
  return nodeTypes.value.filter((t) => !pluginNames.has(t.name))
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

onMounted(fetchAll)
</script>

<template>
  <div class="page">
    <header class="page-head">
      <div class="head-info">
        <h1>节点类型</h1>
        <span class="muted">YAML 中 type:/condition: 可引用的全部类型；修改插件文件后数秒内自动生效。</span>
      </div>
      <span v-if="loadError" class="load-error">加载失败，请检查后端是否可用</span>
      <el-button plain :loading="loading" @click="fetchAll">↻ 刷新</el-button>
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
  padding: 10px 20px;
  border-bottom: 1px solid var(--line);
  display: flex;
  align-items: center;
  gap: 14px;
}
/* 标题+描述成组占满左侧，把刷新按钮推到右端；baseline 对齐让
 * 描述与大标题同一文字基线，窄屏 flex-wrap 时描述整条换行到标题下方 */
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
.card {
  background: rgba(16, 21, 42, 0.72);
  border: 1px solid var(--line);
  border-radius: 12px;
  padding: 16px 18px;
}
.card-head {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 12px;
}
.card-head h2 {
  font-size: 13px;
  margin: 0;
}
.tag-cloud {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  min-height: 26px;
}
/* 节点名是 YAML 里的 type: 键，用等宽呈现代码身份 */
.node-tag {
  margin: 2px 4px 2px 0;
  font-family: var(--font-mono);
}
</style>

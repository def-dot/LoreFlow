<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { listNodeTypes, type NodeTypeInfo } from '@/api/nodeTypes'
import { listPlugins, type PluginInfo } from '@/api/plugins'
import SchemaFields from '@/components/SchemaFields.vue'

const plugins = ref<PluginInfo[]>([])
const nodeTypes = ref<NodeTypeInfo[]>([])
const loading = ref(false)
const loadError = ref(false)

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
      <el-button plain :loading="loading" @click="fetchAll">↻ 刷新</el-button>
    </header>

    <main class="panel">
      <!-- 内置节点类型 -->
      <div class="section-head">
        <h2>内置节点类型</h2>
        <span class="muted">框架自带，只读 · {{ builtinNodes.length }}</span>
      </div>
      <div v-loading="loading">
        <div v-for="(g, gi) in builtinGroups" :key="g.name" class="group-section">
          <h3 class="group-title">{{ g.name }}</h3>
          <div class="node-grid">
            <div v-for="t in g.items" :key="t.name" class="node-card">
              <div class="node-card-head">
                <el-tag
                  class="node-tag node-tag--func"
                  disable-transitions
                >
                  {{ t.name }}
                </el-tag>
                <span class="node-label">{{ t.label }}</span>
              </div>
              <p class="node-desc">{{ t.description }}</p>

              <!-- 输入参数 -->
              <div v-if="t.input_schema && Object.keys(t.input_schema).length" class="schema-section">
                <h3 class="schema-title">输入</h3>
                <SchemaFields :fields="t.input_schema" />
              </div>
              <div v-else class="schema-section">
                <h3 class="schema-title">输入</h3>
                <span class="schema-empty">无</span>
              </div>

              <!-- 输出结构 -->
              <div v-if="t.output_schema" class="schema-section">
                <h3 class="schema-title">输出</h3>
                <SchemaFields v-if="t.output_schema.fields" :fields="t.output_schema.fields" />
                <template v-else-if="t.output_schema.type === 'list' && t.output_schema.item?.fields">
                  <div class="schema-field">
                    <span class="field-type">list[object]</span>
                  </div>
                  <SchemaFields :fields="t.output_schema.item.fields" :depth="1" />
                </template>
                <div v-else class="schema-field">
                  <span class="field-type">{{ t.output_schema.type }}</span>
                  <span v-if="t.output_schema.description" class="field-desc">{{ t.output_schema.description }}</span>
                </div>
              </div>
            </div>
          </div>
        </div>
        <span v-if="!builtinNodes.length && !loading" class="muted">—</span>
      </div>

      <!-- 插件 -->
      <div class="section-divider"></div>
      <section class="card">
        <div class="card-head">
          <h2>插件</h2>
          <span class="muted">默认目录 custom_plugins/</span>
        </div>
        <el-table v-loading="loading" :data="plugins" empty-text="暂无已加载插件">
          <el-table-column prop="filename" label="文件" min-width="160" />
          <el-table-column prop="module" label="模块" min-width="200" />
          <el-table-column label="注册节点类型" min-width="280">
            <template #default="{ row }">
              <el-tooltip
                v-for="name in row.node_names"
                :key="name"
                :content="nodeTypeMap.get(name)?.description || name"
                placement="top"
              >
                <el-tag
                  class="node-tag node-tag--plugin"
                  size="small"
                  disable-transitions
                >
                  {{ name }} · {{ nodeTypeMap.get(name)?.label || '' }}
                </el-tag>
              </el-tooltip>
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
.card-head h2 {
  font-size: 13px;
  margin: 0;
}

/* 节点卡片网格 */
.node-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));
  gap: 12px;
  min-height: 26px;
}
.node-card {
  background: rgba(10, 14, 27, 0.6);
  border: 1px solid var(--line);
  border-radius: 8px;
  padding: 12px 14px;
}
.node-card-head {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 6px;
}
.node-label {
  font-size: 13px;
  font-weight: 500;
  color: var(--ink);
}
.node-desc {
  margin: 0 0 8px;
  font-size: 12px;
  line-height: 1.6;
  color: var(--ink-3);
}

/* schema 区块 */
.schema-section {
  margin-top: 6px;
}
.schema-title {
  margin: 0 0 4px;
  font-size: 11px;
  font-weight: 600;
  color: var(--ink-2);
  letter-spacing: 1px;
}
.schema-empty {
  font-size: 11px;
  color: var(--ink-3);
}
.schema-field {
  display: flex;
  align-items: baseline;
  gap: 6px;
  font-size: 12px;
  line-height: 1.6;
}
.field-type {
  font-family: var(--font-mono);
  font-size: 11px;
  color: var(--ink-3);
  flex-shrink: 0;
}
.field-desc {
  font-size: 11px;
  color: var(--ink-3);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* 节点标签 */
.node-tag {
  font-family: var(--font-mono);
}
.node-tag--func {
  --el-tag-bg-color: rgba(64, 158, 255, 0.15);
  --el-tag-border-color: rgba(64, 158, 255, 0.4);
  --el-tag-text-color: #79bbff;
}
.node-tag--plugin {
  --el-tag-bg-color: rgba(103, 194, 58, 0.15);
  --el-tag-border-color: rgba(103, 194, 58, 0.4);
  --el-tag-text-color: #67c23a;
}
</style>

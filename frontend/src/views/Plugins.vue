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
const guideOpen = ref(false)

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
      <span class="guide-link" @click="guideOpen = true">编写指南</span>
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

    <!-- 编写指南 drawer -->
    <el-drawer v-model="guideOpen" title="插件编写指南" size="min(560px, 90vw)">
      <div class="guide">
        <p>在 <code>custom_plugins/</code> 目录下创建 <code>.py</code> 文件，用 <code>@node_type</code> 装饰器定义函数即可。文件修改后自动热加载，无需重启。也可通过页面「上传插件」按钮上传。</p>

        <h4 class="guide-h4">示例</h4>
        <pre class="guide-code">from app.registry import node_type

@node_type(
    label="发送通知",
    description="发送通知消息",
    input_schema={
        "message": {"type": "string", "required": True, "description": "消息内容"},
        "channel": {"type": "string", "required": False, "description": "通知渠道"},
    },
    output_schema={"type": "string", "description": "发送结果"},
)
async def send_notify(ctx: dict) -> str:
    message = ctx.get("message", "")
    channel = ctx.get("channel", "default")
    # 你的业务逻辑
    return f"已发送至 {channel}"</pre>

        <h4 class="guide-h4">规则</h4>
        <ul class="guide-rules">
          <li>函数必须是 <code>async def</code>，参数为 <code>ctx: dict</code></li>
          <li><code>ctx</code> 是输入字典，通过 <code>ctx.get("字段名")</code> 取值</li>
        </ul>

        <h4 class="guide-h4">@node_type 参数</h4>
        <table class="guide-table">
          <tr><th>参数</th><th>必填</th><th>说明</th></tr>
          <tr><td><code>label</code></td><td>是</td><td>显示名称</td></tr>
          <tr><td><code>description</code></td><td>是</td><td>节点类型功能描述</td></tr>
          <tr><td><code>input_schema</code></td><td>否</td><td>输入参数声明，字典格式</td></tr>
          <tr><td><code>output_schema</code></td><td>否</td><td>输出结构声明</td></tr>
        </table>
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
  font-size: 13px;
  color: var(--ink-3);
  cursor: pointer;
  white-space: nowrap;
}
.guide-link:hover {
  color: #79bbff;
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

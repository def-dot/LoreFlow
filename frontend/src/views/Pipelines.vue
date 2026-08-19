<script setup lang="ts">
import { onMounted } from 'vue'
import MermaidDiagram from '@/components/MermaidDiagram.vue'
import { usePipelinesStore } from '@/stores/pipelines'

const store = usePipelinesStore()

function kindTagType(kind: string) {
  return kind === 'human' ? 'warning' : kind === 'loop' ? 'info' : 'primary'
}

onMounted(async () => {
  try {
    await store.fetchPipelines()
    if (store.pipelines.length) await store.select(store.pipelines[0].filename)
  } catch {
    // 后端未启动时静默，等用户刷新
  }
})
</script>

<template>
  <div class="page">
    <header class="page-head">
      <h1>Pipelines</h1>
      <span class="muted">内置示例流水线（只读）</span>
    </header>
    <main class="layout">
      <aside class="run-side">
        <div class="head">
          <h2>Pipelines</h2>
          <span class="muted">{{ store.pipelines.length }}</span>
        </div>
        <div class="run-list">
          <div
            v-for="p in store.pipelines"
            :key="p.filename"
            class="run-item"
            :class="{ sel: p.filename === store.selectedFile }"
            @click="store.select(p.filename)"
          >
            <el-tag size="small" disable-transitions>{{ p.node_count }} nodes</el-tag>
            <div class="pbody">
              <span class="pname">{{ p.name }}</span>
              <span class="rid">{{ p.filename }}</span>
              <span class="pdesc muted">{{ p.description }}</span>
            </div>
          </div>
          <div v-if="!store.pipelines.length" class="muted">没有可用的流水线。</div>
        </div>
      </aside>

      <div v-if="store.detail" class="detail">
        <div class="detail-head">
          <span class="muted">{{ store.detail.name }}</span>
          <span class="muted">{{ store.detail.filename }}</span>
          <el-tag size="small" disable-transitions>{{ store.detail.node_count }} nodes</el-tag>
        </div>
        <div class="panels">
          <section class="panel">
            <h2>Pipeline</h2>
            <MermaidDiagram :source="store.detail.mermaid" />
            <p v-if="store.detail.description" class="muted desc">{{ store.detail.description }}</p>
          </section>
          <section class="panel">
            <h2>Nodes</h2>
            <el-table :data="store.detail.nodes" size="small" max-height="420">
              <el-table-column prop="name" label="节点" width="90" />
              <el-table-column label="种类" width="80">
                <template #default="{ row }">
                  <el-tag :type="kindTagType(row.kind)" size="small" disable-transitions>
                    {{ row.kind }}
                  </el-tag>
                </template>
              </el-table-column>
              <el-table-column label="功能" min-width="130">
                <template #default="{ row }">
                  <div>{{ row.type_label ?? '—' }}</div>
                  <div v-if="row.type" class="muted">{{ row.type }}</div>
                </template>
              </el-table-column>
              <el-table-column label="依赖" min-width="90">
                <template #default="{ row }">
                  {{ row.depends_on.length ? row.depends_on.join(', ') : '—' }}
                </template>
              </el-table-column>
              <el-table-column label="重试" min-width="110">
                <template #default="{ row }">{{ row.retry ?? '—' }}</template>
              </el-table-column>
              <el-table-column label="条件" min-width="100">
                <template #default="{ row }">{{ row.condition_label ?? '—' }}</template>
              </el-table-column>
              <el-table-column label="提示 / 循环体" min-width="130">
                <template #default="{ row }">{{ row.prompt ?? row.body_summary ?? '—' }}</template>
              </el-table-column>
            </el-table>
          </section>
        </div>
        <section class="panel source-panel">
          <h2>Source</h2>
          <pre class="source">{{ store.detail.source }}</pre>
        </section>
      </div>
      <div v-else class="muted">从左侧选择一个流水线。</div>
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
.layout {
  display: grid;
  grid-template-columns: 300px 1fr;
  gap: 18px;
  padding: 18px 20px;
  align-items: start;
}
.run-side {
  background: #1a1d23;
  border: 1px solid #2a2f38;
  border-radius: 10px;
  padding: 14px;
  align-self: start;
}
.head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 10px;
}
.run-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.run-item {
  padding: 8px 10px;
  border: 1px solid #2a2f38;
  border-radius: 8px;
  cursor: pointer;
  display: flex;
  align-items: flex-start;
  gap: 8px;
}
.run-item:hover {
  border-color: #5a6577;
}
.run-item.sel {
  border-color: #3b82f6;
  background: #1c2433;
}
.pbody {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}
.pname {
  font-size: 13px;
}
.rid {
  font-size: 12px;
  color: #9aa4b2;
}
.pdesc {
  font-size: 12px;
  overflow: hidden;
  text-overflow: ellipsis;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
}
.detail-head {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 12px;
  flex-wrap: wrap;
}
.panels {
  display: grid;
  grid-template-columns: 1fr 1.2fr;
  gap: 18px;
}
.panel {
  background: #1a1d23;
  border: 1px solid #2a2f38;
  border-radius: 10px;
  padding: 14px;
}
.desc {
  margin: 10px 0 0;
}
.source-panel {
  margin-top: 18px;
}
.source {
  margin: 0;
  white-space: pre-wrap;
  word-break: break-all;
  font-size: 12px;
  line-height: 1.5;
  color: #9aa4b2;
  max-height: 420px;
  overflow: auto;
}
@media (max-width: 900px) {
  .layout {
    grid-template-columns: 1fr;
  }
  .panels {
    grid-template-columns: 1fr;
  }
}
</style>

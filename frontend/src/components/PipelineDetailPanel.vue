<script setup lang="ts">
import { computed } from 'vue'
import type { PipelineDetail } from '@/api/pipelines'
import MermaidDiagram from './MermaidDiagram.vue'

const props = defineProps<{ detail: PipelineDetail }>()

function kindTagType(kind: string) {
  return kind === 'human' ? 'warning' : kind === 'loop' ? 'info' : 'primary'
}

// 输入参数声明（归一化行）：有任一才渲染整块，避免无参数流水线多一块空白
const hasParams = computed(() => props.detail.params.length > 0)
// 默认值短预览：过长截断（完整值在 YAML 源码里）
function defaultPreview(value: unknown): string {
  const text = JSON.stringify(value) ?? ''
  return text.length > 40 ? `${text.slice(0, 40)}…` : text
}
// 审核视图文本：label(key)；未声明时节点行 review=null 不渲染
function reviewView(review: Record<string, string>): string {
  return Object.entries(review)
    .map(([key, label]) => (label === key ? key : `${label}(${key})`))
    .join('、')
}
</script>

<template>
  <div>
    <!-- 无自身头部：仅用于 Runs 页 drawer，名称/文件名由 drawer 标题展示 -->
    <!-- description 描述整条流水线：置顶导语，先读说明再看图，图中的重试/条件标记才有解释 -->
    <p v-if="detail.description" class="muted desc">{{ detail.description }}</p>
    <div v-if="hasParams" class="params-block">
      <div class="params-title">运行时参数</div>
      <div v-for="p in detail.params" :key="p.name" class="params-item">
        <el-tag :type="p.required ? 'danger' : 'info'" size="small" disable-transitions>
          {{ p.label }}{{ p.required ? ' *' : '' }}
        </el-tag>
        <code class="params-code">{{ p.name }}</code>
        <span v-if="p.description" class="muted">{{ p.description }}</span>
        <span v-if="p.has_default" class="muted">默认 {{ defaultPreview(p.default) }}</span>
        <span v-if="!p.required && !p.has_default" class="muted">（不填则不传）</span>
      </div>
    </div>
    <div class="panels">
      <section class="panel">
        <h2>流水线</h2>
        <MermaidDiagram :source="detail.mermaid" />
      </section>
      <section class="panel">
        <h2>节点</h2>
        <el-table :data="detail.nodes" size="small" max-height="420">
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
          <el-table-column label="说明" min-width="130">
            <template #default="{ row }">
              <div>{{ row.type_description ?? '—' }}</div>
              <div v-if="row.review" class="muted review-view">审核视图: {{ reviewView(row.review) }}</div>
            </template>
          </el-table-column>
        </el-table>
      </section>
    </div>
    <section class="panel source-panel">
      <h2>YAML 源码</h2>
      <pre class="source">{{ detail.source }}</pre>
    </section>
  </div>
</template>

<style scoped>
/* 自适应：drawer 里单列，宽容器里双列 */
.panels {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
  gap: 18px;
}
.panel {
  background: rgba(16, 21, 42, 0.72);
  border: 1px solid var(--line);
  border-radius: 12px;
  padding: 16px;
}
.desc {
  margin: 0 0 14px;
}
/* human 节点说明下方的审核视图行 */
.review-view {
  font-size: 12px;
  margin-top: 2px;
}
/* 运行时参数块：紧随导语，说明这条流水线吃什么参数 */
.params-block {
  margin-bottom: 14px;
  font-size: 13px;
}
.params-title {
  font-size: 12px;
  font-weight: 600;
  color: var(--ink-3);
  margin-bottom: 6px;
}
.params-item {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}
.params-block .params-item + .params-item {
  margin-top: 6px;
}
.params-code {
  font-family: var(--font-mono);
  font-size: 12px;
  padding: 2px 8px;
  border: 1px solid var(--line);
  border-radius: 6px;
  max-width: 100%;
  overflow: auto;
}
.source-panel {
  margin-top: 18px;
}
.source {
  margin: 0;
  white-space: pre-wrap;
  word-break: break-all;
  font-size: 12px;
  line-height: 1.6;
  color: var(--ink-2);
  font-family: var(--font-mono);
  max-height: 420px;
  overflow: auto;
}
</style>

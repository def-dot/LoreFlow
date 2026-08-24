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
    <!-- description 是整条流水线的导语：正文字号置顶，先读说明再看图，
         图中的重试/条件标记才有解释；参数另起 panel 卡片，与导语明确分区 -->
    <p v-if="detail.description" class="desc">{{ detail.description }}</p>
    <!-- 运行时参数：与下方「流水线/节点」同级的 panel，规格表行式排版 ——
         首行 label（*/可选）+ ctx 键名芯片 + 默认值靠右，次行说明文字。
         与新建运行的参数表单同一套必填/可选语言 -->
    <section v-if="hasParams" class="panel params-panel">
      <h2>运行时参数</h2>
      <div v-for="p in detail.params" :key="p.name" class="param">
        <div class="param-head">
          <span class="param-label">
            {{ p.label }}<span v-if="p.required" class="param-star">*</span>
            <span v-else class="param-optional">可选</span>
          </span>
          <code class="param-key">{{ p.name }}</code>
          <span v-if="p.has_default" class="param-default">默认 {{ defaultPreview(p.default) }}</span>
          <span v-else-if="!p.required" class="param-default">不填则不传</span>
        </div>
        <p v-if="p.description" class="param-desc">{{ p.description }}</p>
      </div>
    </section>
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
          <el-table-column label="类型" min-width="130">
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
/* 导语：正文字号 + 舒展行高，读起来是「说明文」而非界面杂讯 */
.desc {
  margin: 0 0 18px;
  font-size: 13px;
  line-height: 1.7;
  color: var(--ink-2);
}
/* human 节点说明下方的审核视图行 */
.review-view {
  font-size: 12px;
  margin-top: 2px;
}
/* 运行时参数：与图/节点同级的 panel；行式规格表，发丝线分行 */
.params-panel {
  margin-bottom: 18px;
}
.param + .param {
  margin-top: 10px;
  padding-top: 10px;
  border-top: 1px solid var(--line);
}
/* 首行：label + 键名 + 默认值（靠右），基线对齐成一栏规格 */
.param-head {
  display: flex;
  align-items: baseline;
  gap: 8px;
  min-width: 0;
}
.param-label {
  font-size: 13px;
  font-weight: 600;
  color: var(--ink);
}
.param-star {
  color: #ff8f8a;
}
.param-optional {
  font-weight: 400;
  font-size: 11px;
  color: var(--ink-3);
}
/* ctx 键名：等宽小芯片，键就是要敲进 JSON/表单的内容 */
.param-key {
  flex: none;
  font-family: var(--font-mono);
  font-size: 11.5px;
  color: var(--ink-3);
  padding: 1px 6px;
  border: 1px solid var(--line);
  border-radius: 5px;
}
/* 默认值靠右当「值」列：过长省略号截断（全量在下方 YAML 源码） */
.param-default {
  margin-left: auto;
  flex: none;
  max-width: 50%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-family: var(--font-mono);
  font-size: 11.5px;
  color: var(--ink-3);
}
/* 次行：说明文字，弱一档灰 */
.param-desc {
  margin: 4px 0 0;
  font-size: 12px;
  line-height: 1.6;
  color: var(--ink-3);
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

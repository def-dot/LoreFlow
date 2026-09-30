<script setup lang="ts">
import { computed, ref } from 'vue'
import type { JsonSchema, SourceInfo } from '@/api/nodeTypes'
import SchemaFields from './SchemaFields.vue'

/** NodeTypeCard 兼容节点类型与工具（两者的展示字段一致） */
interface CardNode {
  name: string
  label: string
  description: string
  input_schema?: JsonSchema | null
  output_schema?: JsonSchema | null
  roles?: string[]
  source?: SourceInfo
}

const props = defineProps<{
  node: CardNode
  variant?: 'func' | 'plugin'
}>()

/** 默认收起，点标题行才展开 schema —— 目录要能扫，不是参考手册 */
const expanded = ref(false)

function outputType(schema: JsonSchema): string {
  if (schema.type === 'array' && schema.items) return `list[${schema.items.type ?? '?'}]`
  return schema.type ?? '?'
}

const roles = computed(() => props.node.roles ?? ['node'])

/** 徽章只标例外：双端注册是常态，不标；单端才提示 */
const roleNote = computed(() => {
  const isNode = roles.value.includes('node')
  const isTool = roles.value.includes('tool')
  if (isNode && isTool) return ''
  if (isNode) return '仅节点'
  if (isTool) return '仅工具'
  return ''
})

/** label 与 name 相同就不重复渲染（MCP 工具的 label 就是 name） */
const showLabel = computed(() => props.node.label && props.node.label !== props.node.name)
</script>

<template>
  <div class="node-card" :class="{ open: expanded }" @click="expanded = !expanded">
    <div class="node-card-head">
      <svg class="chev" :class="{ open: expanded }" width="10" height="10" viewBox="0 0 10 10" aria-hidden="true">
        <path d="M3 2 L7 5 L3 8" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round" />
      </svg>
      <el-tag :class="`node-tag node-tag--${variant ?? 'func'}`" disable-transitions>{{ node.name }}</el-tag>
      <span v-if="showLabel" class="node-label">{{ node.label }}</span>
      <span v-if="roleNote" class="role-note">{{ roleNote }}</span>
    </div>
    <p class="node-desc">{{ node.description }}</p>

    <div v-if="expanded" class="node-card-body">
      <div v-if="node.input_schema?.properties && Object.keys(node.input_schema.properties).length" class="schema-section">
        <h3 class="schema-title">输入</h3>
        <SchemaFields :schema="node.input_schema" />
      </div>
      <div v-else-if="node.input_schema?.additionalProperties" class="schema-section">
        <h3 class="schema-title">输入</h3>
        <span class="schema-dynamic">动态参数（用户定义）</span>
      </div>
      <div v-else class="schema-section">
        <h3 class="schema-title">输入</h3>
        <span class="schema-empty">无</span>
      </div>

      <div v-if="node.output_schema" class="schema-section">
        <h3 class="schema-title">输出</h3>
        <SchemaFields v-if="node.output_schema.properties && Object.keys(node.output_schema.properties).length" :schema="node.output_schema" />
        <template v-else-if="node.output_schema.type === 'array' && node.output_schema.items?.properties">
          <div class="nc-field">
            <span class="nc-type">list[object]</span>
          </div>
          <SchemaFields :schema="node.output_schema.items" :defs="node.output_schema.$defs" :depth="1" />
        </template>
        <div v-else-if="node.output_schema.additionalProperties" class="nc-field">
          <span class="schema-dynamic">动态参数（用户定义）</span>
        </div>
        <div v-else class="nc-field">
          <span class="nc-type">{{ outputType(node.output_schema) }}</span>
          <span v-if="node.output_schema.description" class="nc-desc">{{ node.output_schema.description }}</span>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.node-card {
  background: transparent;
  border: 1px solid rgba(255, 255, 255, 0.07);
  border-radius: 8px;
  padding: 8px 10px;
  cursor: pointer;
  user-select: none;
}
.node-card.open {
  background: rgba(12, 17, 32, 0.5);
  border-color: rgba(255, 255, 255, 0.12);
}
.node-card-head {
  display: flex;
  align-items: baseline;
  gap: 8px;
  flex-wrap: wrap;
}
.chev {
  flex: none;
  align-self: center;
  color: var(--ink-3);
  transition: transform 0.15s;
}
.chev.open {
  transform: rotate(90deg);
}
.node-label {
  font-size: 13px;
  font-weight: 500;
  color: var(--ink);
}
.role-note {
  margin-left: auto;
  font-size: 10.5px;
  color: var(--ink-3);
  letter-spacing: 0.5px;
  flex-shrink: 0;
}
.node-desc {
  margin: 4px 0 0 18px;
  font-size: 12px;
  line-height: 1.6;
  color: var(--ink-3);
}
.schema-section {
  margin-top: 10px;
  padding-left: 18px;
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
.schema-dynamic {
  font-size: 11px;
  color: var(--ink-3);
  font-style: italic;
}
.nc-field {
  display: flex;
  align-items: baseline;
  gap: 6px;
  font-size: 12px;
  line-height: 1.6;
}
.nc-type {
  font-family: var(--font-mono);
  font-size: 11px;
  color: var(--ink-3);
  flex-shrink: 0;
}
.nc-desc {
  font-size: 11px;
  color: var(--ink-3);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.node-tag {
  font-family: var(--font-mono);
  border: none;
}
.node-tag--func {
  --el-tag-bg-color: rgba(64, 158, 255, 0.12);
  --el-tag-border-color: transparent;
  --el-tag-text-color: #79bbff;
}
.node-tag--plugin {
  --el-tag-bg-color: rgba(103, 194, 58, 0.12);
  --el-tag-border-color: transparent;
  --el-tag-text-color: #67c23a;
}
</style>

<script setup lang="ts">
import type { NodeTypeInfo } from '@/api/nodeTypes'
import SchemaFields from './SchemaFields.vue'

defineProps<{
  node: NodeTypeInfo
  variant?: 'func' | 'plugin'
}>()

function outputType(schema: NonNullable<NodeTypeInfo['output_schema']>): string {
  if (schema.type === 'list' && schema.item) return `list[${schema.item.type}]`
  return schema.type
}
</script>

<template>
  <div class="node-card">
    <div class="node-card-head">
      <el-tag :class="`node-tag node-tag--${variant ?? 'func'}`" disable-transitions>{{ node.name }}</el-tag>
      <span class="node-label">{{ node.label }}</span>
    </div>
    <p class="node-desc">{{ node.description }}</p>

    <div v-if="node.input_schema && Object.keys(node.input_schema).length" class="schema-section">
      <h3 class="schema-title">输入</h3>
      <SchemaFields :fields="node.input_schema" />
    </div>
    <div v-else class="schema-section">
      <h3 class="schema-title">输入</h3>
      <span class="schema-empty">无</span>
    </div>

    <div v-if="node.output_schema" class="schema-section">
      <h3 class="schema-title">输出</h3>
      <SchemaFields v-if="node.output_schema.fields" :fields="node.output_schema.fields" />
      <template v-else-if="node.output_schema.type === 'list' && node.output_schema.item?.fields">
        <div class="nc-field">
          <span class="nc-type">list[object]</span>
        </div>
        <SchemaFields :fields="node.output_schema.item.fields" :depth="1" />
      </template>
      <div v-else class="nc-field">
        <span class="nc-type">{{ outputType(node.output_schema) }}</span>
        <span v-if="node.output_schema.description" class="nc-desc">{{ node.output_schema.description }}</span>
      </div>
    </div>
  </div>
</template>

<style scoped>
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

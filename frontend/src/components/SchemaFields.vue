<script setup lang="ts">
import type { SchemaField } from '@/api/nodeTypes'

defineProps<{
  fields: Record<string, SchemaField>
  depth?: number
}>()

function typeLabel(field: SchemaField): string {
  if (field.type === 'list' && field.item) return `list[${field.item.type}]`
  return field.type
}
</script>

<template>
  <div v-for="(field, key) in fields" :key="key" class="sf-row" :style="{ marginLeft: depth ? '14px' : '0' }">
    <div class="sf-field">
      <span class="sf-key">{{ key }}</span>
      <span class="sf-type">{{ typeLabel(field) }}</span>
      <span v-if="field.required" class="sf-req">*</span>
      <span v-if="field.description" class="sf-desc">{{ field.description }}</span>
    </div>
    <SchemaFields v-if="field.type === 'object' && field.fields" :fields="field.fields" :depth="(depth ?? 0) + 1" />
    <SchemaFields v-else-if="field.type === 'list' && field.item?.fields" :fields="field.item.fields" :depth="(depth ?? 0) + 1" />
  </div>
</template>

<style scoped>
.sf-field {
  display: flex;
  align-items: baseline;
  gap: 6px;
  font-size: 12px;
  line-height: 1.6;
}
.sf-key {
  font-family: var(--font-mono);
  font-size: 11.5px;
  color: var(--ink);
  flex-shrink: 0;
}
.sf-type {
  font-family: var(--font-mono);
  font-size: 11px;
  color: var(--ink-3);
  flex-shrink: 0;
}
.sf-req {
  color: #ff8f8a;
  font-size: 11px;
  flex-shrink: 0;
}
.sf-desc {
  font-size: 11px;
  color: var(--ink-3);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
</style>

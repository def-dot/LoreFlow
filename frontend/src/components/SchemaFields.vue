<script setup lang="ts">
import type { JsonSchema } from '@/api/nodeTypes'

const props = defineProps<{
  schema: JsonSchema
  /** 顶层 $defs，递归时始终保持根引用 */
  defs?: Record<string, JsonSchema>
  depth?: number
}>()

const rootDefs = () => props.defs ?? props.schema.$defs

function resolveRef(schema: JsonSchema): JsonSchema {
  if (!schema.$ref) return schema
  const defs = rootDefs()
  if (!defs) return schema
  const name = schema.$ref.replace(/^#\/\$defs\//, '')
  return defs[name] ?? schema
}

function unwrap(schema: JsonSchema): JsonSchema {
  if (schema.anyOf) {
    const nonNull = schema.anyOf.filter(s => s.type !== 'null')
    if (nonNull.length === 1) {
      // Preserve outer fields (description, title, default) that live on
      // the anyOf wrapper but not inside the remaining branch.
      return { ...schema, ...nonNull[0], anyOf: undefined }
    }
  }
  return schema
}

function effective(schema: JsonSchema): JsonSchema {
  let s = unwrap(schema)
  if (s.$ref) s = resolveRef(s)
  // 解引用后再 unwrap（$ref 目标可能带 anyOf）
  s = unwrap(s)
  return s
}

function isRequired(name: string): boolean {
  return props.schema.required?.includes(name) ?? false
}

function typeLabel(field: JsonSchema): string {
  const f = effective(field)
  if (f.type === 'array' && f.items) return `list[${effective(f.items).type ?? '?'}]`
  return f.type ?? '?'
}

function isObject(field: JsonSchema): boolean {
  const f = effective(field)
  return f.type === 'object' && !!f.properties
}

function isObjectArray(field: JsonSchema): boolean {
  const f = effective(field)
  return f.type === 'array' && !!f.items && !!effective(f.items!).properties
}
</script>

<template>
  <div
    v-for="(field, key) in schema.properties"
    :key="key"
    class="sf-row"
    :style="{ marginLeft: depth ? '14px' : '0' }"
  >
    <div class="sf-field">
      <span class="sf-key">{{ key }}</span>
      <span class="sf-type">{{ typeLabel(field) }}</span>
      <span v-if="isRequired(String(key))" class="sf-req">*</span>
      <span v-if="effective(field).description" class="sf-desc">{{ effective(field).description }}</span>
    </div>
    <SchemaFields
      v-if="isObject(field)"
      :schema="effective(field)"
      :defs="rootDefs()"
      :depth="(depth ?? 0) + 1"
    />
    <SchemaFields
      v-else-if="isObjectArray(field)"
      :schema="effective(effective(field).items!)"
      :defs="rootDefs()"
      :depth="(depth ?? 0) + 1"
    />
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

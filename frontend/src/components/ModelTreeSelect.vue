<script setup lang="ts">
import { computed } from 'vue'
import type { ModelItem } from '@/api/providers'

/** 模型树形下拉：provider 为分组节点（不可选），model 为叶子节点 */
const props = defineProps<{
  models: ModelItem[]
  modelValue: string | null
  placeholder?: string
}>()

const emit = defineEmits<{
  'update:modelValue': [value: string | null]
}>()

interface TreeNode {
  id: number | string
  label: string
  disabled?: boolean
  children?: TreeNode[]
}

const treeData = computed<TreeNode[]>(() => {
  const groups = new Map<number, { label: string; children: TreeNode[] }>()
  for (const m of props.models) {
    const g = groups.get(m.provider_id) ?? {
      label: m.provider?.name ?? `Provider ${m.provider_id}`,
      children: [],
    }
    g.children.push({ id: m.model_key, label: m.name })
    groups.set(m.provider_id, g)
  }
  return [...groups.entries()].map(([pid, g]) => ({
    id: `p-${pid}`,
    label: g.label,
    disabled: true,
    children: g.children,
  }))
})

function filterNode(query: string, data: TreeNode): boolean {
  if (!query) return true
  return data.label.toLowerCase().includes(query.toLowerCase())
}
</script>

<template>
  <el-tree-select
    :model-value="modelValue"
    :data="treeData"
    :props="{ value: 'id', label: 'label', children: 'children', disabled: 'disabled' }"
    node-key="id"
    :filter-node-method="filterNode"
    popper-class="model-tree-popper"
    default-expand-all
    filterable
    clearable
    :placeholder="placeholder"
    @update:model-value="emit('update:modelValue', $event ?? null)"
  />
</template>

<style>
/* 下拉面板被 teleport 到 body，scoped 样式够不到 */
.model-tree-popper .el-tree-node.is-disabled > .el-tree-node__content {
  cursor: default;
}
.model-tree-popper .el-tree-node.is-disabled > .el-tree-node__content .el-tree-node__label {
  color: var(--ink-2);
  opacity: 1;
  font-weight: 600;
}
</style>

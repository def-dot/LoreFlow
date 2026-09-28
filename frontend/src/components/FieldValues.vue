<script setup lang="ts">
import { computed } from 'vue'

// 标注键值的逐字段渲染：label + 值（文本块 / JSON / 未提供）。
// 供审核卡片（payload 声明视图）与运行详情（inputs 快照）共用同一视觉语言。
export interface FieldValue {
  key: string
  label: string
  value: unknown
  required?: boolean
}

const props = defineProps<{
  fields: FieldValue[]
  /** 审核修订草稿（键 → 改后文本）：传入即开启字符串字段的行内编辑 */
  drafts?: Record<string, string>
}>()

interface RenderedField extends FieldValue {
  kind: 'empty' | 'text' | 'json'
  text: string  // kind=text 的正文 / kind=json 的 pretty JSON
}

const rendered = computed<RenderedField[]>(() =>
  props.fields.map((field) => {
    const value = field.value
    if (value === null || value === undefined) {
      return { ...field, kind: 'empty', text: '' }
    }
    // 简单值直接显示：字符串、数字、布尔
    if (typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean') {
      return { ...field, kind: 'text', text: String(value) }
    }
    return { ...field, kind: 'json', text: JSON.stringify(value, null, 2) }
  }),
)
</script>

<template>
  <div class="fields">
    <div v-for="field in rendered" :key="field.key" class="field">
      <div class="field-head">
        <span class="field-label">
          {{ field.label }}<span v-if="field.required" class="field-star">*</span>
        </span>
      </div>
      <div v-if="field.kind === 'empty'" class="field-empty">未提供</div>
      <!-- 审核场景：文本字段可就地修改（改动随「通过」提交，改过的高亮） -->
      <el-input
        v-else-if="field.kind === 'text' && drafts"
        v-model="drafts[field.key]"
        type="textarea"
        :autosize="{ minRows: 2, maxRows: 10 }"
        resize="vertical"
        class="field-edit"
        :class="{ edited: drafts[field.key] !== field.text }"
      />
      <div v-else-if="field.kind === 'text'" class="field-text">{{ field.text }}</div>
      <div v-else class="field-text json-text">{{ field.text }}</div>
    </div>
  </div>
</template>

<style scoped>
.fields {
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.field-head {
  display: flex;
  align-items: baseline;
  gap: 6px;
  margin-bottom: 4px;
}
.field-label {
  font-size: 12.5px;
  font-weight: 600;
  color: var(--ink-2);
}
.field-star {
  color: #ff8f8a;
}
.field-key {
  font-family: var(--font-mono);
  font-size: 11px;
  color: var(--ink-3);
}
.field-text {
  white-space: pre-wrap;
  word-break: break-word;
  font-size: 13px;
  line-height: 1.6;
  color: var(--ink);
  padding: 8px 10px;
  background: #0c1122;
  border: 1px solid #1a2038;
  border-radius: 8px;
  max-height: 220px;
  overflow: auto;
}
.json-text {
  font-family: var(--font-mono);
  font-size: 12px;
}
.field-empty {
  font-size: 12.5px;
  color: var(--ink-3);
  padding: 6px 0;
}
/* 审核修订输入框：与只读文本同底色；有改动时琥珀描边（HITL 语义色） */
.field-edit :deep(.el-textarea__inner) {
  font-size: 13px;
  line-height: 1.6;
  color: var(--ink);
  background: #0c1122;
  border-color: #1a2038;
}
.field-edit.edited :deep(.el-textarea__inner) {
  border-color: var(--amber);
}
</style>

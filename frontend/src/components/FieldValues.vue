<script setup lang="ts">
import { computed } from 'vue'

// 标注键值的逐字段渲染：label + 值（文本块 / 折叠 JSON / 未提供）。
// 供审核卡片（payload 声明视图）与运行详情（inputs 快照）共用同一视觉语言。
export interface FieldValue {
  key: string
  label: string
  value: unknown
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
    if (typeof value === 'string') {
      return { ...field, kind: 'text', text: value }
    }
    return { ...field, kind: 'json', text: JSON.stringify(value, null, 2) }
  }),
)

// 折叠标题：结构化字段的体积提示（字符数）
function jsonTitle(field: RenderedField): string {
  return `${field.text.length} 字符 · JSON`
}
</script>

<template>
  <div class="fields">
    <div v-for="field in rendered" :key="field.key" class="field">
      <div class="field-head">
        <span class="field-label">{{ field.label }}</span>
        <code v-if="field.label !== field.key" class="field-key">{{ field.key }}</code>
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
      <el-collapse v-else class="field-json">
        <el-collapse-item :title="jsonTitle(field)">
          <pre class="json">{{ field.text }}</pre>
        </el-collapse-item>
      </el-collapse>
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
.json {
  margin: 0;
  max-height: 200px;
  overflow: auto;
  font-family: var(--font-mono);
  font-size: 11.5px;
  line-height: 1.55;
  color: var(--ink-2);
  white-space: pre-wrap;
  word-break: break-all;
}
/* 字段内的折叠 JSON：去底色、贴暗色风格 */
.field-json {
  border-top: none;
}
.field-json :deep(.el-collapse-item__header) {
  background: transparent;
  border-bottom: 1px solid var(--line);
  color: var(--ink-3);
  font-size: 12px;
  height: 30px;
  line-height: 30px;
}
.field-json :deep(.el-collapse-item__wrap) {
  background: transparent;
  border-bottom: 1px solid var(--line);
}
.field-json :deep(.el-collapse-item__content) {
  padding-bottom: 8px;
}
</style>

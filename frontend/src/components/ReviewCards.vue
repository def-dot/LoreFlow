<script setup lang="ts">
import { reactive } from 'vue'

defineProps<{ reviewing: { name: string; payload: unknown }[]; deciding: boolean }>()

const emit = defineEmits<{ decide: [node: string, approve: boolean, reason: string | null] }>()

const reasons = reactive<Record<string, string>>({})

// 待审内容转 JSON 文本；无 payload 时留空
function payloadText(payload: unknown): string {
  return payload === null || payload === undefined ? '' : JSON.stringify(payload, null, 1)
}
</script>

<template>
  <div v-if="!reviewing.length" class="muted">暂无待审核节点。</div>
  <div v-for="item in reviewing" :key="item.name" class="review-card">
    <h3>{{ item.name }}</h3>
    <pre class="payload">{{ payloadText(item.payload) }}</pre>
    <el-input v-model="reasons[item.name]" placeholder="拒绝原因（可选）" size="small" />
    <div class="buttons">
      <el-button
        type="success"
        size="small"
        :loading="deciding"
        :disabled="deciding"
        @click="emit('decide', item.name, true, null)"
      >
        ✓ 通过
      </el-button>
      <el-button
        type="danger"
        size="small"
        :loading="deciding"
        :disabled="deciding"
        @click="emit('decide', item.name, false, reasons[item.name] || null)"
      >
        ✕ 驳回
      </el-button>
    </div>
  </div>
</template>

<style scoped>
/* 人工介入时刻：抬升面板 + 琥珀左条（与 running 的青色形成机器/人对位） */
.review-card {
  background: rgba(22, 28, 54, 0.85);
  border: 1px solid var(--line);
  border-left: 3px solid var(--amber);
  border-radius: 10px;
  padding: 12px 14px;
  margin-bottom: 12px;
}
.review-card h3 {
  margin: 0 0 8px;
  font-size: 13.5px;
  font-weight: 600;
  color: var(--ink);
}
.payload {
  margin: 0 0 10px;
  max-height: 200px;
  overflow: auto;
  padding: 10px;
  background: #0c1122;
  border: 1px solid #1a2038;
  border-radius: 8px;
  color: var(--ink-2);
  font-family: var(--font-mono);
  font-size: 11.5px;
  line-height: 1.55;
  white-space: pre-wrap;
}
.buttons {
  margin-top: 10px;
  display: flex;
  gap: 8px;
}
</style>

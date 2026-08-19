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
  <div v-if="!reviewing.length" class="muted">No review awaiting decision.</div>
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
        ✓ Approve
      </el-button>
      <el-button
        type="danger"
        size="small"
        :loading="deciding"
        :disabled="deciding"
        @click="emit('decide', item.name, false, reasons[item.name] || null)"
      >
        ✕ Reject
      </el-button>
    </div>
  </div>
</template>

<style scoped>
.review-card {
  border: 1px solid #3a4150;
  border-radius: 8px;
  padding: 10px 12px;
  margin-bottom: 12px;
}
.review-card h3 {
  margin: 0 0 8px;
  font-size: 14px;
}
.payload {
  margin: 0 0 10px;
  max-height: 200px;
  overflow: auto;
  padding: 8px;
  background: #16181d;
  border: 1px solid #2a2f38;
  border-radius: 6px;
  color: #9aa4b2;
  font-size: 12px;
  white-space: pre-wrap;
}
.buttons {
  margin-top: 10px;
  display: flex;
  gap: 8px;
}
</style>

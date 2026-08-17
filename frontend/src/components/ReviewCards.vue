<script setup lang="ts">
import { reactive } from 'vue'

defineProps<{ reviewing: string[] }>()

const emit = defineEmits<{ decide: [node: string, approve: boolean, reason: string | null] }>()

const reasons = reactive<Record<string, string>>({})
</script>

<template>
  <div v-if="!reviewing.length" class="muted">No review awaiting decision.</div>
  <div v-for="node in reviewing" :key="node" class="review-card">
    <h3>{{ node }}</h3>
    <el-input v-model="reasons[node]" placeholder="拒绝原因（可选）" size="small" />
    <div class="buttons">
      <el-button type="success" size="small" @click="emit('decide', node, true, null)">✓ Approve</el-button>
      <el-button type="danger" size="small" @click="emit('decide', node, false, reasons[node] || null)">
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
.buttons {
  margin-top: 10px;
  display: flex;
  gap: 8px;
}
</style>

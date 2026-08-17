<script setup lang="ts">
import type { RunListItem } from '@/api/runs'
import { statusTagType } from '@/utils/status'

defineProps<{ runs: RunListItem[]; selectedId: number | null }>()

const emit = defineEmits<{ select: [id: number] }>()
</script>

<template>
  <aside class="run-side">
    <div class="head">
      <h2>Runs</h2>
      <span class="muted">{{ runs.length }}</span>
    </div>
    <div class="run-list">
      <div
        v-for="r in runs"
        :key="r.id"
        class="run-item"
        :class="{ sel: r.id === selectedId }"
        @click="emit('select', r.id)"
      >
        <el-tag :type="statusTagType(r.status)" size="small" disable-transitions>{{ r.status }}</el-tag>
        <span class="rid">{{ r.name }} · {{ r.created_at ?? '' }}</span>
      </div>
      <div v-if="!runs.length" class="muted">No runs yet — click “New run” to start one.</div>
    </div>
  </aside>
</template>

<style scoped>
.run-side {
  background: #1a1d23;
  border: 1px solid #2a2f38;
  border-radius: 10px;
  padding: 14px;
  align-self: start;
}
.head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 10px;
}
.run-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.run-item {
  padding: 8px 10px;
  border: 1px solid #2a2f38;
  border-radius: 8px;
  cursor: pointer;
  display: flex;
  align-items: center;
  gap: 8px;
}
.run-item:hover {
  border-color: #5a6577;
}
.run-item.sel {
  border-color: #3b82f6;
  background: #1c2433;
}
.rid {
  font-size: 12px;
  color: #9aa4b2;
}
</style>

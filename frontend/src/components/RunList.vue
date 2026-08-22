<script setup lang="ts">
import type { RunListItem } from '@/api/runs'
import { statusColor } from '@/utils/status'

defineProps<{ runs: RunListItem[]; total: number; selectedId: number | null }>()

const emit = defineEmits<{ select: [id: number] }>()

// 侧栏时间只留一小格（MM-DD HH:mm），完整时间放 title 悬停查看
function fmtShort(iso: string | null): string {
  if (!iso) return ''
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  const p = (n: number) => String(n).padStart(2, '0')
  return `${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`
}

function fmtFull(iso: string | null): string {
  if (!iso) return ''
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString()
}
</script>

<template>
  <aside class="run-side">
    <div class="head">
      <h2>Runs</h2>
      <span class="muted">{{ total }}</span>
    </div>
    <div class="run-list">
      <div
        v-for="r in runs"
        :key="r.id"
        class="run-item"
        :class="{ sel: r.id === selectedId }"
        @click="emit('select', r.id)"
      >
        <span
          class="dot"
          :class="{ live: r.status === 'running' || r.status === 'reviewing' }"
          :style="{ background: statusColor(r.status) }"
          :title="r.status"
        ></span>
        <span class="name" :title="r.name">{{ r.name }}</span>
        <span class="time" :title="fmtFull(r.created_at)">{{ fmtShort(r.created_at) }}</span>
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
.dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  flex: none;
}
.dot.live {
  animation: pulse 1.2s ease-in-out infinite;
}
@keyframes pulse {
  0%,
  100% {
    opacity: 1;
  }
  50% {
    opacity: 0.35;
  }
}
.name {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 12px;
  color: #c6cdd8;
}
.time {
  flex: none;
  font-size: 11px;
  color: #6f7885;
  font-variant-numeric: tabular-nums;
}
</style>

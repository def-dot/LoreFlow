<script setup lang="ts">
import { computed } from 'vue'
import type { RunListItem } from '@/api/runs'
import { statusColor, statusLabel } from '@/utils/status'

const props = defineProps<{
  runs: RunListItem[]
  total: number
  selectedId: number | null
  loadingMore: boolean
  /** 筛选值（'' = 全部），由 store 持有 */
  status: string
  configFile: string
  /** 流水线下拉选项（来自 pipelines 目录） */
  pipelineOptions: { filename: string; name: string }[]
}>()

const emit = defineEmits<{
  select: [id: number]
  delete: [id: number]
  more: []
  'set-status': [v: string]
  'set-config': [v: string]
}>()

// 终态才可删（后端同样拒绝非终态）：运行中/待审核的行不出现删除按钮
const TERMINAL = new Set(['completed', 'failed', 'cancelled'])

// 状态筛选项：值用后端枚举，标签走统一中文映射
const STATUS_OPTIONS = ['running', 'reviewing', 'completed', 'failed', 'cancelled'].map((v) => ({
  value: v,
  label: statusLabel(v),
}))

// 有筛选时空态文案不同：不是"没有运行"，是"没有匹配的运行"
const hasFilter = computed(() => props.status !== '' || props.configFile !== '')

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
      <h2>运行记录</h2>
      <span class="muted total">{{ total }}</span>
    </div>
    <div class="filters">
      <el-select
        :model-value="status"
        size="small"
        @update:model-value="emit('set-status', $event ?? '')"
      >
        <el-option label="全部状态" value="" />
        <el-option v-for="o in STATUS_OPTIONS" :key="o.value" :label="o.label" :value="o.value" />
      </el-select>
      <el-select
        :model-value="configFile"
        size="small"
        :disabled="!pipelineOptions.length"
        @update:model-value="emit('set-config', $event ?? '')"
      >
        <el-option label="全部流水线" value="" />
        <el-option v-for="p in pipelineOptions" :key="p.filename" :label="p.name" :value="p.filename" />
      </el-select>
    </div>
    <div class="run-list">
      <div
        v-for="r in runs"
        :key="r.id"
        class="run-item"
        :class="{ sel: r.id === selectedId }"
        role="button"
        tabindex="0"
        @click="emit('select', r.id)"
        @keydown.enter="emit('select', r.id)"
      >
        <span
          class="dot"
          :class="{ live: r.status === 'running' || r.status === 'reviewing' }"
          :style="{ background: statusColor(r.status) }"
          :title="statusLabel(r.status)"
        ></span>
        <span class="name" :title="r.name">{{ r.name }}</span>
        <span class="idx">#{{ r.id }}</span>
        <span class="time" :title="fmtFull(r.created_at)">{{ fmtShort(r.created_at) }}</span>
        <button
          v-if="TERMINAL.has(r.status)"
          class="del"
          title="删除该记录"
          aria-label="删除该记录"
          @click.stop="emit('delete', r.id)"
          @keydown.enter.stop
        >
          ✕
        </button>
      </div>
      <div v-if="!runs.length" class="muted empty">
        {{ hasFilter ? '没有符合筛选的运行记录。' : '暂无运行记录 — 点击「新建运行」发起一个。' }}
      </div>
    </div>
    <!-- 截断修复：还有更早的 run 时提供翻页入口，并明示已加载进度 -->
    <div v-if="runs.length < total" class="load-more">
      <span class="muted count">{{ runs.length }} / {{ total }}</span>
      <el-button size="small" plain :loading="loadingMore" @click="emit('more')">加载更多</el-button>
    </div>
  </aside>
</template>

<style scoped>
/* 半透明面板浮在图纸网格上。sticky + 内部滚动：加载上百条后
 * 侧栏不再把页面撑高，滚右侧详情时列表保持在视口内 */
.run-side {
  position: sticky;
  top: 14px;
  max-height: calc(100vh - 28px); /* 视口顶 14 + 底 14 呼吸位 */
  display: flex;
  flex-direction: column;
  overflow: hidden; /* 圆角裁住内部滚动区 */
  background: rgba(16, 21, 42, 0.72);
  border: 1px solid var(--line);
  border-radius: 12px;
  align-self: start;
}
/* 头部：标题 + 计数（计数是数据 → mono） */
.head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 12px 14px 10px;
}
.head h2 {
  margin: 0;
}
/* 选中行的青色左轨已是本面板唯一的竖向电流标记，标题不再叠
 * 全局 h2::before 刻度条，避免同一列出现两枚竖杠 */
.head h2::before {
  content: none;
}
.head .total {
  font-family: var(--font-mono);
  font-size: 11px;
  font-variant-numeric: tabular-nums;
}
/* 两枚小筛选平分侧栏宽度；''（全部）为显式选项，不用 clearable 兜边界。
 * 底部发丝线把「控件区」和「内容区」分开 */
.filters {
  display: flex;
  gap: 6px;
  padding: 0 14px 12px;
  border-bottom: 1px solid var(--line);
}
.filters :deep(.el-select) {
  flex: 1;
  min-width: 0;
}
/* 列表区吃掉剩余高度自己滚；min-height:0 是 flex 子项可收缩的前提 */
.run-list {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 10px 14px;
}
.empty {
  padding: 8px 2px;
  font-size: 12px;
  line-height: 1.6;
}
/* 加载更多钉在面板底部，不随列表滚走 */
.load-more {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 10px;
  padding: 10px 14px;
  border-top: 1px solid var(--line);
}
.load-more .count {
  font-family: var(--font-mono);
  font-size: 11px;
}
.run-item {
  position: relative;
  padding: 7px 10px;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: rgba(16, 21, 42, 0.5);
  cursor: pointer;
  display: flex;
  align-items: center;
  gap: 8px;
  flex: none; /* 滚动容器里的行不参与压缩 */
}
.run-item:hover {
  border-color: var(--line-strong);
  background: var(--panel-2);
}
.run-item:focus-visible {
  outline: 2px solid rgba(77, 196, 178, 0.5);
  outline-offset: 1px;
}
.run-item.sel {
  border-color: var(--line-strong);
  background: var(--panel-2);
}
/* 选中行的青色左轨（电流所在支路） */
.run-item.sel::before {
  content: '';
  position: absolute;
  left: -1px;
  top: 8px;
  bottom: 8px;
  width: 3px;
  border-radius: 2px;
  background: var(--accent);
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
  font-size: 12.5px;
  color: var(--ink-2);
}
.run-item.sel .name {
  color: var(--ink);
}
/* 行内 #id：同一流水线反复运行时名字全一样，id 是唯一区分 */
.idx {
  flex: none;
  font-family: var(--font-mono);
  font-size: 11px;
  color: var(--ink-3);
  font-variant-numeric: tabular-nums;
}
.time {
  flex: none;
  font-family: var(--font-mono);
  font-size: 11px;
  color: var(--ink-3);
  font-variant-numeric: tabular-nums;
}
/* 行内删除：默认隐形（列表以浏览为主），hover/focus 才出现；
 * 只占一枚符号宽，避免挤压 name/time 的常规布局 */
.del {
  flex: none;
  width: 18px;
  height: 18px;
  margin-right: -4px; /* 吃掉按钮自身视觉宽度，不 hover 时布局零变化 */
  padding: 0;
  border: none;
  border-radius: 4px;
  background: transparent;
  color: var(--ink-3);
  font-size: 12px;
  line-height: 1;
  cursor: pointer;
  opacity: 0;
  transition: opacity 0.15s ease;
}
.run-item:hover .del,
.del:focus-visible {
  opacity: 1;
}
.del:hover {
  color: #ff8f8a;
  background: rgba(239, 115, 112, 0.12);
}
.del:focus-visible {
  outline: 1px solid rgba(239, 115, 112, 0.5);
}
/* 单列布局下 sticky 全高侧栏会盖住页面：回到普通流，
 * 列表限高避免几百条把详情推到屏幕外 */
@media (max-width: 900px) {
  .run-side {
    position: static;
    max-height: none;
  }
  .run-list {
    max-height: 420px;
  }
}
</style>

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
  pipeline: string
}>()

const emit = defineEmits<{
  select: [id: number]
  delete: [id: number]
  cancel: [id: number]
  more: []
  'set-status': [v: string]
  'set-pipeline': [v: string]
}>()

// 可取消状态：running 或 reviewing；其余（终态）可删，后端同样按此拒绝
const CANCELLABLE = new Set(['running', 'reviewing'])

// 状态筛选项：值用后端枚举，标签走统一中文映射
const STATUS_OPTIONS = ['running', 'reviewing', 'completed', 'failed', 'cancelled'].map((v) => ({
  value: v,
  label: statusLabel(v),
}))

// 从 runs 列表中提取不重复的工作流名称作为筛选选项
const pipelineOptions = computed(() => {
  const names = new Set(props.runs.map((r) => r.pipeline).filter(Boolean))
  return [...names].sort()
})

// 有筛选时空态文案不同：不是"没有运行"，是"没有匹配的运行"
const hasFilter = computed(() => props.status !== '' || props.pipeline !== '')

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
        :model-value="pipeline"
        size="small"
        :disabled="!pipelineOptions.length"
        @update:model-value="emit('set-pipeline', $event ?? '')"
      >
        <el-option label="全部工作流" value="" />
        <el-option v-for="name in pipelineOptions" :key="name" :label="name" :value="name" />
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
        <!-- 身份组：name + #id 紧贴常显。id 是同名 run 的唯一区分，
             hover 展开操作按钮时恰恰是确认目标的时候，不能随时间淡出 -->
        <div class="who">
          <span class="name" :title="r.name">{{ r.name }}</span>
          <span class="idx">#{{ r.id }}</span>
        </div>
        <!-- 尾槽：时间与操作按钮叠放同一格（grid 同槽），非 hover 显示
             时间，hover 交叉淡变为按钮 —— 替换而非遮盖，不依赖背景色 -->
        <div class="tail">
          <span class="time" :title="fmtFull(r.created_at)">{{ fmtShort(r.created_at) }}</span>
          <div class="actions">
            <button
              v-if="CANCELLABLE.has(r.status)"
              class="act cancel"
              title="取消运行"
              aria-label="取消运行"
              @click.stop="emit('cancel', r.id)"
              @keydown.enter.stop
            >
              ⏹
            </button>
            <!-- v-else = 非 running/reviewing，即终态，可删（后端同样拒绝非终态） -->
            <button
              v-else
              class="del"
              title="删除该记录"
              aria-label="删除该记录"
              @click.stop="emit('delete', r.id)"
              @keydown.enter.stop
            >
              ✕
            </button>
          </div>
        </div>
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
/* 身份组：可伸缩可收缩，内部 name 超长省略、#id 始终可见不被吞。
 * 不设 flex-grow —— 右侧剩余空白由 .tail 的 margin-left:auto 吸收，
 * 使 id 紧贴 name 实际宽度而非被推去贴时间 */
.who {
  flex: 0 1 auto;
  min-width: 0;
  display: flex;
  align-items: center;
  gap: 5px;
}
.who .name {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 12.5px;
  color: var(--ink-2);
}
.run-item.sel .who .name {
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
/* 尾槽：吃掉 name 的剩余空间把整组推到最右，内部 grid 叠放 ——
 * 时间与操作按钮占同一格，hover 交叉淡变（时间淡出、按钮淡入）。
 * 是替换而非遮盖，不依赖任何背景色；槽宽取两者较大者，
 * 按钮数量增加时槽随之变宽，行整体布局不动 */
.tail {
  flex: none;
  margin-left: auto;
  display: grid;
  justify-items: end; /* 时间与按钮都贴右缘对齐 */
}
.tail > * {
  grid-area: 1 / 1;
}
.time {
  font-family: var(--font-mono);
  font-size: 11px;
  color: var(--ink-3);
  font-variant-numeric: tabular-nums;
  transition: opacity 0.15s ease;
}
.actions {
  display: flex;
  align-items: center;
  gap: 4px;
  opacity: 0;
  pointer-events: none;
  transition: opacity 0.15s ease;
}
/* hover（或键盘 tab 进行使按钮聚焦）时切换显示。
 * 淡出侧掐掉事件：opacity 0 的元素仍会弹 title 提示、挡住下层 */
.run-item:hover .time,
.run-item:focus-within .time {
  opacity: 0;
  pointer-events: none;
}
.run-item:hover .actions,
.run-item:focus-within .actions {
  opacity: 1;
  pointer-events: auto;
}
/* 触摸设备没有 hover：按钮常显、时间让位（时间在右侧详情面板有完整版） */
@media (hover: none) {
  .time {
    opacity: 0;
    pointer-events: none;
  }
  .actions {
    opacity: 1;
    pointer-events: auto;
  }
}
/* 行内小操作按钮：透明底图标，hover 才上色 */
.act,
.del {
  width: 18px;
  height: 18px;
  padding: 0;
  border: none;
  border-radius: 4px;
  background: transparent;
  color: var(--ink-3);
  line-height: 1;
  cursor: pointer;
  transition:
    color 0.15s ease,
    background-color 0.15s ease,
    transform 0.15s ease;
  display: flex;
  align-items: center;
  justify-content: center;
}

.act {
  font-size: 14px; /* 取消按钮图标稍大 */
}

.del {
  font-size: 12px; /* 删除按钮图标 */
}

.del:hover {
  color: #ff8f8a;
  background: rgba(239, 115, 112, 0.15);
  transform: scale(1.05);
}

.del:focus-visible {
  outline: 1px solid rgba(239, 115, 112, 0.5);
}

.act:hover {
  color: var(--ink);
  background: rgba(139, 144, 176, 0.15);
  transform: scale(1.05);
}

.act:focus-visible {
  outline: 1px solid rgba(139, 144, 176, 0.5);
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

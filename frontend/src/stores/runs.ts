import { defineStore } from 'pinia'
import { approve, cancelRun, deleteRun, getRun, listRuns, startRun } from '@/api/runs'
import type { RunDetail, RunListItem } from '@/api/runs'

/**
 * run 列表与详情状态。轮询定时器由视图层管理（Runs.vue），
 * 这里只负责取数（沿用旧 index.html 的两路 1s 轮询语义）。
 */
const PAGE_SIZE = 50

export const useRunsStore = defineStore('runs', {
  state: () => ({
    runs: [] as RunListItem[],
    total: 0,
    // 下一页 offset：独立于 runs.length —— 翻页间隙有新 run 插入时，
    // 去重后 length 不涨，若拿它当 offset 会反复请求同一页
    offset: 0,
    // 列表筛选（'' = 不筛）；total 语义为筛选后总数
    filters: { status: '', pipeline: '' },
    // 全局执行计数（后端 summary，不受筛选影响）：轮询与电流的真值来源
    summary: { running: 0, active: 0 },
    detail: null as RunDetail | null,
    selectedId: null as number | null,
    deciding: false, // 审批请求进行中：按钮防重复点击
    loadingMore: false, // 加载更多进行中：按钮防重复点击
  }),

  getters: {
    // 轮询启停看全局 summary 而非当前列表：筛"失败"时运行中的 run
    // 依然存在，轮询不能停，否则 run 失败后列表不会自动补进它
    hasRunning: (state) => state.summary.running > 0,
    // 未完结（running / reviewing 等）→ 驱动导航电流
    hasActive: (state) => state.summary.active > 0,
  },

  actions: {
    async fetchRuns() {
      // 刷新（含 1s 轮询）：固定取第一页 PAGE_SIZE；已滚出的后续页
      // 由「加载更多」按钮再拉。原地更新避免数组引用变化导致整个列表重新渲染（闪烁）
      const data = await listRuns(0, PAGE_SIZE, this.filters)
      this.runs.splice(0, this.runs.length, ...data.items)
      this.offset = data.items.length
      this.total = data.total
      this.summary = data.summary
    },

    /** 追加下一页（列表底部「加载更多」按钮触发）。按 id 去重：翻页间隙
     * 有新 run 插入时，offset 边界处的旧项会在两页各出现一次。offset 按
     * 返回条数推进（不能用 runs.length，去重后不涨会卡死在同一页）。 */
    async loadMoreRuns() {
      if (this.loadingMore || this.offset >= this.total) return
      this.loadingMore = true
      try {
        const data = await listRuns(this.offset, PAGE_SIZE, this.filters)
        this.offset += data.items.length
        const seen = new Set(this.runs.map((r) => r.id))
        this.runs.push(...data.items.filter((r) => !seen.has(r.id)))
        this.total = data.total
        this.summary = data.summary
      } finally {
        this.loadingMore = false
      }
    },

    /** 切换筛选：清空已加载页回到第一页，再按新条件取数。 */
    async setFilters(partial: Partial<{ status: string; pipeline: string }>) {
      this.filters = { ...this.filters, ...partial }
      this.runs = []
      this.offset = 0
      await this.fetchRuns()
    },

    async select(id: number) {
      this.selectedId = id
      await this.fetchDetail()
    },

    async fetchDetail() {
      if (this.selectedId === null) return
      this.detail = await getRun(this.selectedId)
    },

    /** 删除一条终态 run：本地同步移除（total 为筛选后口径，同步减一）；
     * 删的是当前选中项时清空选中与详情，右侧回到空态。 */
    async removeRun(id: number) {
      await deleteRun(id)
      this.runs = this.runs.filter((r) => r.id !== id)
      this.total = Math.max(0, this.total - 1)
      if (this.selectedId === id) {
        this.selectedId = null
        this.detail = null
      }
    },

    async startNewRun(pipelineId: number, inputs?: Record<string, unknown>, name?: string) {
      const { run_id } = await startRun(pipelineId, inputs, name)
      await this.select(run_id)
      await this.fetchRuns()
    },

    async decide(node: string, ok: boolean, reason: string | null, values?: Record<string, string> | null) {
      if (this.selectedId === null || this.deciding) return
      this.deciding = true
      try {
        await approve(this.selectedId, node, ok, ok ? null : reason, values ?? undefined)
        await this.fetchDetail()
      } finally {
        this.deciding = false
      }
    },

    /** 取消运行中或待审核的 run：调用后端标记为 CANCELLED，详情页会立即更新状态。 */
    async cancelRun(id: number) {
      const { data } = await cancelRun(id)
      // 同步更新本地详情（立即反馈）+ 列表该条目状态（列表轮询也会更新，但这里主动同步）
      if (this.selectedId === id) this.detail = data
      const idx = this.runs.findIndex((r) => r.id === id)
      if (idx !== -1) this.runs[idx] = { ...this.runs[idx], status: data.status, finished_at: data.finished_at }
    },
  },
})

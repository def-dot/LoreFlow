import { defineStore } from 'pinia'
import { approve, getRun, listRuns, startRun } from '@/api/runs'
import type { RunDetail, RunListItem } from '@/api/runs'

/**
 * run 列表与详情状态。轮询定时器由视图层管理（Runs.vue），
 * 这里只负责取数（沿用旧 index.html 的两路 1s 轮询语义）。
 */
export const useRunsStore = defineStore('runs', {
  state: () => ({
    runs: [] as RunListItem[],
    total: 0,
    detail: null as RunDetail | null,
    selectedId: null as number | null,
    deciding: false, // 审批请求进行中：按钮防重复点击
  }),

  getters: {
    hasRunning: (state) => state.runs.some((r) => r.status === 'running'),
  },

  actions: {
    async fetchRuns() {
      const data = await listRuns()
      this.runs = data.items
      this.total = data.total
    },

    async select(id: number) {
      this.selectedId = id
      this.detail = null
      await this.fetchDetail()
    },

    async fetchDetail() {
      if (this.selectedId === null) return
      this.detail = await getRun(this.selectedId)
    },

    async startNewRun(configFile: string) {
      const { run_id } = await startRun(configFile)
      await this.select(run_id)
      await this.fetchRuns()
    },

    async decide(node: string, ok: boolean, reason: string | null) {
      if (this.selectedId === null || this.deciding) return
      this.deciding = true
      try {
        await approve(this.selectedId, node, ok, ok ? null : (reason ?? 'Rejected in web UI'))
        await this.fetchDetail()
      } finally {
        this.deciding = false
      }
    },
  },
})

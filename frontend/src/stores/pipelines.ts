import { defineStore } from 'pinia'
import { getPipeline, listPipelines } from '@/api/pipelines'
import type { PipelineDetail, PipelineListItem } from '@/api/pipelines'

/**
 * demo 流水线目录与详情（只读浏览）。列表供 Runs 页下拉选择，
 * 详情供 Runs 页预览 drawer 展示 DAG/节点表/YAML 源码。
 */
export const usePipelinesStore = defineStore('pipelines', {
  state: () => ({
    pipelines: [] as PipelineListItem[],
    detail: null as PipelineDetail | null,
    selectedFile: null as string | null,
    loaded: false,
  }),

  actions: {
    async fetchPipelines() {
      const data = await listPipelines()
      this.pipelines = data.pipelines
      this.loaded = true
    },

    async select(filename: string) {
      this.selectedFile = filename
      this.detail = null
      this.detail = await getPipeline(filename)
    },
  },
})

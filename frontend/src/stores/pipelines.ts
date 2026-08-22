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
    // 按文件名缓存详情：demo 流水线是静态文件，重复打开免请求
    detailCache: {} as Record<string, PipelineDetail>,
    selectedFile: null as string | null,
    loaded: false,
  }),

  actions: {
    async fetchPipelines() {
      const data = await listPipelines()
      this.pipelines = data.pipelines
      this.loaded = true
    },

    /** 选中并加载详情（SWR）：有缓存先秒显，同时后台重取 —— 改过 YAML
     *  无需手动刷新；竞态防护：响应回来时用户已切走就不覆盖。 */
    async select(filename: string) {
      this.selectedFile = filename
      const cached = this.detailCache[filename]
      if (cached) this.detail = cached
      const detail = await getPipeline(filename)
      this.detailCache[filename] = detail
      if (this.selectedFile === filename) {
        this.detail = detail
      }
    },
  },
})

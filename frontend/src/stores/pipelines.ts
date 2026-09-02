import { defineStore } from 'pinia'
import { ref } from 'vue'
import type { PipelineDetail, PipelineListItem } from '@/api/pipelines'
import { getPipeline, listPipelines } from '@/api/pipelines'

export const usePipelinesStore = defineStore('pipelines', () => {
  const pipelines = ref<PipelineListItem[]>([])
  const selectedName = ref<string>('')
  const detailCache = ref<Record<string, PipelineDetail>>({})
  const loaded = ref(false)

  async function fetchPipelines() {
    const data = await listPipelines()
    pipelines.value = data.pipelines
    loaded.value = true
  }

  async function select(name: string) {
    selectedName.value = name
    if (!name) return
    // SWR: 秒出缓存，后台刷新
    if (detailCache.value[name]) return
    detailCache.value[name] = await getPipeline(name)
  }

  return {
    pipelines,
    selectedName,
    detailCache,
    loaded,
    fetchPipelines,
    select,
  }
})

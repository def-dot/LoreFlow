import { defineStore } from 'pinia'
import { ref } from 'vue'
import type { PipelineDetail, PipelineListItem } from '@/api/pipelines'
import { getPipeline, listPipelines } from '@/api/pipelines'

export const usePipelinesStore = defineStore('pipelines', () => {
  const pipelines = ref<PipelineListItem[]>([])
  const selectedName = ref<string>('')
  const detail = ref<PipelineDetail | null>(null)
  const loaded = ref(false)

  async function fetchPipelines() {
    const data = await listPipelines()
    pipelines.value = data
    loaded.value = true
  }

  async function select(name: string) {
    selectedName.value = name
    if (!name) return
    detail.value = await getPipeline(name)
  }

  return {
    pipelines,
    selectedName,
    detail,
    loaded,
    fetchPipelines,
    select,
  }
})

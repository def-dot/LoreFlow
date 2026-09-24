import { defineStore } from 'pinia'
import { ref } from 'vue'
import type { PipelineDetail, PipelineListItem } from '@/api/pipelines'
import { getPipeline, listPipelines } from '@/api/pipelines'

export const usePipelinesStore = defineStore('pipelines', () => {
  const pipelines = ref<PipelineListItem[]>([])
  const selectedId = ref<number | null>(null)
  const detail = ref<PipelineDetail | null>(null)
  const loaded = ref(false)

  const selectedName = ref('')

  async function fetchPipelines(q?: string) {
    const data = await listPipelines(q)
    pipelines.value = data
    loaded.value = true
  }

  async function select(id: number) {
    selectedId.value = id
    const item = pipelines.value.find((p) => p.id === id)
    selectedName.value = item?.name ?? ''
    detail.value = await getPipeline(id)
  }

  return {
    pipelines,
    selectedId,
    selectedName,
    detail,
    loaded,
    fetchPipelines,
    select,
  }
})

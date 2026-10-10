<script setup lang="ts">
import { ref, watch, nextTick } from 'vue'
import { ElMessage } from 'element-plus'
import { importModels, type AvailableModel } from '@/api/providers'

const props = defineProps<{
  visible: boolean
  providerId: number | null
  models: AvailableModel[]
}>()

const emit = defineEmits<{
  'update:visible': [val: boolean]
  imported: []
}>()

const selected = ref<AvailableModel[]>([])
const types = ref<Record<string, string>>({})
const importing = ref(false)

watch(
  () => props.visible,
  async (v) => {
    if (v) {
      types.value = Object.fromEntries(props.models.map((m) => [m.name, m.model_type]))
      await nextTick()
      // 已导入的默认勾选
      selected.value = props.models.filter((m) => m.imported)
    }
  },
)

function onSelectionChange(rows: AvailableModel[]) {
  selected.value = rows
}

const newSelected = () => selected.value.filter((m) => !m.imported)

async function handleImport() {
  const items = newSelected()
  if (!props.providerId || !items.length) return
  importing.value = true
  try {
    await importModels(
      props.providerId,
      items.map((m) => ({
        name: m.name,
        model_type: (types.value[m.name] || 'chat') as 'chat' | 'embedding' | 'rerank',
      })),
    )
    ElMessage.success(`已导入 ${items.length} 个模型`)
    emit('update:visible', false)
    emit('imported')
  } catch (e: any) {
    ElMessage.error(e?.message || '导入失败')
  } finally {
    importing.value = false
  }
}

const typeOptions = [
  { label: '文本生成', value: 'chat' },
  { label: 'Embedding', value: 'embedding' },
  { label: 'Rerank', value: 'rerank' },
]
</script>

<template>
  <el-dialog
    :model-value="visible"
    title="同步模型"
    width="560px"
    :close-on-click-modal="false"
    @update:model-value="emit('update:visible', $event)"
  >
    <p class="hint">
      共 {{ models.length }} 个模型，已导入 {{ models.filter((m) => m.imported).length }} 个。
    </p>
    <el-table :data="models" size="small" max-height="360" @selection-change="onSelectionChange">
      <el-table-column type="selection" width="40" />
      <el-table-column prop="name" label="模型名称" min-width="220" show-overflow-tooltip />
      <el-table-column label="类型" width="140">
        <template #default="{ row }">
          <el-select v-if="!row.imported" v-model="types[row.name]" size="small">
            <el-option
              v-for="opt in typeOptions"
              :key="opt.value"
              :label="opt.label"
              :value="opt.value"
            />
          </el-select>
          <el-tag v-else size="small" type="info">已导入</el-tag>
        </template>
      </el-table-column>
    </el-table>
    <template #footer>
      <el-button @click="emit('update:visible', false)">取消</el-button>
      <el-button
        type="primary"
        :loading="importing"
        :disabled="!newSelected().length"
        @click="handleImport"
      >
        导入 ({{ newSelected().length }})
      </el-button>
    </template>
  </el-dialog>
</template>

<style scoped>
.hint {
  font-size: 13px;
  color: var(--ink-2);
  margin: 0 0 12px;
}
</style>

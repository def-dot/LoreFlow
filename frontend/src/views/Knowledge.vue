<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  UploadFilled, Delete, Refresh, RefreshRight,
  VideoPause, Document, View, Search,
} from '@element-plus/icons-vue'
import type { UploadFile } from 'element-plus'
import {
  listAllDocuments,
  deleteDocument,
  uploadDocumentDirect,
  cancelDocument,
  retryDocument,
  type DocumentItem,
} from '@/api/knowledge'

const router = useRouter()

const documents = ref<DocumentItem[]>([])
const loading = ref(false)
const uploading = ref(false)
const searchFileName = ref('')
const filterStatus = ref('')
let pollTimer: ReturnType<typeof setInterval> | null = null
let searchDebounce: ReturnType<typeof setTimeout> | null = null

const page = ref(1)
const pageSize = 20
const total = ref(0)

const hasPending = computed(() =>
  documents.value.some(d => d.status === 'pending' || d.status === 'processing')
)

const statusMap: Record<string, { label: string; type: '' | 'success' | 'warning' | 'danger' | 'info' }> = {
  pending:    { label: '排队中', type: 'info' },
  processing: { label: '解析中', type: 'warning' },
  completed:  { label: '已完成', type: 'success' },
  failed:     { label: '失败',   type: 'danger' },
  cancelled:  { label: '已取消', type: '' },
}

async function fetchDocuments() {
  loading.value = true
  try {
    const res = await listAllDocuments({
      limit: pageSize,
      offset: (page.value - 1) * pageSize,
      status: filterStatus.value || undefined,
    })
    // 前端按文件名过滤（后端暂不支持文件名搜索）
    const q = searchFileName.value.trim().toLowerCase()
    documents.value = q
      ? res.items.filter(d => d.filename.toLowerCase().includes(q))
      : res.items
    total.value = res.total
  } finally {
    loading.value = false
  }
}

function onSearchChange() {
  if (searchDebounce) clearTimeout(searchDebounce)
  searchDebounce = setTimeout(() => {
    page.value = 1
    fetchDocuments()
  }, 300)
}

function onStatusChange() {
  page.value = 1
  fetchDocuments()
}

async function handleUpload(uploadFile: UploadFile) {
  const file = uploadFile.raw
  if (!file) return

  uploading.value = true
  try {
    await uploadDocumentDirect(file)
    ElMessage.success(`${file.name} 已上传，后台正在处理`)
    await fetchDocuments()
  } catch (err: any) {
    ElMessage.error(err.message || '上传失败')
  } finally {
    uploading.value = false
  }
}

async function handleCancel(row: DocumentItem) {
  try {
    await ElMessageBox.confirm(`确定要取消「${row.filename}」的解析吗？`, '取消解析', {
      confirmButtonText: '确定',
      cancelButtonText: '返回',
      type: 'warning',
    })
    await cancelDocument(row.id)
    ElMessage.success('已取消')
    await fetchDocuments()
  } catch { /* 用户取消 */ }
}

async function handleRetry(row: DocumentItem) {
  try {
    await retryDocument(row.id)
    ElMessage.success('已重新加入队列')
    await fetchDocuments()
  } catch { /* interceptor handles */ }
}

async function handleDelete(row: DocumentItem) {
  await ElMessageBox.confirm(`确定删除文档「${row.filename}」及其所有切片？`, '删除确认', {
    type: 'warning',
  })
  try {
    await deleteDocument(row.id)
    ElMessage.success('删除成功')
    await fetchDocuments()
  } catch { /* interceptor handles */ }
}

function openChunks(row: DocumentItem) {
  if (!row.chunk_count) return
  router.push({ name: 'Chunks', params: { id: row.id }, query: { name: row.filename } })
}

function openParseView(row: DocumentItem) {
  router.push({ name: 'ParseView', params: { id: row.id }, query: { name: row.filename } })
}

function formatFileSize(bytes: number | null): string {
  if (!bytes) return '-'
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

function formatDuration(ms: number | null): string {
  if (!ms) return '-'
  if (ms < 1000) return `${ms}ms`
  return `${(ms / 1000).toFixed(1)}s`
}

function formatTime(value: string | null): string {
  if (!value) return ''
  const d = new Date(value)
  if (Number.isNaN(d.getTime())) return ''
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

function startPolling() {
  pollTimer = setInterval(async () => {
    if (!hasPending.value) return
    try {
      const res = await listAllDocuments({
        limit: pageSize,
        offset: (page.value - 1) * pageSize,
        status: filterStatus.value || undefined,
      })
      documents.value = res.items
    } catch { /* silent */ }
  }, 10000)
}

onMounted(async () => {
  await fetchDocuments()
  startPolling()
})

onUnmounted(() => {
  if (pollTimer) clearInterval(pollTimer)
  if (searchDebounce) clearTimeout(searchDebounce)
})
</script>

<template>
  <div class="knowledge-page">
    <!-- 上传区 -->
    <el-card class="upload-card" shadow="never">
      <el-upload
        drag
        :auto-upload="false"
        :show-file-list="false"
        :on-change="handleUpload"
        accept=".txt,.md,.pdf"
        :disabled="uploading"
      >
        <div class="upload-content">
          <div class="upload-icon-wrap">
            <el-icon :size="26"><UploadFilled /></el-icon>
          </div>
          <p class="upload-text">拖拽文件到此处，或 <em>点击上传</em></p>
          <div class="upload-formats">
            <span class="fmt">TXT</span>
            <span class="fmt">MD</span>
            <span class="fmt">PDF</span>
          </div>
        </div>
      </el-upload>
    </el-card>

    <!-- 文档列表 -->
    <el-card shadow="never" class="table-card">
      <template #header>
        <div class="card-header">
          <div class="card-title">
            <span class="title-text">已入库文档</span>
            <span class="count-chip">{{ total }}</span>
          </div>
          <div class="card-filters">
            <el-input
              v-model="searchFileName"
              placeholder="搜索文件名…"
              :prefix-icon="Search"
              clearable
              size="small"
              class="filter-input"
              @input="onSearchChange"
              @clear="onSearchChange"
            />
            <el-select
              v-model="filterStatus"
              placeholder="全部状态"
              clearable
              size="small"
              class="filter-select"
              @change="onStatusChange"
              @clear="onStatusChange"
            >
              <el-option label="排队中" value="pending" />
              <el-option label="解析中" value="processing" />
              <el-option label="已完成" value="completed" />
              <el-option label="失败" value="failed" />
              <el-option label="已取消" value="cancelled" />
            </el-select>
            <el-button :icon="Refresh" text @click="fetchDocuments" :loading="loading">刷新</el-button>
          </div>
        </div>
      </template>

      <el-table :data="documents" v-loading="loading" stripe empty-text="暂无文档，请上传">
        <el-table-column label="文件名" min-width="220">
          <template #default="{ row }">
            <div class="file-name">
              <el-icon class="file-icon"><Document /></el-icon>
              <span class="file-text">{{ row.filename }}</span>
            </div>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="100" align="center">
          <template #default="{ row }">
            <el-tooltip
              v-if="(row.status === 'failed' || row.status === 'cancelled') && row.error"
              :content="row.error"
              placement="top"
              :show-after="200"
            >
              <el-tag :type="statusMap[row.status]?.type ?? 'info'" size="small" effect="light">
                {{ statusMap[row.status]?.label ?? row.status }}
              </el-tag>
            </el-tooltip>
            <el-tag v-else :type="statusMap[row.status]?.type ?? 'info'" size="small" effect="light">
              {{ statusMap[row.status]?.label ?? row.status }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="切片数" width="90" align="center">
          <template #default="{ row }">
            <span
              class="chunk-count-link"
              :class="{ disabled: !row.chunk_count }"
              @click="openChunks(row)"
            >{{ row.chunk_count }}</span>
          </template>
        </el-table-column>
        <el-table-column label="耗时" width="80" align="right">
          <template #default="{ row }">
            <span class="time-cell">{{ formatDuration(row.parse_duration_ms) }}</span>
          </template>
        </el-table-column>
        <el-table-column label="上传时间" width="150">
          <template #default="{ row }">
            <span v-if="row.created_at" class="time-cell">{{ formatTime(row.created_at) }}</span>
            <span v-else class="text-muted">-</span>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="160" align="center">
          <template #default="{ row }">
            <div class="action-group">
              <template v-if="row.status === 'pending' || row.status === 'processing'">
                <el-tooltip content="取消">
                  <el-button :icon="VideoPause" type="warning" text size="small" @click.stop="handleCancel(row)" />
                </el-tooltip>
              </template>
              <template v-else-if="row.status === 'failed' || row.status === 'cancelled'">
                <el-tooltip content="重试">
                  <el-button :icon="RefreshRight" type="warning" text size="small" class="retry-btn" @click.stop="handleRetry(row)" />
                </el-tooltip>
              </template>
              <template v-else-if="row.status === 'completed'">
                <el-tooltip content="解析对照">
                  <el-button :icon="Document" type="primary" text size="small" class="parse-btn" @click.stop="openParseView(row)" />
                </el-tooltip>
                <el-tooltip content="查看切片">
                  <el-button :icon="View" type="primary" text size="small" class="view-btn" @click.stop="openChunks(row)" />
                </el-tooltip>
              </template>
              <el-tooltip content="删除">
                <el-button :icon="Delete" type="danger" text size="small" @click.stop="handleDelete(row)" />
              </el-tooltip>
            </div>
          </template>
        </el-table-column>
      </el-table>

      <div v-if="total > pageSize" class="table-footer">
        <el-pagination
          v-model:current-page="page"
          :page-size="pageSize"
          :total="total"
          layout="prev, pager, next"
          @current-change="fetchDocuments"
        />
      </div>
    </el-card>
  </div>
</template>

<style scoped>
.knowledge-page {
  max-width: 1180px;
  margin: 0 auto;
}

/* ---- Upload ---- */
.upload-card {
  margin-bottom: 20px;
}

.upload-card :deep(.el-card__body) {
  padding: 12px;
}

.upload-card :deep(.el-upload),
.upload-card :deep(.el-upload-dragger) {
  width: 100%;
}

.upload-card :deep(.el-upload-dragger) {
  padding: 32px 20px;
  border-radius: 8px;
  border-color: var(--line);
  background: var(--panel);
  transition: border-color 0.2s ease, background 0.2s ease;
}

.upload-card :deep(.el-upload-dragger:hover) {
  border-color: var(--accent, #4dc4b2);
  background: rgba(77, 196, 178, 0.04);
}

.upload-content {
  text-align: center;
}

.upload-icon-wrap {
  width: 52px;
  height: 52px;
  margin: 0 auto 12px;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 12px;
  background: rgba(77, 196, 178, 0.08);
  color: var(--accent, #4dc4b2);
  transition: background 0.2s ease;
}

.upload-card :deep(.el-upload-dragger:hover) .upload-icon-wrap {
  background: rgba(77, 196, 178, 0.15);
}

.upload-text {
  color: var(--ink-2);
  font-size: 14px;
}

.upload-text em {
  color: var(--accent, #4dc4b2);
  font-style: normal;
  font-weight: 600;
}

.upload-formats {
  margin-top: 10px;
  display: flex;
  gap: 6px;
  justify-content: center;
}

.fmt {
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 0.04em;
  color: var(--ink-3);
  padding: 2px 8px;
  border: 1px solid var(--line);
  border-radius: 4px;
  background: var(--surface);
}

/* ---- Table card ---- */
.table-card :deep(.el-card__header) {
  padding: 14px 20px;
}

.card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 10px;
}

.card-title {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-shrink: 0;
}

.card-filters {
  display: flex;
  align-items: center;
  gap: 8px;
  flex: 1;
  justify-content: flex-end;
}

.filter-input {
  width: 220px;
}

.filter-select {
  width: 130px;
}

.title-text {
  font-weight: 600;
  color: var(--ink);
  font-size: 15px;
}

.count-chip {
  min-width: 22px;
  height: 22px;
  padding: 0 7px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  font-size: 12px;
  font-weight: 600;
  color: var(--accent, #4dc4b2);
  background: rgba(77, 196, 178, 0.08);
  border-radius: 11px;
}

/* ---- Table cells ---- */
.file-name {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}

.file-icon {
  color: var(--ink-4);
  flex-shrink: 0;
}

.file-text {
  color: var(--ink);
  font-weight: 500;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.action-group {
  display: inline-flex;
  align-items: center;
  gap: 2px;
}

.action-group :deep(.el-button) {
  padding: 4px 6px;
}

.time-cell {
  font-variant-numeric: tabular-nums;
  font-size: 12.5px;
  color: var(--ink-3);
  white-space: nowrap;
}

.text-muted {
  color: var(--ink-4);
}

.table-card :deep(.el-table) {
  font-size: 13.5px;
}

.table-card :deep(.el-table .cell) {
  line-height: 1.5;
}

.table-card :deep(.el-table td.el-table__cell) {
  padding: 11px 0;
}

.chunk-count-link {
  font-variant-numeric: tabular-nums;
  font-weight: 600;
  color: var(--accent, #4dc4b2);
  cursor: pointer;
  transition: opacity 0.15s ease;
}

.chunk-count-link:hover {
  opacity: 0.75;
}

.chunk-count-link.disabled {
  color: var(--ink-4);
  cursor: default;
}

.table-footer {
  display: flex;
  justify-content: center;
  padding-top: 16px;
}

.retry-btn {
  color: #e6a23c !important;
}
.retry-btn:hover {
  color: #f0c78a !important;
}

.view-btn {
  color: #409eff !important;
}
.view-btn:hover {
  color: #79bbff !important;
}

.parse-btn {
  color: #67c23a !important;
}
.parse-btn:hover {
  color: #95d475 !important;
}
</style>
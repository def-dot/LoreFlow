<script setup lang="ts">
import { ref, computed, watch, onMounted, onUnmounted } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  Delete, RefreshRight, VideoPause, Document, View, Search, EditPen, UploadFilled, Loading, Close,
} from '@element-plus/icons-vue'
import {
  listAllDocuments,
  deleteDocument,
  inferTags,
  uploadDocuments,
  cancelDocument,
  retryDocument,
  updateDocumentTags,
  getDocumentStatusCounts,
  listTags,
  createTag,
  updateTag,
  deleteTag,
  type DocumentItem,
  type StatusCounts,
} from '@/api/knowledge'
import { uploadFile } from '@/api/uploads'

const router = useRouter()

// ---- 文档台账 ----

const documents = ref<DocumentItem[]>([])
const loading = ref(false)
const searchFileName = ref('')
const filterStatus = ref('')
const filterTagNames = ref<string[]>([])

const page = ref(1)
const pageSize = 20
const total = ref(0)

const counts = ref<StatusCounts>({ pending: 0, processing: 0, completed: 0, failed: 0, cancelled: 0 })
const corpusTotal = computed(() =>
  counts.value.pending + counts.value.processing + counts.value.completed
    + counts.value.failed + counts.value.cancelled,
)

const STATUS_FACETS = [
  { value: '', label: '全部' },
  { value: 'pending', label: '排队中' },
  { value: 'processing', label: '解析中' },
  { value: 'completed', label: '已完成' },
  { value: 'failed', label: '失败' },
  { value: 'cancelled', label: '已取消' },
] as const

// 行内状态点：颜色即语义（青=机器流转，绿=就绪，红=故障，灰=终止）
const statusMeta: Record<string, { label: string; color: string; live?: boolean }> = {
  pending: { label: '排队中', color: 'var(--slate)' },
  processing: { label: '解析中', color: 'var(--accent)', live: true },
  completed: { label: '已完成', color: 'var(--ok)' },
  failed: { label: '失败', color: 'var(--danger)' },
  cancelled: { label: '已取消', color: 'var(--ink-3)' },
}

const hasFilter = computed(() =>
  !!searchFileName.value.trim() || filterStatus.value !== '' || filterTagNames.value.length > 0,
)

// 用全局状态计数判断是否在解析：翻页/筛选后仍能持续轮询
const hasPending = computed(() => counts.value.pending + counts.value.processing > 0)

async function fetchDocuments(opts: { silent?: boolean } = {}) {
  if (!opts.silent) loading.value = true
  try {
    const res = await listAllDocuments({
      limit: pageSize,
      offset: (page.value - 1) * pageSize,
      status: filterStatus.value || undefined,
      q: searchFileName.value.trim() || undefined,
      tags: filterTagNames.value.length ? filterTagNames.value : undefined,
    })
    documents.value = res.items
    total.value = res.total
  } finally {
    if (!opts.silent) loading.value = false
  }
}

async function fetchCounts() {
  try {
    counts.value = await getDocumentStatusCounts()
  } catch { /* silent */ }
}

function refreshAll(opts: { silent?: boolean } = {}) {
  return Promise.all([fetchDocuments(opts), fetchCounts()])
}

function setStatusFilter(value: string) {
  filterStatus.value = value
  page.value = 1
  fetchDocuments()
}

function onStatusSelectChange() {
  page.value = 1
  fetchDocuments()
}

function toggleTagFilter(tagName: string) {
  const idx = filterTagNames.value.indexOf(tagName)
  if (idx >= 0) filterTagNames.value.splice(idx, 1)
  else filterTagNames.value.push(tagName)
  page.value = 1
  fetchDocuments()
}

function clearFilters() {
  searchFileName.value = ''
  filterStatus.value = ''
  filterTagNames.value = []
  page.value = 1
  fetchDocuments()
}

let searchDebounce: ReturnType<typeof setTimeout> | null = null

function onSearchChange() {
  if (searchDebounce) clearTimeout(searchDebounce)
  searchDebounce = setTimeout(() => {
    page.value = 1
    fetchDocuments()
  }, 300)
}

// ---- 标签 ----

const tags = ref<string[]>([])
const addingTag = ref(false)
const addTagInputRef = ref<any>(null)

async function fetchTags() {
  try {
    tags.value = await listTags()
  } catch { /* silent */ }
}

const newTagName = ref('')

watch(addingTag, (v) => {
  if (v) {
    setTimeout(() => {
      const el = document.querySelector('.tag-bar .tag-edit-input input') as HTMLInputElement | null
      if (el) { el.focus(); el.select() }
    }, 100)
  }
})

function handleTagMenu(command: string, tag: string) {
  if (command === 'edit') startEditTag(tag)
  else if (command === 'delete') handleDeleteTag(tag)
}

function handleAddMenu() {
  newTagName.value = ''
  addingTag.value = true
}

async function confirmAddTag() {
  const name = newTagName.value.trim()
  if (!name) { addingTag.value = false; return }
  try {
    await createTag(name)
    newTagName.value = ''
    addingTag.value = false
    await fetchTags()
    ElMessage.success('标签已创建')
  } catch (err: any) {
    ElMessage.error(err?.response?.data?.msg || err?.message || '创建失败')
  }
}

function cancelAddTag() {
  addingTag.value = false
  newTagName.value = ''
}

async function handleDeleteTag(tag: string) {
  try {
    await ElMessageBox.confirm(`删除标签「${tag}」后，文档上的该标签也会一并移除。`, '删除标签', { type: 'warning' })
    await deleteTag(tag)
    const idx = filterTagNames.value.indexOf(tag)
    if (idx >= 0) filterTagNames.value.splice(idx, 1)
    await Promise.all([fetchTags(), refreshAll()])
    ElMessage.success('已删除')
  } catch { /* 用户取消 */ }
}

// 正在重命名的标签原名；editingTagName 为输入框里的新名
const editingTagOld = ref<string | null>(null)
const editingTagName = ref('')

function startEditTag(tag: string) {
  editingTagOld.value = tag
  editingTagName.value = tag
}

watch(editingTagOld, (oldName) => {
  if (oldName === null) return
  setTimeout(() => {
    const el = document.querySelector('.tag-edit-input input') as HTMLInputElement | null
    if (el) {
      el.focus()
      el.select()
    }
  }, 100)
})

async function confirmEditTag() {
  const oldName = editingTagOld.value
  const name = editingTagName.value.trim()
  if (!oldName || !name) { editingTagOld.value = null; return }
  if (oldName === name) { editingTagOld.value = null; return }
  try {
    await updateTag(oldName, name)
    editingTagOld.value = null
    const fi = filterTagNames.value.indexOf(oldName)
    if (fi >= 0) filterTagNames.value.splice(fi, 1, name)
    await Promise.all([fetchTags(), fetchDocuments()])
    ElMessage.success('已更新')
  } catch (err: any) {
    ElMessage.error(err?.response?.data?.msg || err?.message || '更新失败')
  }
}

function cancelEditTag() {
  editingTagOld.value = null
}

// ---- 入库（上传） ----

const fileInput = ref<HTMLInputElement | null>(null)
const dragging = ref(false)

interface PendingItem {
  store_path: string | null
  filename: string
  error: string | null
  tags: string[]
  tags_loading: boolean
}

const pendingItems = ref<PendingItem[]>([])
const allTags = ref<string[]>([])
const previewing = ref(false)
const uploadDialogVisible = ref(false)
const uploading = ref(false)

function openFilePicker() {
  fileInput.value?.click()
}

function onFilePicked(e: Event) {
  const input = e.target as HTMLInputElement
  if (input.files?.length) acceptFiles([...input.files])
  input.value = ''
}

async function acceptFiles(files: File[]) {
  const ok = files.filter(f => /\.(txt|md|pdf)$/i.test(f.name))
  if (!ok.length) {
    ElMessage.error('仅支持 TXT / MD / PDF 文件')
    return
  }
  if (ok.length < files.length) {
    ElMessage.warning(`已跳过 ${files.length - ok.length} 个不支持的文件`)
  }
  previewing.value = true
  uploadDialogVisible.value = true
  try {
    // 获取全部标签
    const allTagsResp = await listTags()
    allTags.value = allTagsResp

    // 逐文件上传（复用 /uploads 接口）
    const items: PendingItem[] = []
    for (const f of ok) {
      try {
        const rec = await uploadFile(f)
        items.push({ store_path: rec.store_path, filename: rec.filename, error: null, tags: [], tags_loading: true })
      } catch {
        items.push({ store_path: null, filename: f.name, error: '上传失败', tags: [], tags_loading: false })
      }
    }
    pendingItems.value = items

    // 异步推断标签
    const toInfer = items.filter(i => i.store_path && !i.error)
    if (toInfer.length && allTags.value.length) {
      const names = toInfer.map(i => i.store_path!)
      inferTags(names).then(({ items: results }) => {
        for (const r of results) {
          const target = pendingItems.value.find(p => p.store_path === r.store_path)
          if (target) {
            target.tags = r.suggested_tags
            target.tags_loading = false
          }
        }
      }).catch(() => {
        for (const p of pendingItems.value) p.tags_loading = false
      })
    } else {
      for (const p of pendingItems.value) p.tags_loading = false
    }
  } catch {
    uploadDialogVisible.value = false
  } finally {
    previewing.value = false
  }
}

// 整页即落点：拖入时升起扫描纱幕，松手进入入库确认
let dragDepth = 0

function onDragEnter(e: DragEvent) {
  if (!e.dataTransfer?.types.includes('Files')) return
  e.preventDefault()
  dragDepth++
  dragging.value = true
}

function onDragOver(e: DragEvent) {
  if (!e.dataTransfer?.types.includes('Files')) return
  e.preventDefault()
}

function onDragLeave() {
  dragDepth = Math.max(0, dragDepth - 1)
  if (dragDepth === 0) dragging.value = false
}

function onDrop(e: DragEvent) {
  e.preventDefault()
  dragDepth = 0
  dragging.value = false
  const files = e.dataTransfer?.files
  if (files?.length) acceptFiles([...files])
}

function removePendingItem(index: number) {
  pendingItems.value.splice(index, 1)
  if (!pendingItems.value.length) uploadDialogVisible.value = false
}

async function confirmUpload() {
  const valid = pendingItems.value.filter(i => i.store_path && !i.error)
  if (!valid.length) return
  uploading.value = true
  try {
    const items = await uploadDocuments(
      valid.map(i => ({ store_path: i.store_path!, filename: i.filename, tags: i.tags })),
    )

    // 有失败项 → 逐项标红，弹窗保留可重试
    const failed = items.filter(r => r.error != null)
    if (failed.length) {
      for (const f of failed) {
        const target = pendingItems.value.find(p => p.filename === f.filename)
        if (target) target.error = f.error
      }
      ElMessage.error(`未入库：${failed.map(r => r.filename).join('、')}`)
      return
    }

    // 全部成功
    const okCount = items.filter(r => !r.error).length
    ElMessage.success(`${okCount} 个已入队`)
    uploadDialogVisible.value = false
    pendingItems.value = []
    await refreshAll()
  } catch {
    // 网络/服务端错误提示由 request 拦截器统一弹出；保留弹窗可重试
  } finally {
    uploading.value = false
  }
}

function cancelUpload() {
  uploadDialogVisible.value = false
  pendingItems.value = []
}

function togglePreviewTag(item: PendingItem, tagName: string) {
  const idx = item.tags.indexOf(tagName)
  if (idx >= 0) item.tags.splice(idx, 1)
  else item.tags.push(tagName)
}

// ---- 文档操作 ----

async function handleCancel(row: DocumentItem) {
  try {
    await ElMessageBox.confirm(`停止解析「${row.filename}」？`, '停止解析', {
      confirmButtonText: '停止',
      cancelButtonText: '返回',
      type: 'warning',
    })
    await cancelDocument(row.id)
    ElMessage.success('已停止')
    await refreshAll()
  } catch { /* 用户取消 */ }
}

async function handleRetry(row: DocumentItem) {
  try {
    await retryDocument(row.id)
    ElMessage.success('已重新入队')
    await refreshAll()
  } catch { /* interceptor handles */ }
}

async function handleDelete(row: DocumentItem) {
  try {
    await ElMessageBox.confirm(`删除文档「${row.filename}」及其全部切片，不可恢复。`, '删除文档', {
      type: 'warning',
    })
    await deleteDocument(row.id)
    ElMessage.success('已删除')
    await refreshAll()
  } catch { /* 用户取消 */ }
}

function openChunks(row: DocumentItem) {
  if (!row.chunk_count) return
  router.push({ name: 'Chunks', params: { id: row.id }, query: { name: row.filename } })
}

function openParseView(row: DocumentItem) {
  router.push({ name: 'ParseView', params: { id: row.id }, query: { name: row.filename } })
}

function openDocument(row: DocumentItem) {
  if (row.status !== 'completed') return
  if (row.chunk_count) openChunks(row)
  else openParseView(row)
}

// ---- 文档标签编辑 ----

async function handleDocTagChange(row: DocumentItem, tagName: string, checked: boolean) {
  const next = checked
    ? [...row.tags, tagName]
    : row.tags.filter(n => n !== tagName)
  try {
    await updateDocumentTags(row.id, next)
    row.tags = next
  } catch {
    ElMessage.error('更新标签失败')
  }
}

// ---- 格式化 ----

function formatFileSize(bytes: number | null): string {
  if (!bytes) return '-'
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

function formatDuration(ms: number | null): string {
  if (!ms) return '-'
  if (ms < 1000) return `${ms} ms`
  return `${(ms / 1000).toFixed(1)} s`
}

function formatTime(value: string | null): string {
  if (!value) return ''
  const d = new Date(value)
  if (Number.isNaN(d.getTime())) return ''
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

// ---- 轮询 ----

let pollTimer: ReturnType<typeof setInterval> | null = null

function startPolling() {
  pollTimer = setInterval(() => {
    if (!hasPending.value) return
    refreshAll({ silent: true })
  }, 10000)
}

onMounted(async () => {
  await Promise.all([refreshAll(), fetchTags()])
  startPolling()
})

onUnmounted(() => {
  if (pollTimer) clearInterval(pollTimer)
  if (searchDebounce) clearTimeout(searchDebounce)
})
</script>

<template>
  <div
    class="knowledge-page"
    @dragenter="onDragEnter"
    @dragover="onDragOver"
    @dragleave="onDragLeave"
    @drop="onDrop"
  >
    <header class="page-head">
      <div class="head-info">
        <h1>知识库</h1>
        <span class="muted">语料入库与切片管理，供工作流与智能体检索。</span>
      </div>
    </header>

    <!-- 文档台账 -->
    <main class="ledger">
      <div class="toolbar">
        <div class="toolbar-row">
          <div class="bar-left">
            <span class="bar-title">已入库文档 <span class="bar-count">共 {{ total }} 个</span></span>
            <el-input
              v-model="searchFileName"
              placeholder="搜索文件名"
              :prefix-icon="Search"
              clearable
              size="small"
              class="search"
              @input="onSearchChange"
              @clear="onSearchChange"
            />
            <el-select
              v-model="filterStatus"
              placeholder="全部状态"
              clearable
              size="small"
              class="status-select"
              @change="onStatusSelectChange"
            >
              <el-option
                v-for="f in STATUS_FACETS"
                :key="f.value"
                :label="f.label"
                :value="f.value"
              />
            </el-select>
          </div>
          <div class="bar-right">
            <el-button type="primary" size="small" :icon="UploadFilled" @click="openFilePicker">上传文档</el-button>
            <input
              ref="fileInput"
              type="file"
              class="file-input"
              accept=".txt,.md,.pdf"
              multiple
              @change="onFilePicked"
            />
          </div>
        </div>
        <div class="toolbar-row tag-row">
          <template v-for="t in tags" :key="t">
            <el-dropdown v-if="editingTagOld !== t" trigger="contextmenu" @command="handleTagMenu($event, t)">
              <button
                type="button"
                class="tag-chip"
                :class="{ on: filterTagNames.includes(t) }"
                @click="toggleTagFilter(t)"
              >{{ t }}</button>
              <template #dropdown>
                <el-dropdown-menu>
                  <el-dropdown-item :icon="EditPen" command="edit">编辑</el-dropdown-item>
                  <el-dropdown-item :icon="Delete" command="delete" divided>删除</el-dropdown-item>
                </el-dropdown-menu>
              </template>
            </el-dropdown>
            <el-input
              v-else
              v-model="editingTagName"
              size="small"
              class="tag-edit-input"
              @keyup.enter="confirmEditTag"
              @keyup.escape="cancelEditTag"
              @blur="confirmEditTag"
            />
          </template>
          <button v-if="!addingTag" type="button" class="tag-chip tag-chip-add" @click="handleAddMenu">+ 标签</button>
          <el-input
            v-else
            ref="addTagInputRef"
            v-model="newTagName"
            size="small"
            class="tag-edit-input"
            placeholder="标签名称"
            maxlength="50"
            @keyup.enter="confirmAddTag"
            @keyup.escape="cancelAddTag"
            @blur="confirmAddTag"
          />
        </div>
      </div>

      <div v-loading="loading" class="ledger-body">
        <el-table :data="documents" size="small" style="width: 100%" empty-text=" ">
          <el-table-column label="文件名" min-width="260">
            <template #default="{ row }">
              <div class="cell-name">
                <span
                  class="doc-name"
                  :class="{ link: row.status === 'completed' }"
                  :title="row.filename"
                  @click="openDocument(row)"
                >{{ row.filename }}</span>
                <span class="doc-tags-wrap">
                  <span v-for="t in row.tags" :key="t" class="doc-tag">{{ t }}</span>
                  <el-popover trigger="click" :width="200">
                    <template #reference>
                      <span class="doc-tag doc-tag-edit">+/-</span>
                    </template>
                  <div class="doc-tag-popover">
                    <label v-for="t in tags" :key="t" class="doc-tag-option">
                      <input
                        type="checkbox"
                        :checked="row.tags.includes(t)"
                        @change="handleDocTagChange(row, t, ($event.target as HTMLInputElement).checked)"
                      />
                      {{ t }}
                    </label>
                    <p v-if="!tags.length" class="muted" style="margin:0;font-size:12px;">暂无标签</p>
                  </div>
                </el-popover>
                </span>
              </div>
              <p v-if="row.error" class="doc-error" :title="row.error">{{ row.error }}</p>
            </template>
          </el-table-column>

          <el-table-column label="状态" width="90" align="center">
            <template #default="{ row }">
              <span class="meta-status" :style="{ color: statusMeta[row.status]?.color }">
                {{ statusMeta[row.status]?.label ?? row.status }}
              </span>
            </template>
          </el-table-column>

          <el-table-column label="切片" width="70" align="right">
            <template #default="{ row }">
              <span class="doc-chunks" :class="{ link: row.chunk_count > 0 }" @click="openChunks(row)">
                {{ row.chunk_count }}
              </span>
            </template>
          </el-table-column>

          <el-table-column label="耗时" width="80" align="center">
            <template #default="{ row }">
              <span class="cell-mono">{{ formatDuration(row.parse_duration_ms) }}</span>
            </template>
          </el-table-column>

          <el-table-column label="大小" width="80" align="center">
            <template #default="{ row }">
              <span class="cell-mono">{{ formatFileSize(row.file_size) }}</span>
            </template>
          </el-table-column>

          <el-table-column label="时间" width="130" align="center">
            <template #default="{ row }">
              <span class="cell-mono">{{ formatTime(row.created_at) }}</span>
            </template>
          </el-table-column>

          <el-table-column label="" width="100" align="right">
            <template #default="{ row }">
              <div class="doc-acts">
                <template v-if="row.status === 'pending' || row.status === 'processing'">
                  <button type="button" class="act warn" title="停止解析" @click="handleCancel(row)">
                    <el-icon><VideoPause /></el-icon>
                  </button>
                </template>
                <template v-else-if="row.status === 'failed' || row.status === 'cancelled'">
                  <button type="button" class="act warn" title="重新解析" @click="handleRetry(row)">
                    <el-icon><RefreshRight /></el-icon>
                  </button>
                </template>
                <template v-else-if="row.status === 'completed'">
                  <button type="button" class="act" title="解析对照" @click="openParseView(row)">
                    <el-icon><Document /></el-icon>
                  </button>
                  <button type="button" class="act" title="查看切片" @click="openChunks(row)">
                    <el-icon><View /></el-icon>
                  </button>
                </template>
                <button type="button" class="act danger" title="删除文档" @click="handleDelete(row)">
                  <el-icon><Delete /></el-icon>
                </button>
              </div>
            </template>
          </el-table-column>
        </el-table>

        <!-- 空态 -->
        <div v-if="!loading && !documents.length" class="empty">
          <template v-if="hasFilter">
            <p>没有匹配的文档。</p>
            <el-button size="small" plain @click="clearFilters">清除筛选</el-button>
          </template>
          <template v-else>
            <p>语料库是空的。</p>
            <p class="muted">点击右上角「上传文档」按钮添加。</p>
          </template>
        </div>
      </div>

      <div v-if="total > pageSize" class="ledger-foot">
        <el-pagination
          v-model:current-page="page"
          :page-size="pageSize"
          :total="total"
          layout="prev, pager, next"
          @current-change="fetchDocuments()"
        />
      </div>
    </main>

    <!-- 拖放纱幕 -->
    <div v-if="dragging" class="drop-veil" aria-hidden="true">
      <div class="veil-frame">
        <span class="veil-text">松开以上传</span>
        <span class="veil-fmt">TXT / MD / PDF</span>
      </div>
    </div>

    <!-- 入库确认 -->
    <el-dialog v-model="uploadDialogVisible" title="入库确认" width="560px" :close-on-click-modal="false" @close="cancelUpload">
      <div class="upload-body">
        <!-- 预览加载中 -->
        <div v-if="previewing" class="preview-loading">
          <el-icon class="is-loading"><Loading /></el-icon>
          <span>正在保存文件…</span>
        </div>

        <!-- 预览结果：逐文件标签 -->
        <template v-else>
          <div v-for="(item, idx) in pendingItems" :key="item.store_path || item.filename" class="preview-file">
            <div class="preview-file-head">
              <el-icon class="file-icon"><Document /></el-icon>
              <span class="file-name" :title="item.filename">{{ item.filename }}</span>
              <span v-if="!item.error && allTags.length" class="doc-tags-wrap">
                <template v-if="item.tags_loading">
                  <el-icon class="is-loading tag-loading-icon"><Loading /></el-icon>
                </template>
                <template v-else>
                  <span v-for="t in item.tags" :key="t" class="doc-tag">{{ t }}</span>
                  <el-popover trigger="click" :width="200">
                    <template #reference>
                      <span class="doc-tag doc-tag-edit">+/-</span>
                    </template>
                    <div class="doc-tag-popover">
                      <label v-for="t in allTags" :key="t" class="doc-tag-option">
                        <input
                          type="checkbox"
                          :checked="item.tags.includes(t)"
                          @change="togglePreviewTag(item, t)"
                        />
                        {{ t }}
                      </label>
                    </div>
                  </el-popover>
                </template>
              </span>
              <span v-if="item.error" class="file-error">{{ item.error }}</span>
              <el-icon class="remove-btn" @click="removePendingItem(idx)"><Close /></el-icon>
            </div>
          </div>
        </template>
      </div>
      <template #footer>
        <el-button @click="cancelUpload">取消</el-button>
        <el-button type="primary" :loading="uploading" :disabled="previewing || pendingItems.some(i => i.error)" @click="confirmUpload">确认入库</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.knowledge-page {
  position: relative;
  display: flex;
  flex-direction: column;
  min-height: calc(100vh - 52px);
  padding: 0 20px 28px;
  max-width: 1280px;
  margin: 0 auto;
}

/* ---- 页眉 ---- */
.page-head {
  display: flex;
  align-items: baseline;
  gap: 14px;
  padding: 14px 0 12px;
}
.head-info {
  display: flex;
  align-items: baseline;
  gap: 12px;
  flex-wrap: wrap;
  flex: 1;
  min-width: 0;
}
.page-head h1 {
  font-size: 18px;
  margin: 0;
}
.page-head .muted {
  font-size: 13px;
}

/* ---- 标签筛选 ---- */
.tag-row {
  gap: 8px;
}
.tag-chip {
  display: inline-flex;
  align-items: center;
  padding: 3px 12px;
  border: 1px solid var(--line);
  border-radius: 999px;
  background: none;
  color: var(--ink-2);
  font-size: 12px;
  cursor: pointer;
  transition: all 0.15s;
}
.tag-chip:hover {
  border-color: var(--ink-3);
  color: var(--ink);
}
.tag-chip.on {
  background: var(--accent);
  border-color: var(--accent);
  color: #fff;
}

.tag-chip-add {
  border-style: dashed;
  color: var(--ink-3);
  padding: 3px 8px;
}
.tag-chip-add:hover {
  border-color: var(--accent);
  color: var(--accent);
}
.tag-edit-input {
  width: 100px;
}

/* ---- 台账 ---- */
.ledger {
  display: flex;
  flex-direction: column;
  min-width: 0;
  margin-top: 16px;
}
.toolbar {
  margin-bottom: 16px;
}
.toolbar-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  flex-wrap: wrap;
}
.toolbar-row + .toolbar-row {
  margin-top: 8px;
  justify-content: flex-start;
}
.toolbar-row:last-child {
  padding-bottom: 10px;
  border-bottom: 1px solid var(--line);
}
.bar-left {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
.bar-title {
  font-size: 14px;
  font-weight: 600;
  color: var(--ink);
}
.bar-count {
  font-family: var(--font-mono);
  font-size: 11px;
  font-variant-numeric: tabular-nums;
  color: var(--ink-2);
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: 999px;
  padding: 1px 8px;
  margin-left: 6px;
  vertical-align: middle;
}
.bar-right {
  display: flex;
  align-items: center;
  gap: 8px;
}
.search {
  width: 200px;
}
.status-select {
  width: 120px;
}
.file-input {
  display: none;
}

.ledger-body {
  min-height: 200px;
}

/* 表格内单元格 */
.cell-name {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}
.doc-name {
  min-width: 0;
  padding: 0;
  border: 0;
  background: none;
  font-family: var(--font-mono);
  font-size: 13px;
  color: var(--ink);
  text-align: left;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  cursor: default;
}
.doc-name.link {
  cursor: pointer;
}
.doc-name.link:hover {
  color: var(--accent);
}
.doc-tag {
  flex: none;
  padding: 1px 8px;
  border: 1px solid var(--line);
  border-radius: 999px;
  font-size: 11px;
  color: var(--ink-3);
}
.doc-tags-wrap {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  cursor: pointer;
  max-width: 260px;
  flex-wrap: wrap;
}
.doc-tag-edit {
  border-style: dashed;
  color: var(--ink-3);
  font-size: 10px;
  padding: 1px 6px;
  opacity: 0;
  transition: opacity 0.15s;
}
.preview-file-head .doc-tag-edit {
  opacity: 1;
}
.doc-tags-wrap:hover .doc-tag-edit {
  opacity: 1;
}
.doc-tag-popover {
  display: flex;
  flex-direction: column;
  gap: 6px;
  max-height: 240px;
  overflow-y: auto;
}
.doc-tag-option {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  cursor: pointer;
}
.doc-chunks {
  font-family: var(--font-mono);
  font-size: 13px;
  font-variant-numeric: tabular-nums;
  color: var(--ink-3);
}
.doc-chunks.link {
  color: var(--accent);
  cursor: pointer;
}
.doc-chunks.link:hover {
  opacity: 0.75;
}
.cell-mono {
  font-family: var(--font-mono);
  font-size: 12px;
  font-variant-numeric: tabular-nums;
  color: var(--ink-3);
}
.meta-status {
  font-size: 12px;
  color: var(--ink-2);
}

.doc-acts {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 2px;
}
.act {
  display: inline-flex;
  padding: 5px;
  border: 0;
  background: none;
  color: var(--ink-2);
  cursor: pointer;
}
.act:hover {
  color: var(--accent);
}
.act.warn:hover {
  color: var(--amber);
}
.act.danger:hover {
  color: var(--danger);
}

.doc-error {
  margin: 4px 0 0;
  font-family: var(--font-mono);
  font-size: 11px;
  line-height: 1.5;
  color: var(--danger);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.empty {
  padding: 48px 12px;
  text-align: center;
}
.empty p {
  margin: 0 0 8px;
  font-size: 13.5px;
}
.empty .muted {
  font-size: 12.5px;
  line-height: 1.7;
}

.ledger-foot {
  display: flex;
  justify-content: center;
  padding-top: 16px;
}

/* ---- 拖放纱幕 ---- */
.drop-veil {
  position: fixed;
  inset: 52px 0 0;
  z-index: 20;
  display: flex;
  align-items: center;
  justify-content: center;
  background: rgba(10, 14, 27, 0.72);
  pointer-events: none;
}
.drop-veil::after {
  content: '';
  position: absolute;
  left: 0;
  right: 0;
  height: 140px;
  background: linear-gradient(180deg, transparent, rgba(77, 196, 178, 0.16), transparent);
  animation: scan 1.5s linear infinite;
}
@keyframes scan {
  from { top: -140px; }
  to { top: 100%; }
}
.veil-frame {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 8px;
  padding: 28px 40px;
  border: 1px dashed var(--accent);
  border-radius: 12px;
}
.veil-text {
  font-size: 15px;
  color: var(--ink);
}
.veil-fmt {
  font-family: var(--font-mono);
  font-size: 11px;
  color: var(--accent);
}

/* ---- 入库确认 ---- */
.upload-body {
  display: flex;
  flex-direction: column;
  gap: 18px;
}
.preview-loading {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 24px 0;
  justify-content: center;
  color: var(--ink-3);
  font-size: 13px;
}
.preview-file {
  border: 1px solid var(--line);
  border-radius: 8px;
  overflow: hidden;
}
.preview-file + .preview-file {
  margin-top: -1px;
}
.preview-file-head {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 12px;
}
.file-icon {
  color: var(--accent);
  flex: none;
}
.file-name {
  flex: 0 1 auto;
  min-width: 0;
  font-family: var(--font-mono);
  font-size: 12.5px;
  color: var(--ink);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.file-error {
  flex: none;
  font-size: 11.5px;
  color: var(--danger, #e5484d);
}
.remove-btn {
  flex: none;
  margin-left: auto;
  cursor: pointer;
  color: var(--ink-3);
  font-size: 14px;
  transition: color 0.15s;
}
.remove-btn:hover {
  color: var(--danger, #e5484d);
}
.tag-loading-icon {
  color: var(--ink-3);
}

/* ---- 窄屏 ---- */
@media (max-width: 700px) {
  .knowledge-page {
    padding: 0 14px 24px;
  }
  .ledger-bar {
    flex-direction: column;
    align-items: flex-start;
  }
  .bar-right {
    width: 100%;
    flex-wrap: wrap;
  }
  .search {
    flex: 1;
    min-width: 120px;
  }
}
</style>

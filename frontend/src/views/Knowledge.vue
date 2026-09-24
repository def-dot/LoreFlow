<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  listAllDocuments,
  deleteDocument,
  listKnowledgeBases,
  createKnowledgeBase,
  uploadDocument,
  type KnowledgeBase,
  type DocumentItem,
} from '@/api/knowledge'

const documents = ref<DocumentItem[]>([])
const kbs = ref<KnowledgeBase[]>([])
const uploading = ref(false)
const fileInput = ref<HTMLInputElement | null>(null)
const showUpload = ref(false)

// 上传弹窗状态
const selectedFiles = ref<File[]>([])
const kbInput = ref('')
const matchedKbId = ref<number | null>(null)

onMounted(loadData)

async function loadData() {
  const [docs, kbList] = await Promise.all([listAllDocuments(), listKnowledgeBases()])
  documents.value = docs
  kbs.value = kbList
}

function openUpload() {
  selectedFiles.value = []
  kbInput.value = ''
  matchedKbId.value = null
  showUpload.value = true
}

function fetchKbSuggestions(query: string, cb: (results: { value: string; id: number }[]) => void) {
  const q = query.trim().toLowerCase()
  const matches = kbs.value
    .filter((kb) => kb.name.toLowerCase().includes(q))
    .map((kb) => ({ value: kb.name, id: kb.id }))
  cb(matches)
}

function onKbSelect(item: { value: string; id: number }) {
  matchedKbId.value = item.id
}

function onKbInput() {
  matchedKbId.value = null
}

function onFileChange(e: Event) {
  const input = e.target as HTMLInputElement
  selectedFiles.value = input.files ? Array.from(input.files) : []
}

async function handleUpload() {
  if (!selectedFiles.value.length) {
    ElMessage.warning('请选择文件')
    return
  }

  let kbId = matchedKbId.value
  if (!kbId) {
    const name = kbInput.value.trim()
    if (!name) {
      ElMessage.warning('请选择或输入知识库名称')
      return
    }
    const existing = kbs.value.find((k) => k.name === name)
    if (existing) {
      kbId = existing.id
    } else {
      const created = await createKnowledgeBase({ name })
      kbId = created.id
    }
  }

  uploading.value = true
  try {
    for (const file of selectedFiles.value) {
      await uploadDocument(kbId, file)
      ElMessage.success(`${file.name} 已入库`)
    }
    showUpload.value = false
    await loadData()
  } catch (err: any) {
    ElMessage.error(err.message || '上传失败')
  } finally {
    uploading.value = false
  }
}

async function handleDelete(doc: DocumentItem) {
  try {
    await ElMessageBox.confirm(`确定删除文档「${doc.filename}」？`, '确认', {
      confirmButtonText: '删除',
      cancelButtonText: '取消',
      type: 'warning',
    })
    await deleteDocument(doc.id)
    ElMessage.success('已删除')
    documents.value = documents.value.filter((d) => d.id !== doc.id)
  } catch {
    // 取消
  }
}

function statusLabel(s: string) {
  return s === 'ready' ? '就绪' : s === 'error' ? '失败' : '处理中'
}

function statusType(s: string) {
  return s === 'ready' ? 'success' : s === 'error' ? 'danger' : 'warning'
}
</script>

<template>
  <div class="knowledge-page">
    <div class="page-header">
      <h2>知识库</h2>
      <el-button type="primary" size="small" @click="openUpload">上传文档</el-button>
    </div>

    <div v-if="!documents.length" class="empty-hint">暂无文档，点击「上传文档」开始</div>

    <div class="doc-list">
      <div v-for="doc in documents" :key="doc.id" class="doc-item">
        <div class="doc-info">
          <span class="doc-name">{{ doc.filename }}</span>
          <span class="doc-kb">{{ doc.kb_name }}</span>
          <el-tag :type="statusType(doc.status)" size="small">{{ statusLabel(doc.status) }}</el-tag>
          <span v-if="doc.status === 'ready'" class="doc-meta">{{ doc.chunk_count }} 个切块</span>
          <span v-if="doc.status === 'error' && doc.error" class="doc-error">{{ doc.error }}</span>
        </div>
        <button class="delete-btn" @click="handleDelete(doc)" title="删除">×</button>
      </div>
    </div>

    <!-- 上传弹窗 -->
    <el-dialog v-model="showUpload" title="上传文档" width="420px">
      <el-form label-position="top">
        <el-form-item label="知识库">
          <el-autocomplete
            v-model="kbInput"
            :fetch-suggestions="fetchKbSuggestions"
            placeholder="选择已有知识库或输入新名称"
            style="width: 100%"
            @select="onKbSelect"
            @input="onKbInput"
          />
          <div v-if="kbInput && !matchedKbId" class="new-kb-hint">
            将新建知识库「{{ kbInput.trim() }}」
          </div>
        </el-form-item>
        <el-form-item label="文件">
          <input
            ref="fileInput"
            type="file"
            multiple
            accept=".txt,.md,.pdf"
            @change="onFileChange"
          />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showUpload = false">取消</el-button>
        <el-button type="primary" :loading="uploading" @click="handleUpload">上传</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.knowledge-page {
  max-width: 960px;
  margin: 0 auto;
  padding: 24px 20px;
}

.page-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 20px;
}

.doc-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.doc-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 10px 14px;
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: 6px;
}

.doc-info {
  display: flex;
  align-items: center;
  gap: 10px;
  flex: 1;
  min-width: 0;
}

.doc-name {
  font-size: 13px;
  color: var(--ink);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.doc-kb {
  font-size: 11px;
  color: var(--ink-3);
  background: rgba(77, 196, 178, 0.08);
  padding: 1px 6px;
  border-radius: 3px;
  flex-shrink: 0;
}

.doc-meta {
  font-size: 11px;
  color: var(--ink-3);
}

.doc-error {
  font-size: 11px;
  color: var(--danger, #e74c3c);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.delete-btn {
  background: none;
  border: none;
  color: var(--ink-3);
  cursor: pointer;
  font-size: 14px;
  padding: 0 4px;
  opacity: 0;
  transition: opacity 0.15s;
}
.doc-item:hover .delete-btn {
  opacity: 1;
}
.delete-btn:hover {
  color: var(--danger, #e74c3c);
}

.empty-hint {
  text-align: center;
  padding: 40px 16px;
  font-size: 13px;
  color: var(--ink-3);
}

.new-kb-hint {
  margin-top: 8px;
}
</style>

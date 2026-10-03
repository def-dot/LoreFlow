<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ArrowLeft, ArrowRight } from '@element-plus/icons-vue'
import MarkdownIt from 'markdown-it'
import { getDocumentPage, type PageData } from '@/api/knowledge'
import { api } from '@/api/request'

const md = new MarkdownIt({ html: false, breaks: true, linkify: true })

const route = useRoute()
const router = useRouter()

const docId = computed(() => Number(route.params.id))
const docName = computed(() => (route.query.name as string) || '未知文档')

const totalPages = ref(0)
const currentPage = ref(1)
const pageData = ref<PageData | null>(null)
const loading = ref(false)
const imageLoading = ref(false)

const imageError = ref(false)
const rawText = ref('')
const imageBlobUrl = ref('')

async function fetchPageImage() {
  imageError.value = false
  rawText.value = ''
  imageLoading.value = true
  if (imageBlobUrl.value) URL.revokeObjectURL(imageBlobUrl.value)
  imageBlobUrl.value = ''
  try {
    const res = await api.get(
      `/documents/${docId.value}/pages/${currentPage.value}/image`,
      { responseType: 'blob' },
    )
    const blob = (res as any).data as Blob
    if (blob.type === 'text/plain') {
      rawText.value = await blob.text()
    } else {
      imageBlobUrl.value = URL.createObjectURL(blob)
    }
  } catch {
    imageError.value = true
  } finally {
    imageLoading.value = false
  }
}

async function loadPage(p: number) {
  if (totalPages.value && (p < 1 || p > totalPages.value)) return
  currentPage.value = p
  loading.value = true
  try {
    const res = await getDocumentPage(docId.value, p)
    pageData.value = res
    if (res.total) totalPages.value = res.total
  } catch {
    pageData.value = null
  } finally {
    loading.value = false
  }
  if (pageData.value) fetchPageImage()
}

function prevPage() {
  if (currentPage.value > 1) loadPage(currentPage.value - 1)
}

function nextPage() {
  if (currentPage.value < totalPages.value) loadPage(currentPage.value + 1)
}

function onPageInput(e: Event) {
  const v = Number((e.target as HTMLInputElement).value)
  if (v >= 1 && v <= totalPages.value) loadPage(v)
}

function handleKeydown(e: KeyboardEvent) {
  if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement) return
  if (e.key === 'ArrowLeft') prevPage()
  else if (e.key === 'ArrowRight') nextPage()
}

function goBack() {
  router.push({ name: 'knowledge' })
}

onMounted(() => {
  document.addEventListener('keydown', handleKeydown)
  loadPage(1)
})

onUnmounted(() => {
  document.removeEventListener('keydown', handleKeydown)
  if (imageBlobUrl.value) URL.revokeObjectURL(imageBlobUrl.value)
})
</script>

<template>
  <div class="parse-page">
    <!-- 顶部 -->
    <div class="page-header">
      <el-button text :icon="ArrowLeft" @click="goBack">返回文档列表</el-button>
      <span class="divider">/</span>
      <span class="doc-name" :title="docName">{{ docName }}</span>
      <span class="count-chip" v-if="totalPages">{{ totalPages }} 页</span>
    </div>

    <!-- 页码导航 -->
    <div class="nav-bar" v-if="totalPages > 1">
      <button class="nav-btn" :disabled="currentPage <= 1" @click="prevPage">
        <el-icon :size="16"><ArrowLeft /></el-icon>
      </button>
      <input
        class="page-input"
        type="number"
        :min="1"
        :max="totalPages"
        :value="currentPage"
        @change="onPageInput"
      />
      <span class="page-total">/ {{ totalPages }}</span>
      <button class="nav-btn" :disabled="currentPage >= totalPages" @click="nextPage">
        <el-icon :size="16"><ArrowRight /></el-icon>
      </button>
    </div>

    <template v-if="totalPages === 0">
      <div class="empty-state">暂无解析数据</div>
    </template>

    <!-- 对照面板 -->
    <template v-else>
      <div class="compare-panels">
        <!-- 左侧：原始 PDF -->
        <div class="panel panel-original">
          <div class="panel-header">
            <span class="panel-label">{{ totalPages > 1 ? '原始 PDF' : '原始内容' }}</span>
            <span class="panel-page" v-if="totalPages > 1">第 {{ currentPage }} 页</span>
          </div>
          <div class="panel-body" :class="{ loading: imageLoading }">
            <div v-if="imageLoading" class="panel-loading">加载中...</div>
            <div v-else-if="imageError" class="panel-error">加载失败</div>
            <pre v-else-if="rawText" class="raw-text">{{ rawText }}</pre>
            <img
              v-else-if="imageBlobUrl"
              :src="imageBlobUrl"
              :alt="`第 ${currentPage} 页`"
              class="page-image"
            />
          </div>
        </div>

        <!-- 右侧：解析结果 -->
        <div class="panel panel-parsed">
          <div class="panel-header">
            <span class="panel-label">解析结果</span>
            <span class="panel-meta" v-if="pageData">
              <template v-if="pageData.table_count">{{ pageData.table_count }} 表格</template>
              <template v-if="pageData.table_count && pageData.picture_count"> · </template>
              <template v-if="pageData.picture_count">{{ pageData.picture_count }} 图片</template>
            </span>
          </div>
          <div class="panel-body" :class="{ loading: loading }">
            <div v-if="loading" class="panel-loading">解析中...</div>
            <div
              v-else-if="pageData"
              class="markdown-body"
              v-html="md.render(pageData.markdown)"
            />
            <div v-else class="panel-loading">加载失败</div>
          </div>
        </div>
      </div>
    </template>
  </div>
</template>

<style scoped>
.parse-page {
  padding-bottom: 40px;
}

/* ---- 页头 ---- */
.page-header {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 20px;
}

.page-header :deep(.el-button) {
  color: var(--ink-3);
  font-size: 13px;
  padding: 0;
}

.page-header :deep(.el-button:hover) {
  color: var(--accent, #4dc4b2);
}

.divider {
  color: var(--ink-5);
  font-size: 14px;
}

.doc-name {
  font-weight: 600;
  color: var(--ink);
  font-size: 15px;
  max-width: 500px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
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

/* ---- 页码导航 ---- */
.nav-bar {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  padding: 10px 0 14px;
}

.nav-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 32px;
  height: 32px;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--surface);
  color: var(--ink-2);
  cursor: pointer;
  transition: all 0.15s;
}

.nav-btn:hover:not(:disabled) {
  border-color: var(--brand);
  color: var(--brand);
}

.nav-btn:disabled {
  opacity: 0.3;
  cursor: default;
}

.page-input {
  width: 56px;
  height: 32px;
  text-align: center;
  border: 1px solid var(--border);
  border-radius: 8px;
  font-size: 14px;
  font-weight: 600;
  font-family: var(--font-sans);
  color: var(--ink);
  background: var(--surface);
  -moz-appearance: textfield;
}

.page-input::-webkit-inner-spin-button,
.page-input::-webkit-outer-spin-button {
  -webkit-appearance: none;
  margin: 0;
}

.page-total {
  font-size: 14px;
  color: var(--ink-4);
  font-variant-numeric: tabular-nums;
}

.compare-panels {
  display: flex;
  gap: 12px;
  height: calc(100vh - 180px);
  min-height: 500px;
}

.panel {
  flex: 1;
  display: flex;
  flex-direction: column;
  border: 1px solid var(--border);
  border-radius: 10px;
  overflow: hidden;
  background: var(--surface);
  min-width: 0;
}

.panel-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 10px 16px;
  background: var(--surface-2);
  border-bottom: 1px solid var(--border);
  flex-shrink: 0;
}

.panel-label {
  font-size: 13px;
  font-weight: 600;
  color: var(--ink);
}

.panel-page,
.panel-meta {
  font-size: 12px;
  color: var(--ink-4);
}

.panel-body {
  flex: 1;
  overflow: auto;
  position: relative;
}

.panel-body.loading {
  display: flex;
  align-items: center;
  justify-content: center;
}

.panel-loading {
  color: var(--ink-4);
  font-size: 14px;
}

.panel-error {
  color: #f56c6c;
  font-size: 14px;
  text-align: center;
  padding: 40px 20px;
}

.raw-text {
  padding: 16px 20px;
  font-size: 13px;
  line-height: 1.7;
  color: var(--ink-2);
  white-space: pre-wrap;
  word-break: break-all;
  margin: 0;
  font-family: var(--font-mono, monospace);
}

.page-image {
  width: 100%;
  display: block;
}

.markdown-body {
  padding: 20px 24px;
  font-size: 14px;
  line-height: 1.8;
  color: var(--ink-2);
}

.markdown-body :deep(h1),
.markdown-body :deep(h2),
.markdown-body :deep(h3) {
  color: var(--ink);
  margin-top: 1.2em;
  margin-bottom: 0.4em;
}

.markdown-body :deep(table) {
  width: 100%;
  border-collapse: collapse;
  margin: 12px 0;
  font-size: 13px;
}

.markdown-body :deep(th),
.markdown-body :deep(td) {
  border: 1px solid var(--border);
  padding: 6px 10px;
  text-align: left;
}

.markdown-body :deep(th) {
  background: var(--surface-2);
  font-weight: 600;
}

.markdown-body :deep(p) {
  margin: 0.5em 0;
}

.markdown-body :deep(img) {
  max-width: 100%;
}

.empty-state {
  text-align: center;
  color: var(--ink-3);
  padding: 80px 0;
  font-size: 14px;
}
</style>
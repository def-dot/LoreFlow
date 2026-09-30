<script setup lang="ts">
import { ref, watch, onMounted, onUnmounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ArrowLeft, Search } from '@element-plus/icons-vue'
import { getDocumentChunks, type ChunkItem } from '@/api/knowledge'

const route = useRoute()
const router = useRouter()

const docId = ref(Number(route.params.id))
const docName = ref(String(route.query.name || ''))
const chunks = ref<ChunkItem[]>([])
const loading = ref(false)
const searchQuery = ref('')
const page = ref(1)
const pageSize = ref(30)
const total = ref(0)
let searchDebounce: ReturnType<typeof setTimeout> | null = null

async function fetchChunks() {
  loading.value = true
  try {
    const res = await getDocumentChunks(docId.value, {
      page: page.value,
      page_size: pageSize.value,
      q: searchQuery.value || undefined,
    })
    chunks.value = res.items
    total.value = res.total
  } finally {
    loading.value = false
  }
}

function onSearchChange() {
  if (searchDebounce) clearTimeout(searchDebounce)
  searchDebounce = setTimeout(() => {
    page.value = 1
    fetchChunks()
  }, 300)
}

function formatPages(chunk: ChunkItem): string {
  if (!chunk.page_numbers || chunk.page_numbers.length === 0) return ''
  return chunk.page_numbers.map(p => `P${p}`).join(', ')
}

function highlightText(text: string, query: string): string {
  if (!query) return text
  const escaped = query.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
  const re = new RegExp(`(${escaped})`, 'gi')
  return text.replace(re, '<span class="hl">$1</span>')
}

function goBack() {
  router.push('/knowledge')
}

function handleSizeChange(val: number) {
  pageSize.value = val
  page.value = 1
  fetchChunks()
}

function handlePageChange(val: number) {
  page.value = val
  fetchChunks()
}

onMounted(fetchChunks)

onUnmounted(() => {
  if (searchDebounce) clearTimeout(searchDebounce)
})
</script>

<template>
  <div class="chunks-page">
    <!-- 页头 -->
    <div class="page-header">
      <el-button text :icon="ArrowLeft" @click="goBack">返回文档列表</el-button>
      <span class="divider">/</span>
      <span class="doc-name" :title="docName">{{ docName }}</span>
      <span class="count-chip" v-if="total">{{ total }}</span>
    </div>

    <!-- 切片列表 -->
    <el-card v-loading="loading" shadow="never">
      <template #header>
        <div class="list-header">
          <span class="list-title">切片详情</span>
          <div class="list-controls">
            <el-input
              v-model="searchQuery"
              placeholder="搜索切片内容…"
              :prefix-icon="Search"
              clearable
              size="small"
              class="search-input"
              @input="onSearchChange"
              @clear="onSearchChange"
            />
            <el-select v-model="pageSize" size="small" class="page-size-select" @change="handleSizeChange">
              <el-option :value="15" label="15 条/页" />
              <el-option :value="30" label="30 条/页" />
              <el-option :value="50" label="50 条/页" />
            </el-select>
          </div>
        </div>
      </template>

      <div v-if="!chunks.length && !loading" class="empty-text">
        {{ searchQuery ? '未找到匹配内容' : '暂无切片' }}
      </div>

      <div class="chunk-list">
        <div v-for="chunk in chunks" :key="chunk.id" class="chunk-card">
          <div class="chunk-header">
            <span class="chunk-number">
              {{ (page - 1) * pageSize + chunks.indexOf(chunk) + 1 }}
            </span>
            <div class="chunk-meta">
              <span v-if="chunk.heading_context" class="heading-text">{{ chunk.heading_context }}</span>
              <span v-if="formatPages(chunk)" class="page-badge">
                {{ formatPages(chunk) }}
              </span>
            </div>
          </div>
          <p
            class="chunk-text"
            v-html="highlightText(chunk.raw_content, searchQuery)"
          />
        </div>
      </div>

      <div v-if="total > pageSize" class="list-footer">
        <el-pagination
          v-model:current-page="page"
          :page-size="pageSize"
          :total="total"
          layout="prev, pager, next"
          @current-change="handlePageChange"
        />
      </div>
    </el-card>
  </div>
</template>

<style scoped>
.chunks-page {
  max-width: 1180px;
  margin: 0 auto;
}

/* ---- 页头 ---- */
.page-header {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 20px;
}

.page-header .el-button {
  color: var(--ink-3);
  font-size: 13px;
  padding: 0;
}

.page-header .el-button:hover {
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

/* ---- 列表头 ---- */
.list-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 10px;
}

.list-title {
  font-weight: 600;
  color: var(--ink);
  font-size: 15px;
}

.list-controls {
  display: flex;
  align-items: center;
  gap: 10px;
}

.search-input {
  width: 240px;
}

.page-size-select {
  width: 120px;
}

/* ---- 空态 ---- */
.empty-text {
  text-align: center;
  color: var(--ink-4);
  padding: 48px 0;
  font-size: 13px;
}

/* ---- 切片卡片 ---- */
.chunk-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.chunk-card {
  padding: 18px 22px;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: var(--panel, #fff);
  transition: border-color 0.2s ease;
}

.chunk-card:hover {
  border-color: var(--accent, #4dc4b2);
}

.chunk-header {
  display: flex;
  align-items: flex-start;
  gap: 14px;
  margin-bottom: 12px;
}

.chunk-number {
  flex-shrink: 0;
  width: 32px;
  height: 32px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  font-size: 13px;
  font-weight: 600;
  border-radius: 50%;
  color: var(--accent, #4dc4b2);
  background: rgba(77, 196, 178, 0.08);
}

.chunk-meta {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  padding-top: 5px;
}

.heading-text {
  color: var(--ink-2);
  font-size: 13px;
  font-weight: 500;
}

.page-badge {
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 0.04em;
  color: var(--accent, #4dc4b2);
  background: rgba(77, 196, 178, 0.1);
  padding: 2px 8px;
  border-radius: 4px;
}

.chunk-text {
  margin: 0;
  line-height: 1.75;
  color: var(--ink-2);
  font-size: 13.5px;
  white-space: pre-wrap;
  word-break: break-word;
}

.chunk-text :deep(.hl) {
  background: rgba(255, 230, 100, 0.35);
  border-radius: 2px;
  padding: 1px 0;
}

/* ---- 分页 ---- */
.list-footer {
  display: flex;
  justify-content: center;
  padding-top: 24px;
}
</style>
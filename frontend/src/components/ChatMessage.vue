<script setup lang="ts">
import { ref, computed, watch, onMounted, onUnmounted } from 'vue'
import { marked } from 'marked'
import { UserFilled, Cpu } from '@element-plus/icons-vue'
import type { ChatMessage } from '@/stores/agents'

interface ChunkSource {
  chunk_id: number
  content: string
  file_path: string
  similarity: number
}

// 流式场景下同步解析，避免闪烁
marked.use({ async: false })

const props = defineProps<{
  message: ChatMessage
  toolLabels?: Record<string, string>
}>()

const showProcess = ref(false)
/** 用户是否手动切换过（阻止自动展开覆盖） */
let userToggled = false

/** 是否存在推理过程（思考或工具步骤） */
const hasProcess = computed(() =>
  props.message.rounds?.some(r => r.thinking || r.steps?.length)
)

/** 流式进行时自动展开，完成后自动折叠 */
watch(() => props.message.streaming, (streaming) => {
  if (streaming && hasProcess.value) {
    showProcess.value = true
    userToggled = false
  } else if (!streaming) {
    showProcess.value = false
    userToggled = false
  }
})

/** 推理数据到达时自动展开（流式中） */
watch(hasProcess, (has) => {
  if (has && props.message.streaming && !userToggled) {
    showProcess.value = true
  }
})

function toggleProcess() {
  userToggled = true
  showProcess.value = !showProcess.value
}

// ── Chunk 引用 ─────────────────────────────────────────────────

const msgTextRef = ref<HTMLElement | null>(null)
const activeChunk = ref<ChunkSource | null>(null)
const popoverPos = ref<Record<string, string>>({})
let popoverAnchor: HTMLElement | null = null

/** 从工具输出中解析检索结果 sources（多次调用合并去重） */
const chunkSources = computed<ChunkSource[]>(() => {
  const rounds = props.message.rounds
  if (!rounds) return []
  const merged: ChunkSource[] = []
  const seen = new Set<number>()
  for (const round of rounds) {
    if (!round.steps) continue
    for (const step of round.steps) {
      if (step.tool_name !== 'retrieve_knowledge' || !step.output) continue
      try {
        const data = JSON.parse(step.output)
        if (Array.isArray(data.sources)) {
          for (const s of data.sources) {
            if (!seen.has(s.chunk_id)) {
              seen.add(s.chunk_id)
              merged.push(s)
            }
          }
        }
      } catch { /* ignore */ }
    }
  }
  return merged
})

/** 点击 [来源#id] 引用 → 弹出/关闭 popover */
function onChunkRefClick(e: MouseEvent) {
  const el = e.target as HTMLElement
  if (!el.classList.contains('chunk-ref')) return
  e.preventDefault()
  const cid = Number(el.dataset.cid)
  const src = chunkSources.value.find(s => s.chunk_id === cid)
  if (!src) return

  // 再次点击同一引用 → 关闭
  if (popoverAnchor === el && activeChunk.value) {
    activeChunk.value = null
    popoverAnchor = null
    return
  }

  activeChunk.value = src
  popoverAnchor = el

  // 定位 popover（Teleport 到 body，用视口坐标）
  const cr = el.getBoundingClientRect()
  popoverPos.value = {
    position: 'fixed',
    left: `${cr.left + cr.width / 2}px`,
    top: `${cr.bottom + 4}px`,
    transform: 'translateX(-50%)',
    zIndex: '9999',
  }
}

/** 点击外部关闭 popover */
function onDocClick(e: MouseEvent) {
  const t = e.target as HTMLElement
  if (t.closest('.chunk-popover') || t.classList.contains('chunk-ref')) return
  activeChunk.value = null
  popoverAnchor = null
}

onMounted(() => document.addEventListener('click', onDocClick, true))
onUnmounted(() => document.removeEventListener('click', onDocClick, true))

/** 点击末尾来源项 → 切换弹窗 */
function toggleSource(src: ChunkSource, e: MouseEvent) {
  if (activeChunk.value?.chunk_id === src.chunk_id) {
    activeChunk.value = null
    popoverAnchor = null
    return
  }
  activeChunk.value = src
  const el = e.currentTarget as HTMLElement
  popoverAnchor = el
  const cr = el.getBoundingClientRect()
  popoverPos.value = {
    position: 'fixed',
    left: `${cr.left + cr.width / 2}px`,
    top: `${cr.top - 4}px`,
    transform: 'translate(-50%, -100%)',
    zIndex: '9999',
  }
}

/** 格式化工具参数为可读文本 */
function formatArgs(args: string): string {
  try {
    const obj = JSON.parse(args)
    return Object.entries(obj)
      .map(([k, v]) => `${k}: ${typeof v === 'string' ? v : JSON.stringify(v)}`)
      .join(', ')
  } catch {
    return args
  }
}



/** 格式化耗时 */
function formatDuration(ms?: number): string {
  if (ms == null) return ''
  if (ms < 1000) return `${ms}ms`
  return `${(ms / 1000).toFixed(1)}s`
}

/** 格式化总耗时 */
/** markdown → HTML */
function formatContent(text: string, withChunkRef = false): string {
  let html = marked.parse(text) as string
  // uploads/ 路径转为可点击链接（支持 /uploads/ 和 uploads/）
  html = html.replace(
    /(?<!["'=/])(\/?uploads\/[\w./-]+\.\w+)/g,
    (m) => {
      const path = m.startsWith('/') ? m : '/' + m
      return `<a href="${path}" target="_blank">${m}</a>`
    },
  )
  // [#123] → 可点击 chunk 引用（仅最终回复）
  if (withChunkRef && chunkSources.value.length) {
    html = html.replace(
      /\[#(\d+)\]/g,
      '<a class="chunk-ref" data-cid="$1" href="javascript:void(0)">[#$1]</a>',
    )
  }
  return html
}

/** 去掉 [附件] 前缀 */
function stripFilePrefix(text: string): string {
  return text.replace(/^\[附件\]\n(?:- .+\n)*\n?/, '')
}
</script>

<template>
  <div class="chat-msg" :class="[`role-${message.role}`, { streaming: message.streaming }]">
    <!-- 用户消息 -->
    <template v-if="message.role === 'user'">
      <div class="msg-avatar user-avatar"><UserFilled /></div>
      <div class="msg-body">
        <div v-if="message.files?.length" class="msg-files">
          <a
            v-for="f in message.files"
            :key="f.store_path"
            :href="'/' + f.store_path"
            target="_blank"
            class="file-chip"
          >
            📄 {{ f.filename }}
          </a>
        </div>
        <div class="bubble user-bubble">
          <pre class="msg-text">{{ stripFilePrefix(message.content) }}</pre>
        </div>
      </div>
    </template>

    <!-- Assistant 消息（含完整执行过程） -->
    <template v-else-if="message.role === 'assistant'">
      <div class="msg-avatar ai-avatar"><Cpu /></div>
      <div class="bubble assistant-bubble">
      <!-- 推理过程（思考 + 工具步骤，统一折叠） -->
      <div v-if="hasProcess || (message.streaming && message.phase === 'thinking')" class="process-section">
        <button class="toggle-btn" @click="toggleProcess">
          <template v-if="message.streaming">
            <span class="stream-dot" />
            💭 思考中…
          </template>
          <template v-else>
            <span class="toggle-icon">{{ showProcess ? '▾' : '▸' }}</span>
            🧠 思考过程
          </template>
        </button>
        <div v-if="showProcess" class="process-content">
          <template v-for="(round, ri) in message.rounds" :key="ri">
            <div v-if="round.thinking || round.steps?.length" class="round-block">
              <div v-if="round.thinking">
                <div v-if="(message.rounds?.filter(r => r.thinking).length ?? 0) > 1" class="round-label">
                  第 {{ ri + 1 }} 轮
                </div>
                <div class="thinking-content" v-html="formatContent(round.thinking)" />
                <div v-if="round.content" class="round-content" v-html="formatContent(round.content)" />
              </div>
              <div v-if="round.steps?.length" class="steps-timeline">
                <div
                  v-for="(step, si) in round.steps"
                  :key="si"
                  class="step-item"
                  :class="`step-${step.status}`"
                >
                  <div class="step-header">
                    <span v-if="step.status === 'running'" class="step-spinner" />
                    <span v-else class="step-icon">{{ step.status === 'error' ? '❌' : '✅' }}</span>
                    <span class="step-name">{{ toolLabels?.[step.tool_name] || step.tool_name }}</span>
                    <span class="step-duration" v-if="step.duration_ms != null">耗时：{{ formatDuration(step.duration_ms) }}</span>
                  </div>
                  <div v-if="step.arguments || step.output" class="step-detail">
                    <template v-if="step.arguments">
                      <div class="detail-label">输入</div>
                      <pre class="detail-input">{{ formatArgs(step.arguments) }}</pre>
                    </template>
                    <template v-if="step.output">
                      <div class="detail-label">输出</div>
                      <pre class="detail-output" :class="{ 'is-error': step.status === 'error' }">{{ step.output }}</pre>
                    </template>
                  </div>
                </div>
              </div>
            </div>
          </template>
        </div>
      </div>

      <!-- 最终回复文本（始终可见） -->
      <div
        v-if="message.content"
        ref="msgTextRef"
        class="msg-text"
        v-html="formatContent(message.content, true)"
        @click="onChunkRefClick"
      />

      <!-- 来源列表（末尾附带） -->
      <div v-if="chunkSources.length && !message.streaming" class="sources-section">
        <div class="sources-title">📎 参考来源</div>
        <div
          v-for="src in chunkSources"
          :key="src.chunk_id"
          class="source-item"
          :class="{ active: activeChunk?.chunk_id === src.chunk_id }"
          @click="toggleSource(src, $event)"
        >
          <span class="source-num">[#{{ src.chunk_id }}]</span>
          <span class="source-file">{{ src.file_path }}</span>
          <span class="source-score">{{ (src.similarity * 100).toFixed(1) }}%</span>
          <div class="source-preview">{{ src.content }}</div>
        </div>
      </div>

      <!-- chunk 引用弹窗（正文内 [n] 点击用） -->
      <Teleport to="body">
        <Transition name="chunk-pop">
          <div
            v-if="activeChunk && chunkSources.length"
            class="chunk-popover"
            :style="popoverPos"
          >
            <div class="chunk-pop-header">
              <span class="chunk-pop-num">#{{ activeChunk.chunk_id }}</span>
              <span class="chunk-pop-file">{{ activeChunk.file_path }}</span>
              <span class="chunk-pop-score">{{ (activeChunk.similarity * 100).toFixed(1) }}%</span>
            </div>
            <div class="chunk-pop-content">{{ activeChunk.content }}</div>
          </div>
        </Transition>
      </Teleport>

      <!-- 流式光标 -->
      <span v-if="message.streaming && message.phase === 'token'" class="cursor">▊</span>

    </div>
    </template>
  </div>
</template>

<style scoped>
.chat-msg {
  display: flex;
  gap: 8px;
  margin-bottom: 12px;
}

.role-user {
  flex-direction: row-reverse;
}
.role-assistant {
  flex-direction: row;
}

.msg-avatar {
  width: 32px;
  height: 32px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}

.msg-avatar svg {
  width: 16px;
  height: 16px;
}

.user-avatar {
  background: rgba(77, 196, 178, 0.18);
  border: 1px solid rgba(77, 196, 178, 0.3);
  color: #3ba89a;
}

.ai-avatar {
  background: rgba(99, 102, 241, 0.15);
  border: 1px solid rgba(99, 102, 241, 0.25);
  color: #5558e6;
}

.bubble {
  padding: 12px 16px;
  border-radius: 12px;
  font-size: 13.5px;
  line-height: 1.65;
  word-break: break-word;
}

.user-bubble {
  background: rgba(77, 196, 178, 0.15);
  border: 1px solid rgba(77, 196, 178, 0.25);
  color: var(--ink);
}

.assistant-bubble {
  background: var(--panel);
  border: 1px solid var(--line);
  color: var(--ink-2);
  max-width: 80%;
}

.msg-body {
  display: flex;
  flex-direction: column;
  gap: 6px;
  max-width: 80%;
}

.msg-files {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.file-chip {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 4px 10px;
  background: rgba(77, 196, 178, 0.1);
  border: 1px solid rgba(77, 196, 178, 0.2);
  border-radius: 6px;
  font-size: 12px;
  color: var(--ink-2);
  text-decoration: none;
  transition: background 0.15s;
}

.file-chip:hover {
  background: rgba(77, 196, 178, 0.2);
}

/* ---- 流式指示 ---- */
.stream-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--accent);
  box-shadow: 0 0 6px rgba(77, 196, 178, 0.5);
  animation: pulse 1.2s ease-in-out infinite;
}

@keyframes pulse {
  0%, 100% { opacity: 1; transform: scale(1); }
  50% { opacity: 0.4; transform: scale(0.8); }
}

/* ---- 消息文本 ---- */
.msg-text {
  margin: 0;
  font-family: inherit;
  line-height: 1.7;
  color: inherit;
}

.msg-text :deep(p) {
  margin: 0 0 8px;
}
.msg-text :deep(p:last-child) {
  margin-bottom: 0;
}

.msg-text :deep(h1),
.msg-text :deep(h2),
.msg-text :deep(h3),
.msg-text :deep(h4) {
  margin: 14px 0 6px;
  font-weight: 600;
  line-height: 1.35;
  color: var(--ink);
}
.msg-text :deep(h1) { font-size: 1.3em; }
.msg-text :deep(h2) { font-size: 1.15em; }
.msg-text :deep(h3) { font-size: 1.05em; }

.msg-text :deep(ul),
.msg-text :deep(ol) {
  margin: 4px 0 8px;
  padding-left: 20px;
}
.msg-text :deep(li) {
  margin: 2px 0;
}

.msg-text :deep(blockquote) {
  margin: 6px 0;
  padding: 4px 12px;
  border-left: 3px solid var(--accent);
  color: var(--ink-3);
  background: rgba(77, 196, 178, 0.06);
  border-radius: 0 4px 4px 0;
}

.msg-text :deep(hr) {
  border: none;
  border-top: 1px solid var(--line);
  margin: 12px 0;
}

.msg-text :deep(table) {
  border-collapse: collapse;
  margin: 6px 0;
  font-size: 12.5px;
  width: 100%;
}
.msg-text :deep(th),
.msg-text :deep(td) {
  border: 1px solid var(--line);
  padding: 5px 10px;
  text-align: left;
}
.msg-text :deep(th) {
  background: rgba(0, 0, 0, 0.06);
  font-weight: 600;
}

.msg-text :deep(pre) {
  background: rgba(0, 0, 0, 0.2);
  padding: 10px 12px;
  border-radius: 6px;
  font-family: var(--font-mono);
  font-size: 12px;
  overflow-x: auto;
  margin: 6px 0;
  white-space: pre;
}
.msg-text :deep(pre code) {
  background: none;
  padding: 0;
  border-radius: 0;
  font-size: inherit;
}

.msg-text :deep(code) {
  background: rgba(0, 0, 0, 0.15);
  padding: 1px 5px;
  border-radius: 4px;
  font-family: var(--font-mono);
  font-size: 12.5px;
}

.msg-text :deep(a) {
  color: var(--accent);
  text-decoration: underline;
  text-underline-offset: 2px;
}
.msg-text :deep(a:hover) {
  opacity: 0.85;
}

/* ---- chunk 引用标签 ---- */
.msg-text :deep(.chunk-ref) {
  display: inline;
  color: var(--accent);
  cursor: pointer;
  font-size: 0.8em;
  vertical-align: super;
  background: rgba(77, 196, 178, 0.1);
  padding: 1px 4px;
  border-radius: 3px;
  text-decoration: none;
  font-weight: 600;
  transition: background 0.15s;
  line-height: 1;
}
.msg-text :deep(.chunk-ref:hover) {
  background: rgba(77, 196, 178, 0.25);
}

.msg-text :deep(img) {
  max-width: 100%;
  border-radius: 6px;
  margin: 4px 0;
}

/* ---- 推理过程 ---- */
.process-section {
  margin-bottom: 8px;
}

.round-block {
  margin-top: 8px;
}

.round-label {
  font-size: 11px;
  color: var(--text-secondary, #888);
  padding: 4px 0 2px;
  border-top: 1px dashed var(--border, #ddd);
  margin-bottom: 6px;
}

.round-content {
  margin-top: 6px;
  padding: 6px 12px;
  border-left: 2px solid rgba(100, 100, 100, 0.25);
  font-size: 12px;
  color: var(--ink-2, #666);
  line-height: 1.5;
  opacity: 0.85;
}

.thinking-content {
  margin-top: 6px;
  padding: 8px 12px;
  border-left: 3px solid rgba(135, 144, 176, 0.4);
  background: rgba(135, 144, 176, 0.06);
  border-radius: 0 6px 6px 0;
  font-size: 12.5px;
  color: var(--ink-3);
  line-height: 1.6;
  max-height: 300px;
  overflow-y: auto;
}

.thinking-content :deep(p) {
  margin: 0 0 6px;
}
.thinking-content :deep(p:last-child) {
  margin-bottom: 0;
}
.thinking-content :deep(pre) {
  background: rgba(0, 0, 0, 0.15);
  padding: 6px 8px;
  border-radius: 4px;
  font-size: 11.5px;
  overflow-x: auto;
  margin: 4px 0;
}
.thinking-content :deep(code) {
  background: rgba(0, 0, 0, 0.1);
  padding: 1px 4px;
  border-radius: 3px;
  font-family: var(--font-mono);
  font-size: 11.5px;
}

/* ---- 工具步骤时间线 ---- */
.steps-timeline {
  margin-top: 8px;
  padding-left: 4px;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.step-item {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 6px 10px;
  border-radius: 6px;
  font-size: 12px;
  background: rgba(0, 0, 0, 0.04);
  border-left: 3px solid var(--line);
}

.step-running {
  border-left-color: var(--accent);
}

.step-success {
  border-left-color: rgba(77, 196, 178, 0.6);
}

.step-error {
  border-left-color: var(--danger, #e74c3c);
}

.step-header {
  display: flex;
  align-items: baseline;
  gap: 6px;
  flex-wrap: wrap;
}

.step-icon {
  font-size: 11px;
  flex-shrink: 0;
}

.step-spinner {
  display: inline-block;
  width: 12px;
  height: 12px;
  border: 2px solid var(--ink-3, #999);
  border-top-color: transparent;
  border-radius: 50%;
  animation: spin 0.6s linear infinite;
  flex-shrink: 0;
}

@keyframes spin {
  to { transform: rotate(360deg); }
}

.step-name {
  font-family: var(--font-mono);
  font-weight: 500;
  color: var(--accent);
  font-size: 12px;
}

.step-args {
  color: var(--ink-3);
  font-family: var(--font-mono);
  font-size: 11px;
  word-break: break-all;
}

.step-duration {
  color: var(--ink-3);
  font-size: 11px;
  margin-left: auto;
  flex-shrink: 0;
}

.step-detail {
  margin-top: 4px;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.step-detail pre {
  margin: 0;
  padding: 8px 10px;
  border-radius: 5px;
  font-family: var(--font-mono);
  font-size: 11px;
  line-height: 1.55;
  white-space: pre-wrap;
  max-height: 200px;
  overflow-y: auto;
}

.detail-label {
  font-size: 11px;
  font-weight: 500;
  color: var(--ink-3, #999);
  margin-bottom: -2px;
}

.detail-input {
  background: rgba(0, 0, 0, 0.06);
  border: 1px solid rgba(0, 0, 0, 0.08);
  color: var(--ink-2);
}

.detail-output {
  background: rgba(0, 0, 0, 0.12);
  color: var(--ink-2);
}

.detail-output.is-error {
  background: rgba(231, 76, 60, 0.08);
  color: var(--danger, #e74c3c);
}

/* ---- 折叠按钮 ---- */
.toggle-btn {
  background: none;
  border: none;
  color: var(--ink-3);
  font-size: 12px;
  cursor: pointer;
  padding: 2px 0;
  display: flex;
  align-items: center;
  gap: 4px;
}
.toggle-btn:hover {
  color: var(--ink-2);
}

.toggle-icon {
  font-size: 10px;
}

/* ---- 执行摘要 ---- */
/* ---- 流式光标 ---- */
.cursor {
  display: inline-block;
  animation: blink 1s step-end infinite;
  color: var(--accent);
  font-size: 14px;
  margin-left: 2px;
}

@keyframes blink {
  50% { opacity: 0; }
}

/* ---- 末尾来源列表 ---- */
.sources-section {
  margin-top: 12px;
  padding-top: 10px;
  border-top: 1px solid var(--line);
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.sources-title {
  font-size: 12px;
  font-weight: 600;
  color: var(--ink-3);
  margin-bottom: 2px;
}

.source-item {
  padding: 8px 12px;
  border: 1px solid var(--line);
  border-radius: 8px;
  cursor: pointer;
  transition: border-color 0.15s, background 0.15s;
}

.source-item:hover {
  border-color: rgba(77, 196, 178, 0.4);
  background: rgba(77, 196, 178, 0.03);
}

.source-item.active {
  border-color: var(--accent);
  background: rgba(77, 196, 178, 0.06);
}

.source-num {
  font-size: 12px;
  font-weight: 600;
  color: var(--accent);
  margin-right: 6px;
}

.source-file {
  font-size: 12px;
  color: var(--ink-3);
  word-break: break-all;
}

.source-score {
  float: right;
  font-size: 11px;
  font-weight: 600;
  color: var(--accent);
  background: rgba(77, 196, 178, 0.08);
  padding: 1px 6px;
  border-radius: 4px;
}

.source-preview {
  margin-top: 6px;
  font-size: 12.5px;
  line-height: 1.6;
  color: var(--ink-3);
  white-space: pre-wrap;
  word-break: break-word;
  max-height: 72px;
  overflow: hidden;
  position: relative;
}

.source-preview::after {
  content: '';
  position: absolute;
  bottom: 0;
  left: 0;
  right: 0;
  height: 24px;
  background: linear-gradient(transparent, var(--panel));
  pointer-events: none;
}

/* ---- chunk 引用弹窗 ---- */
.chunk-popover {
  max-width: min(420px, 90vw);
  min-width: 280px;
  background: var(--panel);
  border: 1px solid var(--line-strong);
  border-radius: 10px;
  box-shadow: 0 8px 32px rgba(0, 0, 0, 0.4);
  padding: 14px 16px;
  pointer-events: auto;
}

.chunk-pop-header {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 10px;
  flex-wrap: wrap;
}

.chunk-pop-num {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 28px;
  height: 22px;
  padding: 0 6px;
  border-radius: 11px;
  font-size: 12px;
  font-weight: 600;
  color: var(--accent);
  background: rgba(77, 196, 178, 0.1);
}

.chunk-pop-file {
  flex: 1;
  font-size: 12px;
  color: var(--ink-3);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  min-width: 0;
}

.chunk-pop-score {
  flex-shrink: 0;
  font-size: 11px;
  font-weight: 600;
  color: var(--accent);
  background: rgba(77, 196, 178, 0.08);
  padding: 2px 7px;
  border-radius: 4px;
}

.chunk-pop-content {
  font-size: 13px;
  line-height: 1.7;
  color: var(--ink-2);
  white-space: pre-wrap;
  word-break: break-word;
  max-height: 260px;
  overflow-y: auto;
  border-top: 1px solid var(--line);
  padding-top: 10px;
}

/* 弹窗进出动画 */
.chunk-pop-enter-active,
.chunk-pop-leave-active {
  transition: opacity 0.15s ease, transform 0.15s ease;
}
.chunk-pop-enter-from,
.chunk-pop-leave-to {
  opacity: 0;
  transform: translateX(-50%) translateY(-4px);
}
</style>

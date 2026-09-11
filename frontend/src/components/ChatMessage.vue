<script setup lang="ts">
import { ref, computed } from 'vue'
import { marked } from 'marked'
import type { ChatMessage } from '@/stores/agents'

// 流式场景下同步解析，避免闪烁
marked.use({ async: false })

const props = defineProps<{
  message: ChatMessage
}>()

const showThinking = ref(false)
const showSteps = ref(false)

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
function formatTotalDuration(ms?: number): string {
  if (ms == null) return ''
  if (ms < 1000) return `${ms}ms`
  if (ms < 60000) return `${(ms / 1000).toFixed(1)}s`
  const m = Math.floor(ms / 60000)
  const s = Math.round((ms % 60000) / 1000)
  return `${m}m${s}s`
}

/** 流式阶段文案 */
function phaseLabel(phase?: string): string {
  switch (phase) {
    case 'thinking': return '💭 思考中…'
    case 'tool': return '🔧 执行工具…'
    case 'token': return '✏️ 生成回复…'
    default: return '⏳ 处理中…'
  }
}

/** markdown → HTML */
function formatContent(text: string): string {
  return marked.parse(text) as string
}
</script>

<template>
  <div class="chat-msg" :class="[`role-${message.role}`, { streaming: message.streaming }]">
    <!-- 用户消息 -->
    <div v-if="message.role === 'user'" class="bubble user-bubble">
      <pre class="msg-text">{{ message.content }}</pre>
    </div>

    <!-- Assistant 消息（含完整执行过程） -->
    <div v-else-if="message.role === 'assistant'" class="bubble assistant-bubble">
      <!-- 流式状态栏 -->
      <div v-if="message.streaming" class="stream-status">
        <span class="stream-dot" />
        {{ phaseLabel(message.phase) }}
      </div>

      <!-- 思考内容（可折叠） -->
      <div v-if="message.thinking" class="thinking-section">
        <button class="toggle-btn" @click="showThinking = !showThinking">
          <span class="toggle-icon">{{ showThinking ? '▾' : '▸' }}</span>
          💭 深度思考
        </button>
        <div v-if="showThinking" class="thinking-content" v-html="formatContent(message.thinking)" />
      </div>

      <!-- 工具调用步骤（时间线） -->
      <div v-if="message.steps?.length" class="steps-section">
        <button class="toggle-btn" @click="showSteps = !showSteps">
          <span class="toggle-icon">{{ showSteps ? '▾' : '▸' }}</span>
          🔧 调用了 {{ message.steps.length }} 个工具
        </button>
        <div v-if="showSteps" class="steps-timeline">
          <div
            v-for="(step, i) in message.steps"
            :key="i"
            class="step-item"
            :class="`step-${step.status}`"
          >
            <div class="step-header">
              <span class="step-icon">{{ step.status === 'running' ? '⏳' : step.status === 'error' ? '❌' : '✅' }}</span>
              <span class="step-name">{{ step.tool_name }}</span>
              <span class="step-args">{{ formatArgs(step.arguments) }}</span>
              <span class="step-duration" v-if="step.duration_ms != null">{{ formatDuration(step.duration_ms) }}</span>
            </div>
            <div v-if="step.output && showSteps" class="step-output">
              <pre>{{ step.output }}</pre>
            </div>
          </div>
        </div>
      </div>

      <!-- 最终回复文本（始终可见） -->
      <div v-if="message.content" class="msg-text" v-html="formatContent(message.content)" />

      <!-- 流式光标 -->
      <span v-if="message.streaming && message.phase === 'token'" class="cursor">▊</span>

      <!-- 执行摘要 -->
      <div v-if="message.summary" class="execution-summary">
        {{ message.summary.total_rounds }} 轮思考 ·
        {{ message.summary.total_tool_calls }} 次工具调用 ·
        {{ formatTotalDuration(message.summary.total_duration_ms) }}
      </div>
    </div>
  </div>
</template>

<style scoped>
.chat-msg {
  display: flex;
  margin-bottom: 12px;
}

.role-user {
  justify-content: flex-end;
}
.role-assistant {
  justify-content: flex-start;
}

.bubble {
  max-width: 80%;
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
}

/* ---- 流式状态栏 ---- */
.stream-status {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: var(--ink-3);
  margin-bottom: 8px;
}

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

.msg-text :deep(img) {
  max-width: 100%;
  border-radius: 6px;
  margin: 4px 0;
}

/* ---- 思考区 ---- */
.thinking-section {
  margin-bottom: 8px;
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
.steps-section {
  margin-bottom: 8px;
}

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
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 300px;
}

.step-duration {
  color: var(--ink-3);
  font-size: 11px;
  margin-left: auto;
  flex-shrink: 0;
}

.step-output {
  margin-top: 2px;
}

.step-output pre {
  margin: 0;
  padding: 6px 8px;
  background: rgba(0, 0, 0, 0.15);
  border-radius: 4px;
  font-family: var(--font-mono);
  font-size: 11px;
  white-space: pre-wrap;
  max-height: 150px;
  overflow-y: auto;
  color: var(--ink-3);
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
.execution-summary {
  margin-top: 10px;
  padding-top: 8px;
  border-top: 1px solid var(--line);
  font-size: 11px;
  color: var(--ink-3);
}

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
</style>

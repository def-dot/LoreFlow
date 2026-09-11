<script setup lang="ts">
import { ref, computed } from 'vue'
import { marked } from 'marked'
import type { ChatMessage } from '@/stores/agents'

// 流式场景下同步解析，避免闪烁
marked.use({ async: false })

const props = defineProps<{
  message: ChatMessage
}>()

const showToolCalls = ref(false)
const showToolOutput = ref(false)
const showThinking = ref(false)
</script>

<template>
  <div class="chat-msg" :class="[`role-${message.role}`, { streaming: message.streaming, thinking: message.streaming }]">
    <!-- 用户消息 -->
    <div v-if="message.role === 'user'" class="bubble user-bubble">
      <pre class="msg-text">{{ message.content }}</pre>
    </div>

    <!-- Assistant 消息 -->
    <div v-else-if="message.role === 'assistant'" class="bubble assistant-bubble">
      <!-- 工具调用折叠区 -->
      <div v-if="message.tool_calls?.length" class="tool-calls-section">
        <button class="toggle-btn" @click="showToolCalls = !showToolCalls">
          <span class="toggle-icon">{{ showToolCalls ? '▾' : '▸' }}</span>
          🔧 调用了 {{ message.tool_calls.length }} 个工具
        </button>
        <div v-if="showToolCalls" class="tool-calls-list">
          <div
            v-for="(tc, i) in message.tool_calls"
            :key="i"
            class="tool-call-item"
          >
            <span class="tool-name">{{ tc.function?.name || 'unknown' }}</span>
            <code class="tool-args">{{ tc.function?.arguments || '' }}</code>
          </div>
        </div>
      </div>

      <!-- 思考内容折叠区 -->
      <div v-if="message.thinking" class="thinking-section">
        <button class="toggle-btn" @click="showThinking = !showThinking">
          <span class="toggle-icon">{{ showThinking ? '▾' : '▸' }}</span>
          💭 深度思考
        </button>
        <div v-if="showThinking" class="thinking-content" v-html="formatContent(message.thinking)" />
      </div>

      <!-- 文本内容 -->
      <div v-if="message.content" class="msg-text" v-html="formatContent(message.content)" />

      <!-- 流式光标 -->
      <span v-if="message.streaming && !message.tool_calls?.length" class="cursor">▊</span>
    </div>

    <!-- Tool 结果消息 -->
    <div v-else-if="message.role === 'tool'" class="bubble tool-bubble">
      <button class="toggle-btn small" @click="showToolOutput = !showToolOutput">
        <span class="toggle-icon">{{ showToolOutput ? '▾' : '▸' }}</span>
        ⚙️ {{ message.tool_name || '工具' }} 返回结果
      </button>
      <pre v-if="showToolOutput" class="tool-output">{{ message.content }}</pre>
    </div>
  </div>
</template>

<script lang="ts">
/** Markdown → HTML（marked 已配置为同步模式） */
function formatContent(text: string): string {
  return marked.parse(text) as string
}
</script>

<style scoped>
.chat-msg {
  display: flex;
  margin-bottom: 12px;
}

.role-user {
  justify-content: flex-end;
}
.role-assistant,
.role-tool {
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

.tool-bubble {
  background: rgba(135, 144, 176, 0.08);
  border: 1px solid var(--line);
  color: var(--ink-3);
  max-width: 70%;
  padding: 8px 12px;
  font-size: 12px;
}

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

.tool-calls-section {
  margin-bottom: 8px;
}

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
.toggle-btn.small {
  font-size: 11.5px;
}

.toggle-icon {
  font-size: 10px;
}

.tool-calls-list {
  margin-top: 6px;
  padding-left: 16px;
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.tool-call-item {
  display: flex;
  align-items: baseline;
  gap: 8px;
}

.tool-name {
  font-family: var(--font-mono);
  font-size: 12px;
  color: var(--accent);
  font-weight: 500;
}

.tool-args {
  font-family: var(--font-mono);
  font-size: 11px;
  color: var(--ink-3);
  max-width: 400px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.tool-output {
  margin: 6px 0 0;
  padding: 6px 8px;
  background: rgba(0, 0, 0, 0.15);
  border-radius: 6px;
  font-family: var(--font-mono);
  font-size: 11.5px;
  white-space: pre-wrap;
  max-height: 200px;
  overflow-y: auto;
}

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

/* Streaming: pulsing teal dot */
.thinking {
  position: relative;
}
.thinking::after {
  content: '';
  position: absolute;
  bottom: -4px;
  left: 20px;
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--accent);
  box-shadow: 0 0 6px rgba(77, 196, 178, 0.5);
  animation: thinking-pulse 1.2s ease-in-out infinite;
}
@keyframes thinking-pulse {
  0%, 100% { opacity: 1; transform: scale(1); }
  50% { opacity: 0.4; transform: scale(0.8); }
}
</style>

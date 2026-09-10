<script setup lang="ts">
import { ref } from 'vue'
import type { ChatMessage } from '@/stores/agents'

const props = defineProps<{
  message: ChatMessage
}>()

const showToolCalls = ref(false)
const showToolOutput = ref(false)
</script>

<template>
  <div class="chat-msg" :class="[`role-${message.role}`, { streaming: message.streaming }]">
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
/** 简单的文本格式化：换行 → <br>，代码块保留 */
function formatContent(text: string): string {
  return text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/```([\s\S]*?)```/g, '<pre class="code-block">$1</pre>')
    .replace(/`([^`]+)`/g, '<code>$1</code>')
    .replace(/\n/g, '<br>')
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
  white-space: pre-wrap;
  font-family: inherit;
}

.msg-text :deep(.code-block) {
  background: rgba(0, 0, 0, 0.2);
  padding: 8px 10px;
  border-radius: 6px;
  font-family: var(--font-mono);
  font-size: 12px;
  overflow-x: auto;
  margin: 6px 0;
}

.msg-text :deep(code) {
  background: rgba(0, 0, 0, 0.15);
  padding: 1px 5px;
  border-radius: 4px;
  font-family: var(--font-mono);
  font-size: 12.5px;
}

.tool-calls-section {
  margin-bottom: 8px;
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

.streaming .assistant-bubble {
  border-color: rgba(77, 196, 178, 0.3);
}
</style>

<script setup lang="ts">
import { ref, onMounted, nextTick, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useAgentsStore } from '@/stores/agents'
import { uploadFile } from '@/api/uploads'
import ChatMessage from '@/components/ChatMessage.vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import type { ConversationListItem } from '@/api/agents'

const route = useRoute()
const router = useRouter()
const store = useAgentsStore()

const agentId = ref(Number(route.params.id))
const inputText = ref('')
const chatContainer = ref<HTMLElement | null>(null)
const fileInput = ref<HTMLInputElement | null>(null)
const pendingFiles = ref<{ id: string; name: string }[]>([])
const uploading = ref(false)

// 加载 Agent 和对话列表
onMounted(async () => {
  await store.selectAgent(agentId.value)
  await store.fetchConversations(agentId.value)
})

// 滚动到底部
function scrollToBottom() {
  nextTick(() => {
    if (chatContainer.value) {
      chatContainer.value.scrollTop = chatContainer.value.scrollHeight
    }
  })
}

// 监听消息变化自动滚动
watch(
  () => store.chatMessages.length,
  () => scrollToBottom(),
)
watch(
  () => store.chatMessages[store.chatMessages.length - 1]?.content,
  () => scrollToBottom(),
)

// 选择对话
async function selectConv(conv: ConversationListItem) {
  store.pendingNewConversation = false
  await store.selectConversation(conv.id)
  scrollToBottom()
}

// 新建对话（仅前端状态，不调后端）
function newConversation() {
  store.newConversation()
}

// 发送消息
async function handleSend() {
  const text = inputText.value.trim()
  if ((!text && !pendingFiles.value.length) || store.streaming) return

  // 如果没有当前对话，标记为待新建（sendMessage 内部会真正创建）
  if (!store.currentConversation && !store.pendingNewConversation) {
    store.newConversation()
  }

  const fileIds = pendingFiles.value.map((f) => f.id)
  const msg = text || '请查看附件'
  inputText.value = ''
  pendingFiles.value = []
  await store.sendMessage(store.currentConversation?.id, msg, fileIds)
  scrollToBottom()
}

// 选择文件 → 上传
async function handleFileSelect(e: Event) {
  const input = e.target as HTMLInputElement
  const files = input.files
  if (!files?.length) return

  uploading.value = true
  try {
    for (const file of Array.from(files)) {
      const result = await uploadFile(file)
      pendingFiles.value.push({ id: result.id, name: result.filename })
    }
  } catch (err: any) {
    ElMessage.error(err.message || '文件上传失败')
  } finally {
    uploading.value = false
    if (fileInput.value) fileInput.value.value = ''
  }
}

function removePendingFile(index: number) {
  pendingFiles.value.splice(index, 1)
}

// 键盘事件
function handleKeydown(e: KeyboardEvent) {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault()
    handleSend()
  }
}

// 删除对话
async function handleDeleteConv(conv: ConversationListItem) {
  try {
    await ElMessageBox.confirm('确定删除这个对话？', '确认', {
      confirmButtonText: '删除',
      cancelButtonText: '取消',
      type: 'warning',
    })
    await store.removeConversation(conv.id)
    ElMessage.success('已删除')
  } catch {
    // 取消
  }
}

// 返回
function goBack() {
  router.push({ name: 'agents' })
}
</script>

<template>
  <div class="agent-chat-page">
    <!-- 左侧：对话列表 -->
    <aside class="conv-sidebar">
      <div class="sidebar-header">
        <button class="back-btn" @click="goBack">← 返回</button>
        <button class="new-btn" @click="newConversation">+ 新对话</button>
      </div>

      <!-- Agent 信息 -->
      <div class="agent-info" v-if="store.selectedAgent">
        <span class="agent-name">{{ store.selectedAgent.name }}</span>
        <span class="agent-desc" v-if="store.selectedAgent.description">{{ store.selectedAgent.description }}</span>
      </div>

      <div class="conv-list">
        <div
          v-for="conv in store.conversations"
          :key="conv.id"
          class="conv-item"
          :class="{ active: !store.pendingNewConversation && store.currentConversation?.id === conv.id }"
          @click="selectConv(conv)"
        >
          <span class="conv-title">{{ conv.title || '新对话' }}</span>
          <button
            class="delete-btn"
            @click.stop="handleDeleteConv(conv)"
            title="删除"
          >×</button>
        </div>
        <div v-if="!store.conversations.length" class="no-conv muted">
          暂无对话
        </div>
      </div>
    </aside>

    <!-- 右侧：聊天区 -->
    <main class="chat-main">
      <!-- 消息区 -->
      <div class="chat-messages" ref="chatContainer">
        <!-- 欢迎状态：无对话 -->
        <div v-if="!store.currentConversation" class="welcome">
          <svg class="welcome-pattern" width="160" height="160" viewBox="0 0 160 160" fill="none" aria-hidden="true">
            <defs>
              <pattern id="agent-dot-grid" x="0" y="0" width="16" height="16" patternUnits="userSpaceOnUse">
                <circle cx="2" cy="2" r="1.2" fill="rgba(77,196,178,0.2)"/>
                <circle cx="10" cy="10" r="0.8" fill="rgba(77,196,178,0.08)"/>
              </pattern>
            </defs>
            <circle cx="80" cy="80" r="78" fill="url(#agent-dot-grid)" stroke="rgba(77,196,178,0.12)" stroke-width="1"/>
            <circle cx="80" cy="80" r="50" fill="url(#agent-dot-grid)" stroke="rgba(77,196,178,0.08)" stroke-width="1"/>
            <circle cx="80" cy="80" r="22" fill="rgba(77,196,178,0.04)" stroke="rgba(77,196,178,0.15)" stroke-width="1"/>
            <circle cx="80" cy="80" r="4" fill="rgba(77,196,178,0.3)"/>
          </svg>
          <div class="welcome-name">{{ store.selectedAgent?.name }}</div>
          <div class="welcome-desc">{{ store.selectedAgent?.description || '输入消息开始对话' }}</div>
          <div class="welcome-actions">
            <button class="welcome-action" @click="inputText = '你好，请介绍一下你能做什么'">了解能力</button>
            <button class="welcome-action" @click="inputText = '请帮我分析一下当前项目'">快速开始</button>
          </div>
        </div>

        <template v-else>
          <ChatMessage
            v-for="(msg, i) in store.chatMessages"
            :key="i"
            :message="msg"
          />
        </template>
      </div>

      <!-- 输入框 -->
      <div class="chat-input-area">
        <div v-if="pendingFiles.length" class="pending-files">
          <div v-for="(f, i) in pendingFiles" :key="i" class="pending-file">
            <span class="file-name">{{ f.name }}</span>
            <button class="remove-file" @click="removePendingFile(i)" :disabled="store.streaming">×</button>
          </div>
        </div>

        <div class="input-bar">
          <input
            ref="fileInput"
            type="file"
            multiple
            accept=".txt,.md,.markdown,.pdf"
            style="display: none"
            @change="handleFileSelect"
          />
          <button
            class="icon-btn attach-btn"
            @click="fileInput?.click()"
            :disabled="store.streaming || uploading"
            title="上传文件 (.txt/.md/.pdf)"
          >
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <path d="m21.44 11.05-9.19 9.19a6 6 0 0 1-8.49-8.49l8.57-8.57A4 4 0 1 1 18 8.84l-8.59 8.57a2 2 0 0 1-2.83-2.83l8.49-8.48"/>
            </svg>
          </button>
          <textarea
            v-model="inputText"
            placeholder="输入消息…"
            rows="1"
            @keydown="handleKeydown"
            :disabled="store.streaming"
          />
          <button
            class="icon-btn send-btn"
            :disabled="(!inputText.trim() && !pendingFiles.length) || store.streaming"
            @click="handleSend"
            title="发送"
          >
            <svg v-if="!store.streaming" width="16" height="16" viewBox="0 0 24 24" fill="currentColor">
              <path d="M3.478 2.405a.75.75 0 00-.926.94l2.432 7.905H13.5a.75.75 0 010 1.5H4.984l-2.432 7.905a.75.75 0 00.926.94 60.519 60.519 0 0018.445-8.986.75.75 0 000-1.218A60.517 60.517 0 003.478 2.405z"/>
            </svg>
            <span v-else class="stop-icon"></span>
          </button>
        </div>
      </div>
    </main>
  </div>
</template>

<style scoped>
/* ================================================================
   Layout
   ================================================================ */
.agent-chat-page {
  display: flex;
  height: calc(100vh - 52px);
  overflow: hidden;
}

/* ================================================================
   Sidebar
   ================================================================ */
.conv-sidebar {
  width: 260px;
  flex: none;
  display: flex;
  flex-direction: column;
  background: var(--panel);
  border-right: 1px solid var(--line);
}

.sidebar-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 14px 16px;
  border-bottom: 1px solid var(--line);
}

.back-btn {
  background: none;
  border: none;
  color: var(--ink-3);
  cursor: pointer;
  font-size: 13px;
  padding: 0;
  transition: color 0.15s;
}
.back-btn:hover {
  color: var(--ink);
}

.new-btn {
  background: none;
  border: none;
  color: var(--accent);
  cursor: pointer;
  font-size: 13px;
  font-weight: 500;
  padding: 0;
  transition: opacity 0.15s;
}
.new-btn:hover {
  opacity: 0.75;
}

.agent-info {
  padding: 14px 16px 10px;
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.agent-name {
  font-size: 13px;
  font-weight: 600;
  color: var(--ink);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.agent-desc {
  font-size: 11.5px;
  color: var(--ink-3);
  line-height: 1.4;
  display: -webkit-box;
  -webkit-line-clamp: 1;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.conv-list {
  flex: 1;
  overflow-y: auto;
  padding: 8px;
}

.conv-item {
  position: relative;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 14px 16px 14px 20px;
  border-radius: 6px;
  cursor: pointer;
  transition: background 0.15s;
}
.conv-item:hover {
  background: rgba(77, 196, 178, 0.04);
}
.conv-item.active {
  background: rgba(77, 196, 178, 0.07);
}
.conv-item.active::before {
  content: '';
  position: absolute;
  left: 8px;
  top: 50%;
  transform: translateY(-50%);
  width: 3px;
  height: 18px;
  border-radius: 2px;
  background: var(--accent);
}

.conv-title {
  font-size: 13px;
  color: var(--ink-3);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  flex: 1;
  transition: color 0.15s;
}
.conv-item:hover .conv-title,
.conv-item.active .conv-title {
  color: var(--ink-2);
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
.conv-item:hover .delete-btn {
  opacity: 1;
}
.delete-btn:hover {
  color: var(--danger);
}

.no-conv {
  text-align: center;
  padding: 40px 16px;
  font-size: 13px;
}

/* ================================================================
   Chat main
   ================================================================ */
.chat-main {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-width: 0;
}

/* ---- Messages ---- */
.chat-messages {
  flex: 1;
  overflow-y: auto;
  padding: 24px;
  display: flex;
  flex-direction: column;
}

/* ---- Welcome ---- */
.welcome {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 16px;
}
.welcome-pattern {
  opacity: 0.8;
}
.welcome-name {
  font-family: var(--font-mono);
  font-size: 22px;
  font-weight: 600;
  color: var(--ink);
  letter-spacing: -0.01em;
}
.welcome-desc {
  font-size: 14px;
  color: var(--ink-3);
  max-width: 320px;
  text-align: center;
  line-height: 1.5;
}
.welcome-actions {
  display: flex;
  gap: 8px;
  margin-top: 4px;
}
.welcome-action {
  background: rgba(77, 196, 178, 0.06);
  border: 1px solid var(--line);
  border-radius: 100px;
  padding: 8px 20px;
  font-size: 13px;
  color: var(--ink-2);
  cursor: pointer;
  transition: border-color 0.2s, background 0.2s;
}
.welcome-action:hover {
  border-color: rgba(77, 196, 178, 0.25);
  background: rgba(77, 196, 178, 0.1);
  color: var(--ink);
}

/* ================================================================
   Input
   ================================================================ */
.chat-input-area {
  padding: 12px 24px 20px;
  background: var(--panel);
}

.input-bar {
  display: flex;
  align-items: center;
  gap: 4px;
  max-width: 800px;
  margin: 0 auto;
  border: 1px solid var(--line-strong);
  border-radius: 12px;
  background: var(--panel-2);
  padding: 6px 8px;
  transition: border-color 0.2s;
}
.input-bar:focus-within {
  border-color: rgba(77, 196, 178, 0.4);
}

.input-bar textarea {
  flex: 1;
  resize: none;
  border: none;
  background: transparent;
  padding: 6px 4px;
  font: inherit;
  font-size: 14px;
  color: var(--ink);
  line-height: 1.5;
  min-height: 24px;
  max-height: 120px;
}
.input-bar textarea:focus {
  outline: none;
}
.input-bar textarea::placeholder {
  color: var(--ink-3);
}

.icon-btn {
  width: 32px;
  height: 32px;
  flex-shrink: 0;
  border-radius: 8px;
  border: none;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: background 0.15s, color 0.15s;
}

.attach-btn {
  background: transparent;
  color: var(--ink-3);
}
.attach-btn:hover:not(:disabled) {
  background: rgba(77, 196, 178, 0.08);
  color: var(--ink-2);
}
.attach-btn:disabled {
  opacity: 0.3;
  cursor: not-allowed;
}

.send-btn {
  background: var(--accent);
  color: #062a24;
}
.send-btn:disabled {
  background: var(--line-strong);
  color: var(--ink-3);
  cursor: not-allowed;
}
.send-btn:not(:disabled):hover {
  opacity: 0.85;
}

.stop-icon {
  width: 10px;
  height: 10px;
  border-radius: 2px;
  background: #062a24;
}

/* ---- Pending files ---- */
.pending-files {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-bottom: 8px;
  max-width: 800px;
  margin-left: auto;
  margin-right: auto;
}
.pending-file {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 4px 10px;
  background: rgba(77, 196, 178, 0.06);
  border: 1px solid var(--line);
  border-radius: 6px;
  font-size: 12px;
}
.file-name {
  color: var(--ink-2);
  max-width: 200px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.remove-file {
  background: none;
  border: none;
  color: var(--ink-3);
  cursor: pointer;
  font-size: 13px;
  padding: 0;
  line-height: 1;
}
.remove-file:hover {
  color: var(--danger);
}

/* ================================================================
   Responsive
   ================================================================ */
@media (max-width: 768px) {
  .conv-sidebar {
    width: 200px;
  }
  .welcome-name {
    font-size: 18px;
  }
}
</style>

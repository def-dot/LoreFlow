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
  await store.selectConversation(conv.id)
  scrollToBottom()
}

// 新建对话
async function newConversation() {
  if (!store.selectedAgent) return
  const conv = await store.createConversation(store.selectedAgent.id)
  await store.fetchConversations(agentId.value)
  await store.selectConversation(conv.id)
}

// 发送消息
async function handleSend() {
  const text = inputText.value.trim()
  if ((!text && !pendingFiles.value.length) || store.streaming) return

  // 如果没有当前对话，先创建
  if (!store.currentConversation) {
    await newConversation()
  }
  if (!store.currentConversation) return

  const fileIds = pendingFiles.value.map((f) => f.id)
  const msg = text || '请查看附件'
  inputText.value = ''
  pendingFiles.value = []
  await store.sendMessage(store.currentConversation.id, msg, fileIds)
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
        <el-button size="small" type="primary" @click="newConversation">
          + 新对话
        </el-button>
      </div>

      <div class="agent-info" v-if="store.selectedAgent">
        <div class="agent-name">{{ store.selectedAgent.name }}</div>
        <div class="agent-desc muted">{{ store.selectedAgent.description || '暂无描述' }}</div>
      </div>

      <div class="conv-list">
        <div
          v-for="conv in store.conversations"
          :key="conv.id"
          class="conv-item"
          :class="{ active: store.currentConversation?.id === conv.id }"
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
      <!-- 无对话状态 -->
      <div v-if="!store.currentConversation" class="chat-empty">
        <div class="empty-icon">💬</div>
        <p>选择一个对话或创建新对话开始聊天</p>
      </div>

      <!-- 对话界面 -->
      <template v-else>
        <div class="chat-messages" ref="chatContainer">
          <ChatMessage
            v-for="(msg, i) in store.chatMessages"
            :key="i"
            :message="msg"
          />
          <div v-if="!store.chatMessages.length" class="chat-welcome muted">
            发送一条消息开始对话
          </div>
        </div>

        <div class="chat-input-area">
          <!-- 待发送文件列表 -->
          <div v-if="pendingFiles.length" class="pending-files">
            <div v-for="(f, i) in pendingFiles" :key="i" class="pending-file">
              <span class="file-name">📎 {{ f.name }}</span>
              <button class="remove-file" @click="removePendingFile(i)" :disabled="store.streaming">×</button>
            </div>
          </div>

          <div class="input-box">
            <input
              ref="fileInput"
              type="file"
              multiple
              accept=".txt,.md,.markdown,.pdf"
              style="display: none"
              @change="handleFileSelect"
            />
            <textarea
              v-model="inputText"
              placeholder="输入消息..."
              rows="1"
              @keydown="handleKeydown"
              :disabled="store.streaming"
            />
            <div class="input-actions">
              <button
                class="action-btn attach-btn"
                @click="fileInput?.click()"
                :disabled="store.streaming || uploading"
                title="上传文件 (.txt/.md/.pdf)"
              >
                <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                  <path d="m21.44 11.05-9.19 9.19a6 6 0 0 1-8.49-8.49l8.57-8.57A4 4 0 1 1 18 8.84l-8.59 8.57a2 2 0 0 1-2.83-2.83l8.49-8.48"/>
                </svg>
              </button>
              <button
                class="action-btn send-btn"
                :disabled="(!inputText.trim() && !pendingFiles.length) || store.streaming"
                @click="handleSend"
                title="发送"
              >
                <svg v-if="!store.streaming" width="18" height="18" viewBox="0 0 24 24" fill="currentColor">
                  <path d="M3.478 2.405a.75.75 0 00-.926.94l2.432 7.905H13.5a.75.75 0 010 1.5H4.984l-2.432 7.905a.75.75 0 00.926.94 60.519 60.519 0 0018.445-8.986.75.75 0 000-1.218A60.517 60.517 0 003.478 2.405z"/>
                </svg>
                <span v-else class="stop-dot"></span>
              </button>
            </div>
          </div>
        </div>
      </template>
    </main>
  </div>
</template>

<style scoped>
.agent-chat-page {
  display: flex;
  height: calc(100vh - 52px);
  overflow: hidden;
}

/* ---- 左侧对话列表 ---- */
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
  padding: 12px 14px;
  border-bottom: 1px solid var(--line);
}

.back-btn {
  background: none;
  border: none;
  color: var(--ink-2);
  cursor: pointer;
  font-size: 13px;
  padding: 0;
}
.back-btn:hover {
  color: var(--ink);
}

.agent-info {
  padding: 14px;
  border-bottom: 1px solid var(--line);
}
.agent-info .agent-name {
  font-size: 15px;
  font-weight: 600;
  color: var(--ink);
  margin-bottom: 4px;
}
.agent-info .agent-desc {
  font-size: 12px;
  line-height: 1.5;
}

.conv-list {
  flex: 1;
  overflow-y: auto;
  padding: 8px;
}

.conv-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 10px 12px;
  border-radius: 8px;
  cursor: pointer;
  transition: background 0.15s;
}
.conv-item:hover {
  background: rgba(77, 196, 178, 0.05);
}
.conv-item.active {
  background: rgba(77, 196, 178, 0.1);
  border: 1px solid rgba(77, 196, 178, 0.2);
}

.conv-title {
  font-size: 13px;
  color: var(--ink-2);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  flex: 1;
}
.conv-item.active .conv-title {
  color: var(--ink);
}

.delete-btn {
  background: none;
  border: none;
  color: var(--ink-3);
  cursor: pointer;
  font-size: 16px;
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
  padding: 30px 10px;
  font-size: 13px;
}

/* ---- 右侧聊天区 ---- */
.chat-main {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-width: 0;
}

.chat-empty {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 12px;
  color: var(--ink-3);
}
.empty-icon {
  font-size: 48px;
  opacity: 0.5;
}

.chat-messages {
  flex: 1;
  overflow-y: auto;
  padding: 20px 24px;
}

.chat-welcome {
  text-align: center;
  padding: 40px;
}

.chat-input-area {
  padding: 12px 20px 16px;
  border-top: 1px solid var(--line);
  background: var(--panel);
}

.input-box {
  max-width: 800px;
  margin: 0 auto;
  border: 1px solid var(--line-strong);
  border-radius: 16px;
  background: var(--panel-2);
  padding: 10px 12px 8px;
  transition: border-color 0.2s;
}
.input-box:focus-within {
  border-color: var(--accent);
}

.input-box textarea {
  width: 100%;
  resize: none;
  border: none;
  background: transparent;
  padding: 2px 4px;
  font: inherit;
  font-size: 13.5px;
  color: var(--ink);
  line-height: 1.5;
  min-height: 24px;
  max-height: 120px;
}
.input-box textarea:focus {
  outline: none;
}
.input-box textarea::placeholder {
  color: var(--ink-3);
}

.input-actions {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-top: 4px;
}

.action-btn {
  width: 32px;
  height: 32px;
  border-radius: 50%;
  border: none;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: background 0.15s, opacity 0.15s;
  flex-shrink: 0;
}

.attach-btn {
  background: transparent;
  color: var(--ink-3);
}
.attach-btn:hover:not(:disabled) {
  background: rgba(77, 196, 178, 0.1);
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

.stop-dot {
  width: 10px;
  height: 10px;
  border-radius: 2px;
  background: #062a24;
}

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
  background: rgba(77, 196, 178, 0.08);
  border: 1px solid rgba(77, 196, 178, 0.2);
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
  font-size: 14px;
  padding: 0;
  line-height: 1;
}
.remove-file:hover {
  color: var(--danger);
}

@media (max-width: 768px) {
  .conv-sidebar {
    width: 200px;
  }
}
</style>

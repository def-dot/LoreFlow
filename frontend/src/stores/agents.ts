import { defineStore } from 'pinia'
import {
  listAgents,
  getAgent,
  createAgent,
  updateAgent,
  deleteAgent,
  listConversations,
  getConversation,
  createConversation,
  deleteConversation,
  sendChatMessage,
  type AgentListItem,
  type ConversationListItem,
  type ConversationDetail,
  type MessageItem,
  type ChatEvent,
} from '@/api/agents'

// ---------------------------------------------------------------------------
// Chat message with streaming state
// ---------------------------------------------------------------------------

export interface ChatMessage {
  role: 'user' | 'assistant' | 'tool'
  content: string
  tool_calls?: any[] | null
  tool_call_id?: string | null
  tool_name?: string | null
  /** 正在流式接收中 */
  streaming?: boolean
}

// ---------------------------------------------------------------------------
// Store
// ---------------------------------------------------------------------------

export const useAgentsStore = defineStore('agents', {
  state: () => ({
    // Agent 列表
    agents: [] as AgentListItem[],
    selectedAgent: null as AgentListItem | null,

    // 对话列表
    conversations: [] as ConversationListItem[],
    currentConversation: null as ConversationDetail | null,

    // 当前聊天消息（含流式状态）
    chatMessages: [] as ChatMessage[],

    // UI 状态
    loading: false,
    chatLoading: false,
    streaming: false,
    /** 点击"新对话"后尚未提交首条消息 */
    pendingNewConversation: false,
  }),

  getters: {
    agentById: (state) => (id: number) => state.agents.find((a) => a.id === id),
  },

  actions: {
    // ----- Agent CRUD -----

    async fetchAgents() {
      this.agents = await listAgents()
    },

    async selectAgent(id: number) {
      this.selectedAgent = await getAgent(id)
    },

    async createAgent(data: AgentListItem) {
      const agent = await createAgent(data)
      await this.fetchAgents()
      return agent
    },

    async updateAgent(id: number, data: Partial<AgentListItem>) {
      const agent = await updateAgent(id, data)
      if (this.selectedAgent?.id === id) {
        this.selectedAgent = agent
      }
      await this.fetchAgents()
      return agent
    },

    async removeAgent(id: number) {
      await deleteAgent(id)
      if (this.selectedAgent?.id === id) {
        this.selectedAgent = null
      }
      await this.fetchAgents()
    },

    // ----- Conversations -----

    async fetchConversations(agentId?: number) {
      this.conversations = await listConversations(agentId)
    },

    async selectConversation(id: number) {
      this.currentConversation = await getConversation(id)
      // 转换为 ChatMessage
      this.chatMessages = (this.currentConversation.messages || []).map((m) => {
        const msg: any = { role: m.role, content: m.content, tool_calls: m.tool_calls, tool_call_id: m.tool_call_id }
        if (m.role === 'tool') {
          const match = m.content.match(/^\[(.+?)]\s/)
          if (match) msg.tool_name = match[1]
        }
        return msg
      })
    },

    /** 仅进入"新对话"前端状态，不调后端 */
    newConversation() {
      this.currentConversation = null
      this.chatMessages = []
      this.pendingNewConversation = true
    },

    /** 实际创建对话（内部用，由 sendMessage 在提交首条消息时调） */
    async createConversation(agentId: number, title?: string) {
      const conv = await createConversation({ agent_id: agentId, title })
      await this.fetchConversations(agentId)
      return conv
    },

    async removeConversation(id: number) {
      const agentId = this.selectedAgent?.id
      await deleteConversation(id)
      if (this.currentConversation?.id === id) {
        this.currentConversation = null
        this.chatMessages = []
      }
      if (agentId) await this.fetchConversations(agentId)
    },

    // ----- Chat -----

    async sendMessage(conversationId: number | undefined, message: string, fileIds: string[] = []) {
      // 若处于"新对话待提交"状态，先在后端真正创建对话
      if (this.pendingNewConversation && this.selectedAgent) {
        const title = message.length > 30 ? message.slice(0, 30) + '…' : message
        const conv = await this.createConversation(this.selectedAgent.id, title)
        this.currentConversation = conv
        this.pendingNewConversation = false
        conversationId = conv.id
      }

      // 添加用户消息（含文件标记）
      const displayMsg = fileIds.length
        ? `[📎 ${fileIds.length} 个附件]\n${message}`
        : message
      this.chatMessages.push({ role: 'user', content: displayMsg })

      // 当前活跃的 assistant 消息引用（工具调用后会切换）
      let activeMsg: ChatMessage = { role: 'assistant', content: '', streaming: true }
      this.chatMessages.push(activeMsg)
      this.streaming = true

      try {
        for await (const evt of sendChatMessage(conversationId, message, fileIds)) {
          switch (evt.event) {
            case 'token':
              activeMsg.content += evt.data.content
              break
            case 'tool_start':
              activeMsg.tool_calls = activeMsg.tool_calls || []
              activeMsg.tool_calls.push({
                function: {
                  name: evt.data.tool_name,
                  arguments: evt.data.arguments,
                },
              })
              break
            case 'tool_end':
              // 工具结果消息
              this.chatMessages.push({
                role: 'tool',
                content: evt.data.output,
                tool_name: evt.data.tool_name,
              })
              // 结束当前 assistant 消息的流式状态
              activeMsg.streaming = false
              // 创建新的 assistant 占位（下一轮 LLM 回复）
              activeMsg = { role: 'assistant', content: '', streaming: true }
              this.chatMessages.push(activeMsg)
              break
            case 'done':
              activeMsg.content = evt.data.content || activeMsg.content
              activeMsg.streaming = false
              break
            case 'error':
              activeMsg.content = `⚠️ ${evt.data.message}`
              activeMsg.streaming = false
              break
          }
        }
      } catch (err: any) {
        activeMsg.content = `⚠️ ${err.message || '请求失败'}`
        activeMsg.streaming = false
      } finally {
        // 兜底：关闭所有未结束的 streaming
        for (const msg of this.chatMessages) {
          if (msg.streaming) msg.streaming = false
        }
        this.streaming = false
      }

      // 刷新对话列表（标题可能变了）
      const conv = this.currentConversation
      if (conv) {
        await this.fetchConversations(conv.agent_id)
      }
    },
  },
})

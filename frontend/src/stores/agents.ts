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

/** 单个工具调用步骤 */
export interface ToolStep {
  tool_name: string
  arguments: string
  output?: string
  duration_ms?: number
  status: 'running' | 'success' | 'error'
}

export interface ChatMessage {
  role: 'user' | 'assistant'
  content: string
  thinking?: string
  /** 工具调用步骤（替代原 tool_calls + 独立 tool 消息） */
  steps?: ToolStep[]
  /** 执行摘要（done 事件携带） */
  summary?: {
    total_rounds: number
    total_tool_calls: number
    total_duration_ms: number
  }
  /** 当前流式阶段 */
  phase?: 'thinking' | 'tool' | 'token'
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
      const raw = this.currentConversation.messages || []
      const result: ChatMessage[] = []

      for (const m of raw) {
        if (m.role === 'user') {
          result.push({ role: 'user', content: m.content })
        } else if (m.role === 'assistant') {
          const msg: ChatMessage = { role: 'assistant', content: m.content }
          // 从 tool_calls 重建 steps
          if (m.tool_calls?.length) {
            msg.steps = m.tool_calls.map((tc: any) => ({
              tool_name: tc.function?.name || 'unknown',
              arguments: tc.function?.arguments || '',
              status: 'success' as const,
            }))
          }
          result.push(msg)
        }
        // tool 消息不再单独显示，其内容已通过 SSE 流式阶段的 tool_end 填入 steps
        // 但历史数据中 tool 输出存在独立的 tool 消息里，需要回填到前一个 assistant 的最后一个 step
        if (m.role === 'tool') {
          const match = m.content.match(/^\[(.+?)]\s([\s\S]*)$/)
          const toolName = match?.[1] || 'unknown'
          const output = match?.[2] || m.content
          // 找前一个 assistant 消息中最后一个匹配的 step
          for (let i = result.length - 1; i >= 0; i--) {
            if (result[i].role === 'assistant') {
              const steps = result[i].steps
              if (steps?.length) {
                const lastStep = steps[steps.length - 1]
                if (lastStep.tool_name === toolName && !lastStep.output) {
                  lastStep.output = output
                }
              }
              break
            }
          }
        }
      }
      this.chatMessages = result
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

      // 当前活跃的 assistant 消息在数组中的索引
      // 必须通过 this.chatMessages[idx] 访问（reactive Proxy），
      // 不能持有 push 前的原始对象引用，否则 Vue 追踪不到变更。
      this.chatMessages.push({ role: 'assistant', content: '', streaming: true })
      let activeIdx = this.chatMessages.length - 1
      this.streaming = true

      /** 获取当前活跃 assistant 消息（始终走 reactive Proxy） */
      const getActive = (): ChatMessage => this.chatMessages[activeIdx]

      try {
        for await (const evt of sendChatMessage(conversationId, message, fileIds)) {
          switch (evt.event) {
            case 'thinking':
              if (!getActive().thinking) getActive().thinking = ''
              getActive().thinking += evt.data.content
              getActive().phase = 'thinking'
              break
            case 'token':
              getActive().content += evt.data.content
              getActive().phase = 'token'
              break
            case 'tool_start': {
              const msg = getActive()
              msg.steps = msg.steps || []
              msg.steps.push({
                tool_name: evt.data.tool_name,
                arguments: evt.data.arguments,
                status: 'running',
              })
              msg.phase = 'tool'
              break
            }
            case 'tool_end': {
              // 找当前 assistant 消息中最后一个 running 的 step
              const msg = getActive()
              const steps = msg.steps || []
              const step = [...steps].reverse().find((s) => s.status === 'running')
              if (step) {
                step.output = evt.data.output
                step.duration_ms = evt.data.duration_ms
                step.status = 'success'
              }
              // 结束当前 assistant 消息，创建新的 assistant 占位（下一轮 LLM）
              msg.streaming = false
              this.chatMessages.push({ role: 'assistant', content: '', streaming: true })
              activeIdx = this.chatMessages.length - 1
              break
            }
            case 'done': {
              const msg = getActive()
              msg.content = evt.data.content || msg.content
              msg.summary = {
                total_rounds: evt.data.total_rounds,
                total_tool_calls: evt.data.total_tool_calls,
                total_duration_ms: evt.data.total_duration_ms,
              }
              msg.streaming = false
              break
            }
            case 'error':
              getActive().content = `⚠️ ${evt.data.message}`
              getActive().streaming = false
              break
          }
        }
      } catch (err: any) {
        getActive().content = `⚠️ ${err.message || '请求失败'}`
        getActive().streaming = false
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

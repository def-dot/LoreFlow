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

/** 一轮 agentic 循环的数据 */
export interface RoundData {
  thinking?: string
  content?: string
  steps?: ToolStep[]
}

export interface ChatMessage {
  role: 'user' | 'assistant'
  content: string
  /** 按轮分组的过程数据（思考 + 工具调用） */
  rounds?: RoundData[]
  /** 执行摘要（done 事件携带） */
  summary?: {
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

      // 按 user 消息分组为 turns
      const turns: MessageItem[][] = []
      let currentTurn: MessageItem[] = []
      for (const m of raw) {
        if (m.role === 'user') {
          if (currentTurn.length) turns.push(currentTurn)
          currentTurn = [m]
        } else {
          currentTurn.push(m)
        }
      }
      if (currentTurn.length) turns.push(currentTurn)

      const result: ChatMessage[] = []
      for (const turn of turns) {
        const userMsg = turn.find(m => m.role === 'user')
        const assistantMsgs = turn.filter(m => m.role === 'assistant')
        const toolMsgs = turn.filter(m => m.role === 'tool')

        if (userMsg) result.push({ role: 'user', content: userMsg.content })

        // 把每个 assistant 消息和它对应的 tool 消息配对为 rounds
        const rounds: RoundData[] = []
        for (const a of assistantMsgs) {
          const round: RoundData = {}
          if (a.reasoning_content) round.thinking = a.reasoning_content
          if (a.tool_calls?.length) {
            round.steps = a.tool_calls.map((tc: any) => {
              const toolMsg = toolMsgs.find(t => t.tool_call_id === tc.id)
              return {
                tool_name: tc.function?.name || 'unknown',
                arguments: tc.function?.arguments || '',
                output: toolMsg?.content || '',
                status: 'success' as const,
              } as ToolStep
            })
          } else {
            round.content = a.content
          }
          rounds.push(round)
        }

        const finalAssistant = assistantMsgs.find(a => !a.tool_calls?.length)
        const hasProcess = rounds.some(r => r.thinking || r.steps?.length)
        result.push({
          role: 'assistant',
          content: finalAssistant?.content || '',
          rounds: hasProcess ? rounds : undefined,
        })
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
        // 追踪当前轮次索引（thinking 到达时若当前轮已有 steps 则开新轮）
        let roundIdx = 0
        // 各轮累积的 content（done 时归位最后一轮）
        let roundContentBuf = ''
        const ensureRound = () => {
          const msg = getActive()
          msg.rounds = msg.rounds || [{ thinking: '' }]
          return msg
        }
        const currentRound = () => getActive().rounds![roundIdx]

        for await (const evt of sendChatMessage(conversationId, message, fileIds)) {
          switch (evt.event) {
            case 'thinking': {
              const msg = ensureRound()
              // 当前轮已有 steps → 新一轮开始，把已累积 content 归入前一轮
              if (msg.rounds![roundIdx].steps?.length) {
                msg.rounds![roundIdx].content = roundContentBuf || undefined
                roundContentBuf = ''
                roundIdx++
                msg.rounds!.push({ thinking: '' })
              }
              const round = msg.rounds![roundIdx]
              round.thinking = (round.thinking || '') + evt.data.content
              msg.phase = 'thinking'
              break
            }
            case 'token':
              ensureRound()
              getActive().content += evt.data.content
              roundContentBuf += evt.data.content
              getActive().phase = 'token'
              break
            case 'tool_start': {
              const round = currentRound()
              round.steps = round.steps || []
              round.steps.push({
                tool_name: evt.data.tool_name,
                arguments: evt.data.arguments,
                status: 'running',
              })
              getActive().phase = 'tool'
              break
            }
            case 'tool_end': {
              const round = currentRound()
              const steps = round.steps || []
              const step = [...steps].reverse().find((s) => s.status === 'running')
              if (step) {
                step.output = evt.data.output
                step.duration_ms = evt.data.duration_ms
                step.status = evt.data.status || 'success'
              }
              break
            }
            case 'done': {
              const msg = getActive()
              // 最终 content 归入最后一轮
              const lastRound = msg.rounds?.[msg.rounds.length - 1]
              if (lastRound && !lastRound.content) {
                lastRound.content = evt.data.content || roundContentBuf || undefined
              }
              msg.content = evt.data.content || msg.content
              msg.summary = {
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

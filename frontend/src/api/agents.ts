import { api } from './request'

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface AgentListItem {
  id: number
  name: string
  description: string
  system_prompt: string
  model: string
  tools: string[]
  skills: string[]
  created_at: string | null
  updated_at: string | null
}

export interface ConversationListItem {
  id: number
  agent_id: number
  title: string
  created_at: string | null
  updated_at: string | null
}

export interface ConversationCreate {
  agent_id: number
  title?: string
}

export interface MessageItem {
  id: number
  role: 'user' | 'assistant' | 'tool'
  content: string
  tool_calls?: any[] | null
  tool_call_id?: string | null
  created_at: string | null
}

export interface ConversationDetail extends ConversationListItem {
  messages: MessageItem[]
}

export interface ChatRequest {
  message: string
}

// ---------------------------------------------------------------------------
// Agent CRUD
// ---------------------------------------------------------------------------

export const listAgents = () => api.get<AgentListItem[]>('/agents')

export const getAgent = (id: number) => api.get<AgentListItem>(`/agents/${id}`)

export const createAgent = (data: AgentListItem) =>
  api.post<AgentListItem>('/agents', data)

export const updateAgent = (id: number, data: Partial<AgentListItem>) =>
  api.put<AgentListItem>(`/agents/${id}`, data)

export const deleteAgent = (id: number) => api.delete(`/agents/${id}`)

// ---------------------------------------------------------------------------
// Conversation CRUD
// ---------------------------------------------------------------------------

export const listConversations = (agentId?: number) => {
  const params = agentId != null ? { agent_id: agentId } : undefined
  return api.get<ConversationListItem[]>('/conversations', { params })
}

export const getConversation = (id: number) =>
  api.get<ConversationDetail>(`/conversations/${id}`)

export const createConversation = (data: ConversationCreate) =>
  api.post<ConversationListItem>('/conversations', data)

export const deleteConversation = (id: number) =>
  api.delete(`/conversations/${id}`)

// ---------------------------------------------------------------------------
// Chat (SSE)
// ---------------------------------------------------------------------------

export interface ChatEvent {
  event: 'token' | 'tool_start' | 'tool_end' | 'done' | 'error'
  data: Record<string, any>
}

/**
 * 发送消息并返回 SSE 事件流。
 * 调用方用 for await 逐个消费事件。
 */
export async function* sendChatMessage(
  conversationId: number,
  message: string,
  fileIds: string[] = [],
): AsyncGenerator<ChatEvent> {
  const resp = await fetch(`/api/v1/conversations/${conversationId}/chat`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ message, file_ids: fileIds }),
  })

  if (!resp.ok) {
    const text = await resp.text()
    throw new Error(`Chat request failed: ${resp.status} ${text}`)
  }

  const reader = resp.body!.getReader()
  const decoder = new TextDecoder()
  let buffer = ''

  while (true) {
    const { done, value } = await reader.read()
    if (done) break

    buffer += decoder.decode(value, { stream: true })
    const lines = buffer.split('\n')
    buffer = lines.pop()! // 保留不完整的行

    let currentEvent = ''
    for (const line of lines) {
      if (line.startsWith('event: ')) {
        currentEvent = line.slice(7).trim()
      } else if (line.startsWith('data: ')) {
        const dataStr = line.slice(6)
        try {
          const data = JSON.parse(dataStr)
          yield { event: currentEvent as ChatEvent['event'], data }
        } catch {
          // 忽略解析错误
        }
      }
    }
  }

  // 处理缓冲区剩余
  if (buffer.trim()) {
    const lines = buffer.split('\n')
    let currentEvent = ''
    for (const line of lines) {
      if (line.startsWith('event: ')) {
        currentEvent = line.slice(7).trim()
      } else if (line.startsWith('data: ')) {
        try {
          const data = JSON.parse(line.slice(6))
          yield { event: currentEvent as ChatEvent['event'], data }
        } catch {
          // ignore
        }
      }
    }
  }
}
